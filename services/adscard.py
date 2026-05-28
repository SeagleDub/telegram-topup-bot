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

from config import ADSCARD_TOKEN, ADSCARD_AUTH_TOKEN

logger = logging.getLogger(__name__)

ADSCARD_API_BASE = "https://talkv2.adscard.net/v3"

# Тайм-аут на запрос (сек)
_REQUEST_TIMEOUT = 30


async def _post(endpoint: str, payload: dict | None = None) -> dict:
    """Выполняет POST-запрос к AdsCard API.

    Возвращает распарсенный JSON при успехе, либо словарь с ключом
    success=False и описанием ошибки. Никогда не бросает наружу —
    вызывающий код проверяет наличие "error".
    """
    if not ADSCARD_TOKEN or not ADSCARD_AUTH_TOKEN:
        logger.error("[adscard] ADSCARD_TOKEN / ADSCARD_AUTH_TOKEN не заданы в окружении")
        return {"success": False, "error": "config_missing",
                "details": "AdsCard токены не настроены"}

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
                    logger.error("[adscard] %s -> HTTP %s: %s", endpoint, status, text[:500])
                    return {"success": False, "error": f"http_{status}",
                            "details": "Ошибка ответа сервера AdsCard"}

                try:
                    return json.loads(text)
                except ValueError:
                    logger.error("[adscard] %s -> не удалось распарсить JSON: %s", endpoint, text[:500])
                    return {"success": False, "error": "bad_json",
                            "details": "Некорректный ответ сервера AdsCard"}
    except aiohttp.ClientError as e:
        logger.error("[adscard] %s -> сетевая ошибка: %s", endpoint, e)
        return {"success": False, "error": "network_error", "details": str(e)}
    except Exception as e:  # таймаут и прочее
        logger.error("[adscard] %s -> непредвиденная ошибка: %s", endpoint, e)
        return {"success": False, "error": "unexpected", "details": str(e)}


def _has_error(result: dict) -> bool:
    """Признак того, что ответ AdsCard содержит ошибку нашего формата."""
    return bool(result.get("error")) or result.get("success") is False


async def get_cards() -> dict:
    """Получает список карт пользователя (cards/list)."""
    return await _post("cards/list")


async def find_card_by_number(number: str) -> dict | None:
    """Ищет карту по введённому полному номеру в списке cards/list.

    Пользователь вводит полный номер карты. Сначала пытаемся найти точное
    совпадение цифр. Если в cards/list номера приходят маскированными и точного
    совпадения нет — фолбэк по последним 4 цифрам. Возвращает словарь:
    {"card": <карта>, "multiple": bool} при успехе,
    {"error": ...} при ошибке API, либо None если карта не найдена.
    """
    result = await get_cards()
    if _has_error(result):
        return {"error": result.get("error"), "details": result.get("details")}

    data = result.get("data", {})
    # data приходит как объект {"0": {...}, "1": {...}}
    cards = list(data.values()) if isinstance(data, dict) else (data or [])

    needle = "".join(ch for ch in str(number) if ch.isdigit())
    if not needle:
        return None

    def card_digits(card) -> str:
        return "".join(ch for ch in str(card.get("number", "")) if ch.isdigit())

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
    """Устанавливает новый абсолютный лимит карты (cards/limit)."""
    return await _post("cards/limit", {"cards_id": [card_id], "limit": limit})


async def block_card(card_id) -> dict:
    """Блокирует карту (cards/block)."""
    return await _post("cards/block", {"cards_id": [card_id]})


async def get_card_transactions(card_id, time: str = "month") -> dict:
    """Получает транзакции по карте (cards/transactions). time обязателен."""
    return await _post("cards/transactions", {"time": time, "card_id": card_id})
