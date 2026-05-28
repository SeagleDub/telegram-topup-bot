"""
Сервис для работы с API MultiCards (https://api.multicards.io/v1).

Аутентификация: логин по email/password -> JWT, который передаётся в заголовке
x-auth-token (и дублируется в Authorization: Bearer для совместимости). Токен
кэшируется в памяти и переполучается по истечении (claim exp в JWT) или при 401.

Ошибки делаем "шумными": логируем и отправляем в Bugsnag, наружу возвращаем
{"success": False, "error": ..., "details": ...}. Успешные ответы возвращаются
как есть (список для card/list, объект карты для update/close, {items:[...]}
для транзакций).
"""
import json
import time
import base64
import asyncio
import logging
from datetime import datetime

import aiohttp
import bugsnag

from config import MULTICARDS_EMAIL, MULTICARDS_PASSWORD, BUGSNAG_TOKEN

logger = logging.getLogger(__name__)

MULTICARDS_API_BASE = "https://api.multicards.io/v1"
_REQUEST_TIMEOUT = 30

# Кэш JWT-токена в памяти процесса
_token: str | None = None
_token_exp: int = 0           # unix-время истечения токена (из claim exp)
_token_lock = asyncio.Lock()  # сериализуем логин, чтобы не логиниться параллельно


def _report(error, endpoint: str, **meta) -> None:
    """Логирует ошибку и отправляет её в Bugsnag (если настроен)."""
    logger.error("[multicards] %s -> %s | %s", endpoint, error, meta)
    if not BUGSNAG_TOKEN:
        return
    exc = error if isinstance(error, Exception) else Exception(str(error))
    try:
        bugsnag.notify(exc, meta_data={"multicards": {"endpoint": endpoint, **meta}})
    except Exception as e:
        logger.error("[multicards] bugsnag.notify failed: %s", e)


def _jwt_exp(token: str) -> int:
    """Достаёт claim exp (unix) из JWT без проверки подписи. 0 при неудаче."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return int(data.get("exp", 0))
    except Exception:
        return 0


async def _login() -> dict | str:
    """Логинится и кэширует токен. Возвращает токен (str) или dict с ошибкой."""
    global _token, _token_exp

    if not MULTICARDS_EMAIL or not MULTICARDS_PASSWORD:
        _report("MULTICARDS_EMAIL / MULTICARDS_PASSWORD не заданы в окружении", "auth/login")
        return {"success": False, "error": "config_missing",
                "details": "Логин/пароль MultiCards не настроены в .env"}

    url = f"{MULTICARDS_API_BASE}/auth/login"
    body = {"email": MULTICARDS_EMAIL, "password": MULTICARDS_PASSWORD}
    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=body) as resp:
                status = resp.status
                text = await resp.text()
                if status != 200:
                    snippet = text[:300]
                    _report(f"HTTP {status}: {snippet}", "auth/login", status=status)
                    return {"success": False, "error": f"http_{status}",
                            "details": f"Логин MultiCards не удался (HTTP {status})"}
                try:
                    token = json.loads(text).get("token")
                except ValueError:
                    _report(f"невалидный JSON: {text[:300]}", "auth/login")
                    return {"success": False, "error": "bad_json",
                            "details": "Некорректный ответ логина MultiCards"}
    except aiohttp.ClientError as e:
        _report(e, "auth/login", kind="network_error")
        return {"success": False, "error": "network_error", "details": str(e)}
    except Exception as e:
        _report(e, "auth/login", kind="unexpected")
        return {"success": False, "error": "unexpected", "details": str(e)}

    if not token:
        _report("в ответе логина нет token", "auth/login")
        return {"success": False, "error": "no_token", "details": "MultiCards не вернул токен"}

    _token = token
    _token_exp = _jwt_exp(token)
    return token


async def _get_token() -> dict | str:
    """Возвращает валидный токен из кэша или логинится заново."""
    async with _token_lock:
        # Запас 60 секунд до истечения; если exp не распознан (0) — считаем валидным
        if _token and (_token_exp == 0 or _token_exp - time.time() > 60):
            return _token
        return await _login()


def _invalidate_token() -> None:
    global _token, _token_exp
    _token = None
    _token_exp = 0


async def _request(method: str, endpoint: str, json_body: dict | None = None, _retry: bool = True):
    """Выполняет авторизованный запрос. Возвращает распарсенный JSON при успехе
    или dict с ошибкой. При 401 один раз перелогинивается и повторяет запрос.
    """
    token = await _get_token()
    if isinstance(token, dict):  # ошибка логина
        return token

    url = f"{MULTICARDS_API_BASE}/{endpoint}"
    headers = {
        "x-auth-token": token,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, url, headers=headers, json=json_body) as resp:
                status = resp.status
                text = await resp.text()

                if status == 401 and _retry:
                    _invalidate_token()
                    return await _request(method, endpoint, json_body, _retry=False)

                if status != 200:
                    snippet = text[:300]
                    _report(f"HTTP {status}: {snippet}", endpoint, status=status)
                    return {"success": False, "error": f"http_{status}",
                            "details": f"HTTP {status}: {snippet}"}

                if not text:
                    return {}
                try:
                    return json.loads(text)
                except ValueError:
                    snippet = text[:300]
                    _report(f"невалидный JSON: {snippet}", endpoint)
                    return {"success": False, "error": "bad_json",
                            "details": f"Некорректный ответ MultiCards: {snippet}"}
    except aiohttp.ClientError as e:
        _report(e, endpoint, kind="network_error")
        return {"success": False, "error": "network_error", "details": str(e)}
    except Exception as e:
        _report(e, endpoint, kind="unexpected")
        return {"success": False, "error": "unexpected", "details": str(e)}


def _is_error(result) -> bool:
    """Признак ошибочного ответа нашего формата (успех card/list — это список)."""
    return isinstance(result, dict) and (bool(result.get("error")) or result.get("success") is False)


def card_digits(value) -> str:
    """Возвращает только цифры из номера карты (строка или объект карты)."""
    if isinstance(value, dict):
        value = value.get("cardNumber", "")
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def current_month_period() -> tuple[int, int]:
    """(начало текущего календарного месяца, текущее время) в unix-секундах."""
    now = datetime.now()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp()), int(now.timestamp())


async def get_cards():
    """Список карт (GET /card/list). Успех — JSON-массив карт."""
    return await _request("GET", "card/list")


async def find_card_by_number(number: str) -> dict | None:
    """Ищет карту по полному номеру в card/list.

    Контракт совпадает с adscard.find_card_by_number:
    {"card": <карта>, "multiple": bool} | {"error": ...} | None.
    """
    result = await get_cards()
    if _is_error(result):
        return {"error": result.get("error"), "details": result.get("details")}

    cards = result if isinstance(result, list) else []
    needle = card_digits(number)
    if not needle:
        return None

    exact = [c for c in cards if card_digits(c) and card_digits(c) == needle]
    if exact:
        return {"card": exact[0], "multiple": len(exact) > 1}

    if len(needle) >= 4:
        last4 = needle[-4:]
        by_last4 = [c for c in cards if card_digits(c) and card_digits(c)[-4:] == last4]
        if by_last4:
            return {"card": by_last4[0], "multiple": len(by_last4) > 1}

    return None


async def set_total_limit(card_id, value) -> dict:
    """Устанавливает глобальный лимит карты (POST /card/{id}/update, totalLimit)."""
    return await _request("POST", f"card/{card_id}/update", {"totalLimit": value})


async def set_daily_limit(card_id, value) -> dict:
    """Устанавливает дневной лимит карты (POST /card/{id}/update, dailyLimit)."""
    return await _request("POST", f"card/{card_id}/update", {"dailyLimit": value})


async def block_card(card_id) -> dict:
    """Закрывает карту (POST /card/{id}/close). Действие необратимо."""
    return await _request("POST", f"card/{card_id}/close")


async def get_transactions(period_start: int, period_end: int) -> dict:
    """Транзакции команды за период (POST /transaction/pageable).

    periodStart/periodEnd — unix-секунды в виде строк (как в документации).
    Успех — объект с полем items.
    """
    return await _request("POST", "transaction/pageable", {
        "periodStart": str(period_start),
        "periodEnd": str(period_end),
    })
