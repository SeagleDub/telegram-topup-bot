"""
Сервис для работы с API luboydomen.info

Добавлена обработка rate limit: уважение правила "1 request per second for identical requests",
обработка 429 с чтением поля wait_time и заголовка Retry-After, а также экспоненциальные ретраи.
Также введена сериализация запросов с помощью asyncio.Lock чтобы избежать гонок, когда
несколько корутин одновременно начинают одинаковый запрос.
"""
import asyncio
import time
import json
import aiohttp
from config import LUBOYDOMEN_API_TOKEN

LUBOYDOMEN_API_BASE = "https://luboydomen.info/api/ggl"

# Простое хранилище времени последних идентичных запросов (monotonic seconds)
_last_request_timestamps: dict = {}
# Хранилище времени последних запросов по endpoint (без учета params/body)
_last_endpoint_timestamps: dict = {}

# Локы для сериализации: по идентичным запросам и по endpoint
_identical_locks: dict = {}
_endpoint_locks: dict = {}

# Параметры retry/ backoff
_MAX_RETRIES = 6
_BASE_BACKOFF = 1.0  # seconds
_MAX_BACKOFF = 60.0  # seconds

# Rate limit derived safe intervals (в секундах)
# Per IP: 10 req/min => 6.0s per request
# Per token: 20 req/min => 3.0s per request
# Чтобы быть максимально безопасными, используем max => 6.0
_ENDPOINT_MIN_INTERVAL = 6.0
_IDENTICAL_MIN_INTERVAL = 1.0


def _get_lock(d: dict, key: str) -> asyncio.Lock:
    """Получить или создать asyncio.Lock для ключа"""
    lock = d.get(key)
    if lock is None:
        lock = asyncio.Lock()
        d[key] = lock
    return lock


async def _fetch_json_with_rate_handling(session: aiohttp.ClientSession, method: str, url: str, *, headers=None, params=None, json_body=None) -> dict:
    """Выполняет HTTP-запрос и обрабатывает 429/RateLimit и правило 1 req/sec для идентичных запросов.

    Возвращает распарсенный JSON (если возможен) или словарь с ошибкой в формате, совместимом с текущим кодом.
    """
    # Формируем ключ для идентификации «идентичных» запросов
    try:
        params_key = json.dumps(params, sort_keys=True, ensure_ascii=False) if params else ""
    except Exception:
        params_key = str(params)

    try:
        body_key = json.dumps(json_body, sort_keys=True, ensure_ascii=False) if json_body else ""
    except Exception:
        body_key = str(json_body)

    identical_key = f"{method.upper()}:{url}:{params_key}:{body_key}"
    # endpoint_key не учитывает params/body — нужен для подсчёта общего лимита по endpoint
    endpoint_key = f"{method.upper()}:{url}"

    # Получаем/создаём локи (endpoint lock всегда берём первым чтобы избежать дедлока)
    endpoint_lock = _get_lock(_endpoint_locks, endpoint_key)
    identical_lock = _get_lock(_identical_locks, identical_key)

    retries = 0
    backoff = _BASE_BACKOFF

    while True:
        # Сериализуем обращения к endpoint и к идентичному запросу
        async with endpoint_lock:
            async with identical_lock:
                # Enforce minimal interval between any requests to this endpoint
                now = time.monotonic()
                last_ep = _last_endpoint_timestamps.get(endpoint_key)
                if last_ep is not None:
                    elapsed_ep = now - last_ep
                    if elapsed_ep < _ENDPOINT_MIN_INTERVAL:
                        wait_for = _ENDPOINT_MIN_INTERVAL - elapsed_ep
                        await asyncio.sleep(wait_for)

                # Enforce minimal interval for identical requests
                last_id = _last_request_timestamps.get(identical_key)
                now = time.monotonic()
                if last_id is not None:
                    elapsed_id = now - last_id
                    if elapsed_id < _IDENTICAL_MIN_INTERVAL:
                        wait_for = _IDENTICAL_MIN_INTERVAL - elapsed_id
                        await asyncio.sleep(wait_for)

                try:
                    async with session.request(method, url, headers=headers, params=params, json=json_body) as resp:
                        status = resp.status
                        text = await resp.text()

                        # Update timestamps after getting response
                        ts = time.monotonic()
                        _last_request_timestamps[identical_key] = ts
                        _last_endpoint_timestamps[endpoint_key] = ts

                        # Handle rate limit 429
                        if status == 429:
                            # Try to parse JSON body to get wait_time
                            wait_time = None
                            try:
                                body_json = json.loads(text)
                                wait_time = body_json.get("wait_time")
                            except Exception:
                                body_json = None

                            # Check Retry-After header
                            retry_after = None
                            try:
                                header_val = resp.headers.get("Retry-After")
                                if header_val is not None:
                                    retry_after = int(float(header_val))
                            except Exception:
                                retry_after = None

                            # Determine sleep time (prefer server-provided wait_time / retry_after)
                            sleep_time = None
                            if wait_time is not None:
                                try:
                                    sleep_time = float(wait_time)
                                except Exception:
                                    sleep_time = None
                            if sleep_time is None and retry_after is not None:
                                sleep_time = float(retry_after)

                            # Fallback to exponential backoff
                            if sleep_time is None:
                                sleep_time = backoff

                            # Cap
                            if sleep_time > _MAX_BACKOFF:
                                sleep_time = _MAX_BACKOFF

                            retries += 1
                            if retries > _MAX_RETRIES:
                                return {"success": False, "error": "rate_limited", "detail": "Exceeded retry attempts due to 429 responses"}

                            # Sleep outside of locks to avoid long blocking others: release locks, sleep, then retry loop
                            pass_sleep = sleep_time
                        else:
                            # For other statuses, try to return JSON if possible
                            try:
                                return json.loads(text)
                            except Exception:
                                return {"success": False, "error": f"http_{status}", "details": text}

                except aiohttp.ClientError as e:
                    retries += 1
                    if retries > _MAX_RETRIES:
                        return {"success": False, "error": "network_error", "details": str(e)}
                    # Sleep outside locks
                    pass_sleep = backoff

        # Если мы сюда попали, значит нужно подождать и повторить (rate limited or transient network)
        # Увеличиваем backoff только для сетевых/429 последовательных
        await asyncio.sleep(pass_sleep)
        backoff = min(backoff * 2, _MAX_BACKOFF)
        continue


async def get_all_phone_numbers() -> dict:
    """Получает список всех номеров телефонов из API с учетом пагинации"""
    headers = {"Authorization": f"Token {LUBOYDOMEN_API_TOKEN}"}
    all_numbers = []
    offset = 0
    limit = 100
    total = None

    async with aiohttp.ClientSession() as session:
        while True:
            params = {"limit": limit, "offset": offset}
            result = await _fetch_json_with_rate_handling(
                session,
                "GET",
                f"{LUBOYDOMEN_API_BASE}/numbers",
                headers=headers,
                params=params
            )

            # Если вернулась ошибка (формат не success True)
            if not result.get("success"):
                return result

            data = result.get("data", {})
            numbers = data.get("numbers", [])
            pagination = data.get("pagination", {})

            all_numbers.extend(numbers)

            if total is None:
                total = pagination.get("total", len(numbers))

            # Проверяем, есть ли ещё номера
            offset += limit
            if offset >= total or not numbers:
                break

    return {
        "success": True,
        "data": {
            "numbers": all_numbers,
            "pagination": {
                "total": total or len(all_numbers),
                "limit": limit,
                "offset": 0
            }
        }
    }


async def purchase_number(custom_name: str, country_code: str = "GB", duration_months: int = 1, auto_renew: bool = False) -> dict:
    """Покупает один номер телефона через API"""
    headers = {
        "Authorization": f"Token {LUBOYDOMEN_API_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "country_code": country_code,
        "duration_months": duration_months,
        "auto_renew": auto_renew,
        "custom_name": custom_name
    }

    async with aiohttp.ClientSession() as session:
        result = await _fetch_json_with_rate_handling(
            session,
            "POST",
            f"{LUBOYDOMEN_API_BASE}/numbers/purchase/",
            headers=headers,
            json_body=payload
        )
        return result


async def get_sms_messages(number_id: str, limit: int = 100, offset: int = 0) -> dict:
    """Получает список SMS для номера по его ID"""
    headers = {"Authorization": f"Token {LUBOYDOMEN_API_TOKEN}"}

    params = {"limit": limit, "offset": offset}

    async with aiohttp.ClientSession() as session:
        result = await _fetch_json_with_rate_handling(
            session,
            "GET",
            f"{LUBOYDOMEN_API_BASE}/numbers/{number_id}/sms",
            headers=headers,
            params=params
        )
        return result
