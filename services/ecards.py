"""
Сервис для работы с внешним API eCards (https://ecardsnew.cab/api/modern/api-external/v1).

Авторизация статическая: заголовок Authorization: Bearer <ECARDS_TOKEN>.
В документации (API.md) нет эндпоинта логина, поэтому токен считается
долгоживущим и хранится в .env (как ADSCARD_TOKEN).

Массивные query-параметры передаются в нотации filterId[]=1&filterId[]=2
(список кортежей в aiohttp). Даты — ISO 8601 (UTC, суффикс Z).

Ошибки делаем "шумными": логируем + Bugsnag, наружу отдаём
{"success": False, "error": ..., "details": ...}. Успех GET /card,
/card-operation, /card-group — коллекция объектов; ответ может прийти как
JSON-массив, {items:[...]}, {data:[...]} или {data:{idx:{...}}} — нормализуем
в список хелпером _as_list.

ВНИМАНИЕ: API.md документирует только query-параметры, тела ответов — нет.
Имена полей ответа (номер карты, id, сумма, валюта, группа, тип операции) не
специфицированы, поэтому доступ к полям сделан толерантным (_pick с несколькими
кандидатами). Если живой ответ использует другие имена — поправить кандидатов
в блоке _FIELDS ниже.
"""
import re
import json
import logging
from datetime import datetime, timezone, timedelta

import aiohttp
import bugsnag

from config import ECARDS_TOKEN, BUGSNAG_TOKEN

logger = logging.getLogger(__name__)

ECARDS_API_BASE = "https://ecardsnew.cab/api/modern/api-external/v1"

_REQUEST_TIMEOUT = 30
_PAGE_LIMIT = 100          # максимум по API.md (0–100)
_MAX_PAGES = 50            # safety-cap на пагинацию (50*100 = 5000 операций)

# Имена полей карты/группы — подтверждены живыми ответами.
# Карта (GET /card, а также вложенная card в операции) отдаёт полный cardNumber.
_FIELDS = {
    "card_id": ("id",),
    "card_number": ("cardNumber",),
    "card_status": ("status",),
    "card_currency": ("currency",),
    "group_id": ("id",),
    "group_name": ("name",),
}

# Классификация типа операции для нетто-расхода делается по подстроке (см.
# _op_sign), т.к. живые данные используют напр. "debit_authorization" (это
# успешное списание, а не hold). Явные множества здесь только для документации.
DECLINE_MARKERS = ("declin",)                 # отклонённые — не считаем
SKIP_MARKERS = ("verif",)                     # 3DS-верификации ($0) — не считаем
REFUND_MARKERS = ("refund", "return", "revers", "release", "unhold")  # возврат (−)
SPEND_MARKERS = ("debit", "charge")           # списание (+)


def _report(error, endpoint: str, **meta) -> None:
    """Логирует ошибку и отправляет её в Bugsnag (если настроен)."""
    logger.error("[ecards] %s -> %s | %s", endpoint, error, meta)
    if not BUGSNAG_TOKEN:
        return
    exc = error if isinstance(error, Exception) else Exception(str(error))
    try:
        bugsnag.notify(exc, meta_data={"ecards": {"endpoint": endpoint, **meta}})
    except Exception as e:
        logger.error("[ecards] bugsnag.notify failed: %s", e)


def _pick(obj, key: str, default=None):
    """Возвращает первое непустое значение из кандидатов _FIELDS[key]."""
    if not isinstance(obj, dict):
        return default
    for candidate in _FIELDS.get(key, (key,)):
        value = obj.get(candidate)
        if value not in (None, ""):
            return value
    return default


def _as_list(result) -> list:
    """Нормализует ответ-коллекцию в список.

    Фактическая обёртка eCards — пейджинг: {"data": {"content": [...], ...}}.
    Также поддерживаем массив, {content:[...]} и {items:[...]} на всякий случай.
    """
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        data = result.get("data")
        if isinstance(data, dict) and isinstance(data.get("content"), list):
            return data["content"]
        if isinstance(data, list):
            return data
        for wrapper in ("content", "items", "rows"):
            inner = result.get(wrapper)
            if isinstance(inner, list):
                return inner
    return []


def _page_is_last(result) -> bool:
    """True, если пейджинг сообщает, что это последняя страница."""
    data = result.get("data") if isinstance(result, dict) else None
    if isinstance(data, dict) and "last" in data:
        return bool(data.get("last"))
    return False


async def _request(method: str, endpoint: str, params=None, json_body: dict | None = None):
    """Выполняет запрос к API eCards.

    params — список кортежей (для массивных ключей key[]=v). Возвращает
    распарсенный JSON при успехе либо dict с ключом success=False. Наружу не
    бросает — вызывающий проверяет наличие "error".
    """
    if not ECARDS_TOKEN:
        _report("ECARDS_TOKEN не задан в окружении", endpoint)
        return {"success": False, "error": "config_missing",
                "details": "Токен eCards не настроен в .env"}

    url = f"{ECARDS_API_BASE}/{endpoint.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {ECARDS_TOKEN}",
        "Content-Type": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, url, headers=headers,
                                       params=params, json=json_body) as resp:
                status = resp.status
                text = await resp.text()

                if status not in (200, 201):
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
                            "details": f"Некорректный ответ eCards: {snippet}"}
    except aiohttp.ClientError as e:
        _report(e, endpoint, kind="network_error")
        return {"success": False, "error": "network_error", "details": str(e)}
    except Exception as e:  # таймаут и прочее
        _report(e, endpoint, kind="unexpected")
        return {"success": False, "error": "unexpected", "details": str(e)}


def _is_error(result) -> bool:
    """Признак ошибочного ответа нашего формата (успех коллекций — список/обёртка)."""
    return isinstance(result, dict) and (bool(result.get("error")) or result.get("success") is False)


def card_digits(value) -> str:
    """Возвращает только цифры из номера карты (строка или объект карты)."""
    if isinstance(value, dict):
        value = _pick(value, "card_number", "")
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def card_id(card: dict):
    """ID карты (толерантно)."""
    return _pick(card, "card_id")


def card_number(card: dict):
    """Полный номер карты (GET /card отдаёт cardNumber)."""
    return _pick(card, "card_number")


# --- Аксессоры операции card-operation (структура вложенная) ---------------- #
def op_card(op: dict) -> dict:
    """Вложенный объект карты внутри операции."""
    c = op.get("card") if isinstance(op, dict) else None
    return c if isinstance(c, dict) else {}


def op_value(op: dict):
    """Сумма операции — поле value (строка), в валюте карты."""
    return op.get("value") if isinstance(op, dict) else None


def op_currency(op: dict):
    """Валюта операции: валюта карты, иначе originalCurrency."""
    return op_card(op).get("currency") or (op.get("originalCurrency") if isinstance(op, dict) else None)


def op_type(op: dict):
    return op.get("type") if isinstance(op, dict) else None


def op_date(op: dict):
    return op.get("createdAt") if isinstance(op, dict) else None


def op_card_id(op: dict):
    return op_card(op).get("id")


def op_card_number(op: dict):
    return op_card(op).get("cardNumber")


def op_merchant(op: dict):
    if not isinstance(op, dict):
        return None
    return op.get("merchantInfo") or op.get("description")


def _iso(dt: datetime) -> str:
    """datetime → ISO 8601 UTC с миллисекундами и суффиксом Z."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _day_start(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _day_end(dt: datetime) -> datetime:
    return dt.replace(hour=23, minute=59, second=59, microsecond=999000)


def current_month_period() -> tuple[str, str]:
    """(начало текущего календарного месяца, сейчас) в ISO 8601 UTC."""
    now = datetime.now(timezone.utc)
    return _iso(_day_start(now).replace(day=1)), _iso(now)


def prev_month_period() -> tuple[str, str]:
    """(начало, конец предыдущего календарного месяца) в ISO 8601 UTC."""
    now = datetime.now(timezone.utc)
    first_this = _day_start(now).replace(day=1)
    end_prev = _day_end(first_this - timedelta(days=1))
    start_prev = _day_start(end_prev).replace(day=1)
    return _iso(start_prev), _iso(end_prev)


def last_days_period(days: int) -> tuple[str, str]:
    """(начало N дней назад, сейчас) в ISO 8601 UTC."""
    now = datetime.now(timezone.utc)
    return _iso(_day_start(now - timedelta(days=days))), _iso(now)


def parse_period(text: str) -> tuple[str, str] | None:
    """Парсит пользовательский период из двух дат через пробел/запятую.

    Форматы дат: ДД.ММ.ГГГГ, ГГГГ-ММ-ДД, ДД/ММ/ГГГГ. Начало — 00:00, конец —
    23:59:59. Если даты перепутаны местами — меняем. None при ошибке.
    """
    parts = str(text or "").replace(",", " ").split()
    if len(parts) != 2:
        return None

    def parse_one(s: str):
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    d1, d2 = parse_one(parts[0]), parse_one(parts[1])
    if not d1 or not d2:
        return None
    if d2 < d1:
        d1, d2 = d2, d1
    return _iso(_day_start(d1)), _iso(_day_end(d2))


# --------------------------------------------------------------------------- #
# Карты
# --------------------------------------------------------------------------- #
async def get_cards(search: str | None = None) -> dict | list:
    """Список карт (GET /card). Успех — коллекция карт."""
    params = [("limit", str(_PAGE_LIMIT))]
    if search:
        params.append(("search", search))
    return await _request("GET", "card", params=params)


async def find_card_by_number(number: str) -> dict | None:
    """Ищет карту по полному номеру в GET /card (как adscard/multicards).

    Контракт: {"card": <карта>, "multiple": bool} | {"error": ...} | None.
    Пробуем сузить выборку через search, при необходимости листаем страницы.
    """
    needle = card_digits(number)
    if not needle:
        return None

    last4 = needle[-4:] if len(needle) >= 4 else None
    for page in range(_MAX_PAGES):
        result = await _request("GET", "card", params=[
            ("offset", str(page * _PAGE_LIMIT)),
            ("limit", str(_PAGE_LIMIT)),
            ("search", number),
        ])
        if _is_error(result):
            return {"error": result.get("error"), "details": result.get("details")}
        cards = _as_list(result)

        exact = [c for c in cards if card_digits(c) == needle]
        if exact:
            return {"card": exact[0], "multiple": len(exact) > 1}
        if last4:
            by_last4 = [c for c in cards if card_digits(c) and card_digits(c)[-4:] == last4]
            if by_last4:
                return {"card": by_last4[0], "multiple": len(by_last4) > 1}

        if _page_is_last(result) or len(cards) < _PAGE_LIMIT:
            break
    return None


async def block_card(card_id_value) -> dict:
    """Закрывает карту (POST /card/close). Действие необратимо."""
    return await _request("POST", "card/close", json_body={"cardsIds": [card_id_value]})


# --------------------------------------------------------------------------- #
# Операции по картам
# --------------------------------------------------------------------------- #
async def get_card_operations(created_from: str | None = None, created_to: str | None = None,
                              card_ids: list | None = None,
                              group_ids: list | None = None,
                              offset: int = 0, limit: int = _PAGE_LIMIT) -> dict | list:
    """Одна страница операций (GET /card-operation), отсортировано по дате убыв.

    created_from/created_to опциональны: без них отдаются просто последние операции.
    """
    params = [
        ("offset", str(offset)),
        ("limit", str(limit)),
        ("sortBy", "createdAt"),
        ("sortDirection", "desc"),
    ]
    if created_from:
        params.append(("createdFrom", created_from))
    if created_to:
        params.append(("createdTo", created_to))
    for cid in (card_ids or []):
        params.append(("filterCardId[]", str(cid)))
    for gid in (group_ids or []):
        params.append(("filterCardGroupId[]", str(gid)))
    return await _request("GET", "card-operation", params=params)


async def get_all_group_operations(group_id_value, created_from: str, created_to: str) -> dict | list:
    """Все операции группы за период с пагинацией.

    Возвращает список операций при успехе или dict с ошибкой. При упоре в
    safety-cap (_MAX_PAGES) — логирует, чтобы усечение не было тихим.
    """
    all_ops: list = []
    for page in range(_MAX_PAGES):
        result = await get_card_operations(
            created_from, created_to, group_ids=[group_id_value],
            offset=page * _PAGE_LIMIT, limit=_PAGE_LIMIT,
        )
        if _is_error(result):
            return result
        ops = _as_list(result)
        all_ops.extend(ops)
        if _page_is_last(result) or len(ops) < _PAGE_LIMIT:
            break
    else:
        _report(f"достигнут предел пагинации {_MAX_PAGES} страниц", "card-operation",
                group_id=group_id_value, collected=len(all_ops))
    return all_ops


def _op_sign(op_type_value) -> float:
    """Знак операции для расхода: +1 списание, -1 возврат, 0 не учитывать.

    Классификация по подстроке типа: живые данные используют
    "debit_authorization" (успешное списание), поэтому явных множеств мало.
    Порядок проверок важен: сначала отсев (declined/verification), затем возврат,
    затем списание.
    """
    t = str(op_type_value or "").lower()
    if not t:
        return 0.0
    if any(m in t for m in DECLINE_MARKERS):
        return 0.0
    if any(m in t for m in SKIP_MARKERS):
        return 0.0
    if any(m in t for m in REFUND_MARKERS):
        return -1.0
    if any(m in t for m in SPEND_MARKERS):
        return 1.0
    return 0.0


def sum_spend_by_currency(operations: list) -> dict[str, float]:
    """Нетто-расход по валютам: + списания, − возвраты; прочие типы игнорируются.

    Сумма — поле value операции, валюта — валюта карты. Оба парсятся толерантно.
    """
    totals: dict[str, float] = {}
    for op in operations:
        if not isinstance(op, dict):
            continue
        sign = _op_sign(op_type(op))
        if sign == 0.0:
            continue

        try:
            amount = abs(float(str(op_value(op)).replace(",", ".")))
        except (TypeError, ValueError):
            continue

        currency = str(op_currency(op) or "").upper() or "?"
        totals[currency] = totals.get(currency, 0.0) + sign * amount
    return totals


# --------------------------------------------------------------------------- #
# Группы карт
# --------------------------------------------------------------------------- #
async def get_card_groups() -> dict | list:
    """Список групп карт (GET /card-group). Успех — коллекция групп."""
    return await _request("GET", "card-group", params=[("limit", str(_PAGE_LIMIT))])


# --------------------------------------------------------------------------- #
# Уведомления / 3DS-коды
# --------------------------------------------------------------------------- #
def notif_payload(notification: dict) -> dict:
    """Разбирает JSON-строку payload уведомления. {} при ошибке."""
    raw = notification.get("payload") if isinstance(notification, dict) else None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


async def get_notifications(notif_type: str = "code_3ds", limit: int = _PAGE_LIMIT) -> dict | list:
    """Уведомления заданного типа (GET /notification), новые сверху."""
    params = [
        ("offset", "0"),
        ("limit", str(limit)),
        ("sortBy", "createdAt"),
        ("sortDirection", "desc"),
        ("filterType[]", notif_type),
    ]
    return await _request("GET", "notification", params=params)


async def find_latest_3ds(card_id_value) -> dict | None:
    """Последний 3DS-код для карты из ленты уведомлений code_3ds.

    Возвращает {"otpCode","amount","currency","merchant","cardNumber","createdAt"}
    для самой свежей записи с нужным cardId, None если нет, либо dict с ошибкой.
    """
    result = await get_notifications("code_3ds")
    if _is_error(result):
        return result
    needle = str(card_id_value)
    for n in _as_list(result):  # уже отсортировано по дате убыв.
        payload = notif_payload(n)
        if str(payload.get("cardId")) == needle:
            return {
                "otpCode": payload.get("otpCode"),
                "amount": payload.get("amount"),
                "currency": payload.get("currency"),
                "merchant": payload.get("merchant"),
                "cardNumber": payload.get("cardNumber"),
                "createdAt": n.get("createdAt"),
            }
    return None


def group_id(group: dict):
    return _pick(group, "group_id")


def group_name(group: dict):
    return _pick(group, "group_name")


def group_matches_tg(group: dict, tg_id: int) -> bool:
    """True, если tg_id присутствует в имени группы как отдельный числовой токен.

    Матчим по токенам (\\d+), а не по подстроке, чтобы 123 не совпало со 1234.
    """
    needle = str(tg_id)
    return any(tok == needle for tok in re.findall(r"\d+", str(group_name(group) or "")))


async def get_buyer_groups(tg_id: int):
    """Группы байера (в имени которых есть его tg_id).

    Возвращает список групп при успехе или dict с ошибкой (контракт _is_error).
    """
    result = await get_card_groups()
    if _is_error(result):
        return result
    return [g for g in _as_list(result) if group_matches_tg(g, tg_id)]
