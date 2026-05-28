"""
Сервис для работы с API AdsCard (https://talkv2.adscard.net/v3).

Аутентификация двухслойная:
- Bearer-токен ADSCARD_TOKEN передаётся в заголовке Application-Authorization
- auth_token ADSCARD_AUTH_TOKEN передаётся в JSON-теле каждого запроса

Все запросы — POST с JSON. Ошибки делаем "шумными": при сетевой ошибке,
не-200 ответе или нераспарсенном JSON возвращаем словарь
{"success": False, "error": ..., "details": ...} и логируем контекст,
чтобы проблема была видна, а не маскировалась тихим None.
"""
import json
import logging
import aiohttp
import bugsnag

from config import ADSCARD_TOKEN, ADSCARD_AUTH_TOKEN, BUGSNAG_TOKEN

logger = logging.getLogger(__name__)

ADSCARD_API_BASE = "https://talkv2.adscard.net/v3"

# Тайм-аут на запрос (сек)
_REQUEST_TIMEOUT = 30


def _report(error, endpoint: str, **meta) -> None:
    """Логирует ошибку и отправляет её в Bugsnag (если настроен).

    error может быть как Exception, так и строкой с описанием.
    """
    logger.error("[adscard] %s -> %s | %s", endpoint, error, meta)
    if not BUGSNAG_TOKEN:
        return
    exc = error if isinstance(error, Exception) else Exception(str(error))
    try:
        bugsnag.notify(exc, meta_data={"adscard": {"endpoint": endpoint, **meta}})
    except Exception as e:  # на всякий случай не роняем поток из-за репортинга
        logger.error("[adscard] bugsnag.notify failed: %s", e)


async def _post(endpoint: str, payload: dict | None = None) -> dict:
    """Выполняет POST-запрос к AdsCard API.

    Возвращает распарсенный JSON при успехе, либо словарь с ключом
    success=False и описанием ошибки. Никогда не бросает наружу —
    вызывающий код проверяет наличие "error".
    """
    if not ADSCARD_TOKEN or not ADSCARD_AUTH_TOKEN:
        _report("ADSCARD_TOKEN / ADSCARD_AUTH_TOKEN не заданы в окружении", endpoint)
        return {"success": False, "error": "config_missing",
                "details": "Токены AdsCard не настроены в .env"}

    url = f"{ADSCARD_API_BASE}/{endpoint}"
    headers = {
        "Application-Authorization": f"Bearer {ADSCARD_TOKEN}",
        "Content-Type": "application/json",
    }
    body = {"auth_token": ADSCARD_AUTH_TOKEN}
    if payload:
        body.update(payload)

    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=body) as resp:
                status = resp.status
                text = await resp.text()

                if status != 200:
                    snippet = text[:300]
                    _report(f"HTTP {status}: {snippet}", endpoint, status=status)
                    return {"success": False, "error": f"http_{status}",
                            "details": f"HTTP {status}: {snippet}"}

                try:
                    return json.loads(text)
                except ValueError:
                    snippet = text[:300]
                    _report(f"невалидный JSON: {snippet}", endpoint)
                    return {"success": False, "error": "bad_json",
                            "details": f"Некорректный ответ AdsCard: {snippet}"}
    except aiohttp.ClientError as e:
        _report(e, endpoint, kind="network_error")
        return {"success": False, "error": "network_error", "details": str(e)}
    except Exception as e:  # таймаут и прочее
        _report(e, endpoint, kind="unexpected")
        return {"success": False, "error": "unexpected", "details": str(e)}


def _has_error(result: dict) -> bool:
    """Признак того, что ответ AdsCard содержит ошибку нашего формата."""
    return bool(result.get("error")) or result.get("success") is False


def card_digits(value) -> str:
    """Возвращает только цифры из номера карты (из строки или поля number карты)."""
    if isinstance(value, dict):
        value = value.get("number", "")
    return "".join(ch for ch in str(value or "") if ch.isdigit())


async def get_team_cards() -> dict:
    """Получает список карт команды (teams/cards_list).

    Без user_id возвращает карты по всей команде.
    """
    return await _post("teams/cards_list")


async def find_card_by_number(number: str) -> dict | None:
    """Ищет карту по введённому полному номеру в списке teams/cards_list.

    Пользователь вводит полный номер карты. Сначала пытаемся найти точное
    совпадение цифр. Если номера в списке маскированы и точного совпадения нет —
    фолбэк по последним 4 цифрам. Возвращает словарь:
    {"card": <карта>, "multiple": bool} при успехе,
    {"error": ...} при ошибке API, либо None если карта не найдена.
    """
    result = await get_team_cards()
    if _has_error(result):
        return {"error": result.get("error"), "details": result.get("details")}

    data = result.get("data", {})
    # data приходит как объект {"0": {...}, "1": {...}}
    cards = list(data.values()) if isinstance(data, dict) else (data or [])

    needle = card_digits(number)
    if not needle:
        return None

    # 1) Точное совпадение полного номера
    exact = [c for c in cards if card_digits(c) and card_digits(c) == needle]
    if exact:
        return {"card": exact[0], "multiple": len(exact) > 1}

    # 2) Фолбэк по последним 4 цифрам (на случай маскированных номеров в списке)
    if len(needle) >= 4:
        last4 = needle[-4:]
        by_last4 = [c for c in cards if card_digits(c) and card_digits(c)[-4:] == last4]
        if by_last4:
            return {"card": by_last4[0], "multiple": len(by_last4) > 1}

    return None


async def set_card_limit(card_id, limit) -> dict:
    """Устанавливает новый абсолютный лимит карты (cards/limit).

    В teams/* нет эндпоинта смены лимита, поэтому используем общий cards/limit.
    """
    return await _post("cards/limit", {"cards_id": [card_id], "limit": limit})


async def block_card(card_id) -> dict:
    """Блокирует карту команды (teams/cards_block)."""
    return await _post("teams/cards_block", {"cards_id": [card_id]})


async def get_team_transactions(time: str = "month") -> dict:
    """Получает транзакции по картам команды (teams/cards_transactions).

    Эндпоинт не принимает card_id и отдаёт транзакции всей команды за период;
    фильтрация по конкретной карте делается на стороне бота. time обязателен.
    """
    return await _post("teams/cards_transactions", {"time": time})
