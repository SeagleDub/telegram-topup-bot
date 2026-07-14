"""
Обработчики для функции "Действия с картами".

Поток: выбор банка -> ввод полного номера карты -> выбор действия
(поменять лимит / заблокировать / последние транзакции).

Поддерживаются два банка:
- AdsCard   (services.adscard)   — командные эндпоинты teams/*, лимит один.
- MultiCards (services.multicards) — JWT-логин, лимит раздельный (глобальный/дневной).

Номера карт выводятся маскированно (последние 4 цифры).
"""
import logging

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from states import Form
from keyboards import (
    card_flow_kb,
    ANOTHER_CARD_TEXT,
    get_menu_keyboard,
    get_card_bank_keyboard,
    get_card_action_keyboard,
    get_card_block_confirm_keyboard,
    get_ecards_group_keyboard,
)

# Все состояния флоу "Действия с картами" (для StateFilter)
CARD_STATES = (
    Form.card_actions_choose_bank,
    Form.card_actions_enter_number,
    Form.card_actions_choose_action,
    Form.card_actions_enter_limit,
    Form.card_actions_confirm_block,
)
from utils import last_messages, delete_last_messages
import services.adscard as adscard
import services.multicards as multicards
import services.ecards as ecards

logger = logging.getLogger(__name__)

router = Router()

MAX_TRANSACTIONS = 10
ADSCARD_TRANSACTIONS_TIME = "month"

# Сколько последних операций по группе показывать (API отдаёт до 100/страницу;
# бьём вывод на сообщения, т.к. лимит Telegram ~4096 символов).
ECARDS_GROUP_TX_LIMIT = 50
ECARDS_TX_CHUNK = 20

BANK_LABELS = {"adscard": "AdsCard", "multicards": "MultiCards", "ecards": "eCards"}

# Метки статусов карт по банкам (у eCards имена статусов из API.md неизвестны —
# показываем как есть).
STATUS_LABELS = {
    "adscard": {"A": "🟢 Активна", "D": "🔴 Заблокирована"},
    "multicards": {"ACTIVE": "🟢 Активна", "FROZEN": "🔵 Заморожена", "CLOSED": "🔴 Закрыта"},
    "ecards": {},
}

# Диапазоны лимитов для валидации ввода (kind -> (min, max|None))
LIMIT_RANGES = {
    "adscard": (0, None),
    "total": (0, 100000),
    "daily": (0, 10000),
}

LIMIT_PROMPTS = {
    "adscard": "💵 Введите новый лимит карты (число):",
    "total": "💵 Введите новый глобальный лимит (число, до 100000):",
    "daily": "📅 Введите новый дневной лимит (число, до 10000):",
}


# --------------------------------------------------------------------------- #
# Хелперы
# --------------------------------------------------------------------------- #
def mask_card_number(number) -> str:
    """Маскирует номер карты, оставляя последние 4 цифры."""
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    if len(digits) < 4:
        return "****"
    return f"**** **** **** {digits[-4:]}"


def _digits(value) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _is_error(result) -> bool:
    """Признак ошибочного ответа сервиса (успех card/list MultiCards — список)."""
    return isinstance(result, dict) and (bool(result.get("error")) or result.get("success") is False)


def _first_card(result: dict) -> dict | None:
    """Первая карта из ответа AdsCard вида data: {"0": {...карта...}}."""
    data = result.get("data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        return None
    for value in data.values():
        if isinstance(value, dict) and any(k in value for k in ("id", "number", "status")):
            return value
    return None


def get_card_id(bank: str, card: dict):
    """ID карты (adscard/multicards: id; ecards: толерантно)."""
    if bank == "ecards":
        return ecards.card_id(card)
    return card.get("id")


def get_card_number(bank: str, card: dict):
    """Полный номер карты (adscard: number, multicards/ecards: cardNumber)."""
    if bank == "ecards":
        return ecards.card_number(card)
    return card.get("number") if bank == "adscard" else card.get("cardNumber")


def _has(value) -> bool:
    """Есть ли осмысленное значение (не None и не пустая строка). 0 — есть."""
    return value not in (None, "")


def _is_zeroish(value) -> bool:
    """True, если значение численно равно нулю (в т.ч. '0.00')."""
    try:
        return float(str(value)) == 0
    except (TypeError, ValueError):
        return False


def format_card_summary(bank: str, card: dict) -> str:
    """Карточка для показа. Поля без значения (None/пусто) пропускаются.

    Чувствительные данные (CVC, полный номер) не выводятся.
    """
    currency = str(card.get("currency", "")).upper()
    cur = f" {currency}" if currency else ""
    lines = ["💳 <b>Карта найдена</b>", f"Банк: {BANK_LABELS.get(bank, bank)}"]

    lines.append(f"Номер: <code>{mask_card_number(get_card_number(bank, card))}</code>")

    status_raw = card.get("status")
    if _has(status_raw):
        lines.append(f"Статус: {STATUS_LABELS.get(bank, {}).get(str(status_raw), status_raw)}")

    def money(label, value, suffix=cur):
        if _has(value):
            lines.append(f"{label}: <b>{value}</b>{suffix}")

    def text(label, value):
        if _has(value):
            lines.append(f"{label}: {value}")

    if bank == "adscard":
        money("Лимит", card.get("limit"))
        money("Баланс", card.get("balance"))
        money("Потрачено", card.get("expense"))
        money("Банковский лимит", card.get("bank_limit"))
        text("Действует до", card.get("date_expired"))
        text("Владелец", card.get("card_user_email"))
        text("Комментарий", card.get("comment"))
    elif bank == "ecards":
        # По требованию показываем только «Использовано». Чувствительные поля
        # (CVC, 3DS-пароль/OTP, token, PAN, email владельца) не выводим.
        money("Использовано", card.get("sharedBalanceUsed"))
    else:
        money("Глобальный лимит", card.get("limitAmount"))
        money("Дневной лимит", card.get("dailyLimitAmount"))
        money("Баланс", card.get("balanceAmount"))
        money("Потрачено всего", card.get("spendAmount"))
        money("Потрачено за день", card.get("dailySpendAmount"))
        money("Возвраты", card.get("refundAmount"))
        if not _is_zeroish(card.get("overdraftAmount")):
            money("Овердрафт", card.get("overdraftAmount"))
        exp_m, exp_y = card.get("cardExpiryMonth"), card.get("cardExpiryYear")
        if _has(exp_m) and _has(exp_y):
            lines.append(f"Действует до: {int(exp_m):02d}/{exp_y}")
        if card.get("autoRefillEnabled"):
            thr, amt = card.get("autoRefillThreshold"), card.get("autoRefillAmount")
            extra = f" (при ≤ {thr} пополнять на {amt})" if _has(thr) and _has(amt) else ""
            lines.append(f"Автопополнение: вкл{extra}")
        group = card.get("cardGroup") or {}
        if isinstance(group, dict):
            text("Группа", group.get("name"))
        owner = card.get("owner") or {}
        if isinstance(owner, dict):
            text("Владелец", owner.get("email"))
        text("Заметка", card.get("note"))
    return "\n".join(lines)


async def _find_card(bank: str, number: str):
    if bank == "adscard":
        return await adscard.find_card_by_number(number)
    if bank == "ecards":
        return await ecards.find_card_by_number(number)
    return await multicards.find_card_by_number(number)


async def _show_action_menu(target, user_id: int, state: FSMContext, bank: str, card_number) -> None:
    """Показывает меню действий по выбранной карте и возвращает в состояние выбора.

    Карта (bank/card_id/card_number) остаётся в state — можно сделать ещё одно
    действие без повторного поиска.
    """
    masked = mask_card_number(card_number)
    m1 = await target.answer(
        f"💳 Карта <code>{masked}</code> ({BANK_LABELS.get(bank, bank)})\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=get_card_action_keyboard(bank),
    )
    m2 = await target.answer("❌ Нажмите 'Отмена', чтобы выйти", reply_markup=card_flow_kb)
    last_messages[user_id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.card_actions_choose_action)


# --------------------------------------------------------------------------- #
# Шаг 1. Запуск — выбор банка
# --------------------------------------------------------------------------- #
@router.message(F.text == "💳 Действия с картами")
async def start_card_actions(message: Message, state: FSMContext):
    """Начинает флоу действий с картами — предлагает выбрать банк."""
    m1 = await message.answer(
        "💳 <b>Действия с картами</b>\n\nВыберите банк:",
        parse_mode="HTML",
        reply_markup=get_card_bank_keyboard(),
    )
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=card_flow_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.card_actions_choose_bank)


# --------------------------------------------------------------------------- #
# Шаг 2. Выбор банка
# --------------------------------------------------------------------------- #
@router.message(StateFilter(*CARD_STATES), F.text == ANOTHER_CARD_TEXT)
async def card_actions_another(message: Message, state: FSMContext):
    """Кнопка «Другая карта» — сбрасывает выбранную карту и возвращает к выбору банка.

    Зарегистрирован раньше обработчиков ввода (номер/лимит), чтобы текст кнопки
    не воспринимался как ввод. Фильтр по состояниям флоу — чтобы не срабатывать вне него.
    """
    await delete_last_messages(message.from_user.id, message.bot)
    await state.set_data({})  # очищаем bank/card_id/card_number
    m1 = await message.answer(
        "💳 <b>Действия с картами</b>\n\nВыберите банк:",
        parse_mode="HTML",
        reply_markup=get_card_bank_keyboard(),
    )
    m2 = await message.answer("❌ Отмена  /  🔄 Другая карта", reply_markup=card_flow_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.card_actions_choose_bank)


@router.callback_query(F.data.startswith("card_bank:"), Form.card_actions_choose_bank)
async def card_bank_selected(query: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор банка."""
    bank = query.data.split(":", 1)[1]
    if bank not in BANK_LABELS:
        await query.answer("❌ Неизвестный банк", show_alert=True)
        return

    await state.update_data(bank=bank)
    await query.answer()
    await delete_last_messages(query.from_user.id, query.message.bot)

    # eCards: сверху действия по картам байера (по его группе), плюс ввод номера
    # для действий по конкретной карте. Остаёмся в состоянии ввода номера —
    # набранный текст обрабатывается как номер, кнопки — как колбэки card_group:*.
    if bank == "ecards":
        m1 = await query.message.answer(
            "🏦 <b>eCards</b>\n\nВыберите действие по вашим картам "
            "или введите полный номер карты для действий с картой:",
            parse_mode="HTML",
            reply_markup=get_ecards_group_keyboard(),
        )
        m2 = await query.message.answer("Или введите номер карты:", reply_markup=card_flow_kb)
        last_messages[query.from_user.id] = [m1.message_id, m2.message_id]
        await state.set_state(Form.card_actions_enter_number)
        return

    m1 = await query.message.answer(
        f"🏦 <b>{BANK_LABELS[bank]}</b>\n\nВведите полный номер карты:",
        parse_mode="HTML",
        reply_markup=card_flow_kb,
    )
    last_messages[query.from_user.id] = [m1.message_id]
    await state.set_state(Form.card_actions_enter_number)


# --------------------------------------------------------------------------- #
# eCards: действия по картам байера (технически по его группе, tg_id в названии)
# --------------------------------------------------------------------------- #
async def _ecards_show_group_menu(target, user_id: int, state: FSMContext) -> None:
    """Пере-показывает меню действий eCards (расход/транзакции) + ввод номера."""
    m1 = await target.answer(
        "🏦 <b>eCards</b>\n\nВыберите действие или введите номер карты:",
        parse_mode="HTML",
        reply_markup=get_ecards_group_keyboard(),
    )
    m2 = await target.answer("Или введите номер карты:", reply_markup=card_flow_kb)
    last_messages[user_id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.card_actions_enter_number)


async def _resolve_buyer_groups(query: CallbackQuery, state: FSMContext):
    """Группы байера по tg_id. При ошибке/пустоте сообщает и пере-показывает меню.

    Возвращает список пар (id, name) либо None (уже обработано).
    """
    groups = await ecards.get_buyer_groups(query.from_user.id)
    if _is_error(groups):
        await query.message.answer("❌ Не удалось получить список групп. Попробуйте позже.")
        await _ecards_show_group_menu(query.message, query.from_user.id, state)
        return None

    my_groups = [(ecards.group_id(g), ecards.group_name(g) or f"Группа {ecards.group_id(g)}")
                 for g in groups if ecards.group_id(g) is not None]
    if not my_groups:
        await query.message.answer(
            "📭 Для вас не найдено групп карт. Обратитесь к администратору "
            "(в названии группы должен быть ваш Telegram-ID)."
        )
        await _ecards_show_group_menu(query.message, query.from_user.id, state)
        return None
    return my_groups


@router.callback_query(F.data == "card_group:spend", Form.card_actions_enter_number)
async def ecards_group_spend(query: CallbackQuery, state: FSMContext):
    """Нетто-расход по картам байера за текущий месяц."""
    await query.answer()
    await delete_last_messages(query.from_user.id, query.message.bot)
    progress = await query.message.answer("🔄 Считаю расход за текущий месяц...")

    my_groups = await _resolve_buyer_groups(query, state)
    if my_groups is None:
        try:
            await progress.delete()
        except Exception:
            pass
        return

    start, end = ecards.current_month_period()
    operations = []
    failed = False
    for gid, _ in my_groups:
        result = await ecards.get_all_group_operations(gid, start, end)
        if _is_error(result):
            failed = True
            break
        operations.extend(result if isinstance(result, list) else [])

    try:
        await progress.delete()
    except Exception:
        pass

    if failed:
        await query.message.answer("❌ Не удалось получить операции. Попробуйте позже.")
        await _ecards_show_group_menu(query.message, query.from_user.id, state)
        return

    totals = ecards.sum_spend_by_currency(operations)
    period = f"{start[:10]} — {end[:10]}"
    lines = [
        "💸 <b>Расход по вашим картам за текущий период</b>",
        f"Период: {period}",
        "",
    ]
    if not totals:
        lines.append("Расход за период отсутствует.")
    else:
        for currency, amount in sorted(totals.items()):
            lines.append(f"<b>{round(amount, 2)}</b> {currency}")

    await query.message.answer("\n".join(lines), parse_mode="HTML")
    await _ecards_show_group_menu(query.message, query.from_user.id, state)


@router.callback_query(F.data == "card_group:transactions", Form.card_actions_enter_number)
async def ecards_group_transactions(query: CallbackQuery, state: FSMContext):
    """Последние операции по картам байера за текущий месяц (до ECARDS_GROUP_TX_LIMIT)."""
    await query.answer()
    await delete_last_messages(query.from_user.id, query.message.bot)
    progress = await query.message.answer("🔄 Загружаю транзакции...")

    my_groups = await _resolve_buyer_groups(query, state)
    if my_groups is None:
        try:
            await progress.delete()
        except Exception:
            pass
        return

    start, end = ecards.current_month_period()
    operations = []
    failed = False
    for gid, _ in my_groups:
        result = await ecards.get_card_operations(
            start, end, group_ids=[gid], limit=ECARDS_GROUP_TX_LIMIT
        )
        if _is_error(result):
            failed = True
            break
        operations.extend(ecards._as_list(result))

    try:
        await progress.delete()
    except Exception:
        pass

    if failed:
        await query.message.answer("❌ Не удалось получить транзакции. Попробуйте позже.")
        await _ecards_show_group_menu(query.message, query.from_user.id, state)
        return

    # Сортируем по дате убыв. и берём последние N.
    operations.sort(key=lambda o: str(ecards.op_date(o) or ""), reverse=True)
    operations = operations[:ECARDS_GROUP_TX_LIMIT]

    if not operations:
        await query.message.answer("📭 Транзакций по вашим картам за период не найдено.")
        await _ecards_show_group_menu(query.message, query.from_user.id, state)
        return

    blocks = [_format_transaction("ecards", i, tx) for i, tx in enumerate(operations, 1)]
    # Бьём на сообщения по ECARDS_TX_CHUNK, чтобы не упереться в лимит Telegram.
    for start_idx in range(0, len(blocks), ECARDS_TX_CHUNK):
        chunk = blocks[start_idx:start_idx + ECARDS_TX_CHUNK]
        header = "📜 <b>Последние транзакции</b>\n\n" if start_idx == 0 else ""
        await query.message.answer(header + "\n".join(chunk), parse_mode="HTML")

    await _ecards_show_group_menu(query.message, query.from_user.id, state)


# --------------------------------------------------------------------------- #
# Шаг 3. Ввод номера карты и поиск
# --------------------------------------------------------------------------- #
@router.message(Form.card_actions_enter_number)
async def card_number_entered(message: Message, state: FSMContext):
    """Ищет карту по введённому полному номеру."""
    # "❌ Отмена" перехватывается глобальным обработчиком в common.py
    number = message.text.strip()
    if not any(ch.isdigit() for ch in number):
        await message.answer("❌ Введите номер карты (цифры).", reply_markup=card_flow_kb)
        return

    data = await state.get_data()
    bank = data.get("bank", "adscard")

    progress = await message.answer("🔄 Ищу карту...")
    result = await _find_card(bank, number)

    try:
        await progress.delete()
    except Exception:
        pass

    if isinstance(result, dict) and result.get("error"):
        await message.answer(
            f"❌ Не удалось получить список карт {BANK_LABELS.get(bank, bank)}. "
            "Попробуйте позже.",
            reply_markup=card_flow_kb,
        )
        return

    if not result:
        await message.answer(
            "❌ Карта с таким номером не найдена. Проверьте номер и попробуйте снова.",
            reply_markup=card_flow_kb,
        )
        return

    card = result["card"]
    await delete_last_messages(message.from_user.id, message.bot)
    await state.update_data(card_id=get_card_id(bank, card), card_number=get_card_number(bank, card))

    text = format_card_summary(bank, card)
    if result.get("multiple"):
        text += "\n\n⚠️ Найдено несколько карт, показана первая."

    m1 = await message.answer(text, parse_mode="HTML", reply_markup=get_card_action_keyboard(bank))
    m2 = await message.answer("❌ Нажмите 'Отмена', чтобы выйти", reply_markup=card_flow_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.card_actions_choose_action)


# --------------------------------------------------------------------------- #
# Шаг 4. Выбор действия
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("card_action:"), Form.card_actions_choose_action)
async def card_action_selected(query: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор действия с картой."""
    action = query.data.split(":", 1)[1]
    data = await state.get_data()
    bank = data.get("bank", "adscard")
    card_id = data.get("card_id")

    if card_id is None:
        await query.answer()
        await query.message.answer(
            "❌ Карта не выбрана. Начните заново.",
            reply_markup=get_menu_keyboard(query.from_user.id),
        )
        await state.clear()
        return

    if action in ("limit", "limit_total", "limit_daily"):
        kind = {"limit": "adscard", "limit_total": "total", "limit_daily": "daily"}[action]
        await state.update_data(limit_kind=kind)
        await query.answer()
        await delete_last_messages(query.from_user.id, query.message.bot)
        m1 = await query.message.answer(LIMIT_PROMPTS[kind], reply_markup=card_flow_kb)
        last_messages[query.from_user.id] = [m1.message_id]
        await state.set_state(Form.card_actions_enter_limit)

    elif action == "block":
        await query.answer()
        await delete_last_messages(query.from_user.id, query.message.bot)
        m1 = await query.message.answer(
            f"🚫 Заблокировать карту <code>{mask_card_number(data.get('card_number'))}</code>?\n"
            "Действие необратимо.",
            parse_mode="HTML",
            reply_markup=get_card_block_confirm_keyboard(),
        )
        last_messages[query.from_user.id] = [m1.message_id]
        await state.set_state(Form.card_actions_confirm_block)

    elif action == "transactions":
        await query.answer()
        await _show_transactions(query, state, bank, card_id, data.get("card_number"))

    else:
        await query.answer("❌ Неизвестное действие", show_alert=True)


# --------------------------------------------------------------------------- #
# Транзакции
# --------------------------------------------------------------------------- #
async def _show_transactions(query: CallbackQuery, state: FSMContext, bank: str, card_id, card_number):
    """Загружает транзакции и выводит последние по выбранной карте."""
    await delete_last_messages(query.from_user.id, query.message.bot)
    progress = await query.message.answer("🔄 Загружаю транзакции...")

    if bank == "adscard":
        result = await adscard.get_team_transactions(ADSCARD_TRANSACTIONS_TIME)
    elif bank == "ecards":
        start, end = ecards.current_month_period()
        result = await ecards.get_card_operations(start, end, card_ids=[card_id])
    else:
        start, end = multicards.current_month_period()
        result = await multicards.get_transactions(start, end)

    try:
        await progress.delete()
    except Exception:
        pass

    if _is_error(result):
        await query.message.answer(
            f"❌ Не удалось получить транзакции {BANK_LABELS.get(bank, bank)}. "
            "Попробуйте позже.",
        )
        await _show_action_menu(query.message, query.from_user.id, state, bank, card_number)
        return

    transactions = _extract_transactions(bank, result)
    transactions = [tx for tx in transactions if _tx_matches(bank, tx, card_id, card_number)]

    if not transactions:
        await query.message.answer("📭 Транзакций по карте за период не найдено.")
        await _show_action_menu(query.message, query.from_user.id, state, bank, card_number)
        return

    lines = ["📜 <b>Последние транзакции</b>\n"]
    for i, tx in enumerate(transactions[:MAX_TRANSACTIONS], 1):
        lines.append(_format_transaction(bank, i, tx))

    await query.message.answer("\n".join(lines), parse_mode="HTML")
    await _show_action_menu(query.message, query.from_user.id, state, bank, card_number)


def _extract_transactions(bank: str, result) -> list:
    if bank == "adscard":
        data = result.get("data", {})
        return list(data.values()) if isinstance(data, dict) else (data or [])
    if bank == "ecards":
        return ecards._as_list(result)
    items = result.get("items", []) if isinstance(result, dict) else []
    return items or []


def _tx_matches(bank: str, tx: dict, card_id, card_number) -> bool:
    """Транзакция относится к выбранной карте — по id, иначе по последним 4 цифрам."""
    if bank == "ecards":
        # eCards уже фильтрует по filterCardId на стороне API, но сверимся.
        # Карта во вложенном объекте операции (op.card.{id,cardNumber}).
        if card_id is not None:
            tx_id = ecards.op_card_id(tx)
            if tx_id is not None:
                return str(tx_id) == str(card_id)
        last4 = _digits(card_number)[-4:]
        tx_digits = _digits(ecards.op_card_number(tx))
        return bool(last4) and bool(tx_digits) and tx_digits[-4:] == last4

    id_field = "card_id" if bank == "adscard" else "cardId"
    num_field = "card_number" if bank == "adscard" else "cardNumber"

    if card_id is not None and tx.get(id_field) is not None:
        return str(tx.get(id_field)) == str(card_id)

    last4 = _digits(card_number)[-4:]
    tx_digits = _digits(tx.get(num_field))
    return bool(last4) and bool(tx_digits) and tx_digits[-4:] == last4


def _pretty_dt(value) -> str:
    """ISO 8601 -> 'YYYY-MM-DD HH:MM' (для MultiCards); иначе как есть."""
    s = str(value or "")
    if "T" in s:
        return s[:16].replace("T", " ")
    return s


def _format_transaction(bank: str, i: int, tx: dict) -> str:
    """Подробная строка транзакции; пустые/нулевые поля пропускаются."""
    currency = str(tx.get("currency", "")).upper()
    cur = f" {currency}" if currency else ""
    amount = tx.get("amount")

    if bank == "ecards":
        # Поля операции из живого /card-operation (сумма в value, валюта у карты).
        currency = str(ecards.op_currency(tx) or "").upper()
        cur = f" {currency}" if currency else ""
        amount = ecards.op_value(tx)
        date = _pretty_dt(ecards.op_date(tx))
        tx_type = ecards.op_type(tx) or "—"
        head = tx_type
        merchant = ecards.op_merchant(tx)
        fee = None
        balance = None
        note = None
    elif bank == "adscard":
        date = tx.get("date", "")
        tx_type = tx.get("type") or "—"
        head = tx_type
        merchant = tx.get("merchant")
        fee = tx.get("fee")
        balance = tx.get("userbalance")
        note = tx.get("card_comment")
    else:
        date = _pretty_dt(tx.get("createdAt"))
        tx_type = tx.get("type") or "—"
        status = tx.get("status")
        head = f"{tx_type} · {status}" if _has(status) else tx_type
        merchant = tx.get("description")
        # У MultiCards комиссия = процент + фиксированная
        fee_parts = []
        if not _is_zeroish(tx.get("transactionFee")):
            fee_parts.append(f"{tx.get('transactionFee')}%")
        if not _is_zeroish(tx.get("transactionFixedFee")):
            fee_parts.append(f"{tx.get('transactionFixedFee')}{cur}")
        fee = " + ".join(fee_parts) if fee_parts else None
        balance = None
        note = tx.get("cardNote")

    lines = [f"<b>#{i}</b> {date}".rstrip()]
    lines.append(f"   {head} — <b>{amount if _has(amount) else '—'}</b>{cur}")
    if bank == "adscard":
        if _has(fee) and not _is_zeroish(fee):
            lines.append(f"   Комиссия: {fee}{cur}")
    elif _has(fee):
        lines.append(f"   Комиссия: {fee}")
    if _has(merchant):
        lines.append(f"   🏬 {merchant}")
    if _has(note):
        lines.append(f"   📝 {note}")
    if _has(balance):
        lines.append(f"   Баланс после: {balance}{cur}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Шаг 5. Ввод нового лимита
# --------------------------------------------------------------------------- #
@router.message(Form.card_actions_enter_limit)
async def card_limit_entered(message: Message, state: FSMContext):
    """Применяет новый лимит карты."""
    # "❌ Отмена" перехватывается глобальным обработчиком в common.py
    data = await state.get_data()
    bank = data.get("bank", "adscard")
    card_id = data.get("card_id")
    card_number = data.get("card_number")
    kind = data.get("limit_kind", "adscard")
    menu_kb = get_menu_keyboard(message.from_user.id)

    if card_id is None:
        await state.clear()
        await message.answer("❌ Карта не выбрана. Начните заново.", reply_markup=menu_kb)
        return

    low, high = LIMIT_RANGES.get(kind, (0, None))
    raw = message.text.strip().replace(",", ".")
    try:
        value = float(raw)
        if value < low or (high is not None and value > high):
            raise ValueError()
    except ValueError:
        limit_hint = f" (от {low} до {high})" if high is not None else f" (≥ {low})"
        await message.answer(f"❌ Введите корректное число{limit_hint}.", reply_markup=card_flow_kb)
        return

    limit = int(value) if value.is_integer() else value

    await delete_last_messages(message.from_user.id, message.bot)
    progress = await message.answer("🔄 Меняю лимит...")

    if bank == "adscard":
        result = await adscard.set_card_limit(card_id, limit)
    elif kind == "total":
        result = await multicards.set_total_limit(card_id, limit)
    else:
        result = await multicards.set_daily_limit(card_id, limit)

    try:
        await progress.delete()
    except Exception:
        pass

    if _is_error(result):
        await message.answer("❌ Не удалось изменить лимит. Попробуйте позже.")
    else:
        applied = _applied_limit(bank, kind, result, limit)
        kind_label = {"adscard": "Лимит", "total": "Глобальный лимит", "daily": "Дневной лимит"}[kind]
        await message.answer(
            f"✅ {kind_label} карты <code>{mask_card_number(card_number)}</code> "
            f"изменён на <b>{applied}</b>.",
            parse_mode="HTML",
        )

    await _show_action_menu(message, message.from_user.id, state, bank, card_number)


def _applied_limit(bank: str, kind: str, result, fallback):
    """Фактический лимит из ответа API, иначе — введённое значение."""
    if bank == "adscard":
        card = _first_card(result)
        if card and card.get("limit") is not None:
            return card.get("limit")
    elif isinstance(result, dict):
        field = "limitAmount" if kind == "total" else "dailyLimitAmount"
        if result.get(field) is not None:
            return result.get(field)
    return fallback


# --------------------------------------------------------------------------- #
# Шаг 6. Подтверждение блокировки
# --------------------------------------------------------------------------- #
@router.callback_query(F.data.startswith("card_block_confirm:"), Form.card_actions_confirm_block)
async def card_block_confirmed(query: CallbackQuery, state: FSMContext):
    """Обрабатывает подтверждение/отмену блокировки карты."""
    choice = query.data.split(":", 1)[1]
    data = await state.get_data()
    bank = data.get("bank", "adscard")
    card_id = data.get("card_id")
    card_number = data.get("card_number")
    menu_kb = get_menu_keyboard(query.from_user.id)

    await query.answer()
    await delete_last_messages(query.from_user.id, query.message.bot)

    if card_id is None:
        await query.message.answer("❌ Карта не выбрана. Начните заново.", reply_markup=menu_kb)
        await state.clear()
        return

    masked = mask_card_number(card_number)

    if choice != "yes":
        await query.message.answer("Блокировка отменена.")
        await _show_action_menu(query.message, query.from_user.id, state, bank, card_number)
        return

    progress = await query.message.answer("🔄 Блокирую карту...")
    if bank == "adscard":
        result = await adscard.block_card(card_id)
    elif bank == "ecards":
        result = await ecards.block_card(card_id)
    else:
        result = await multicards.block_card(card_id)

    try:
        await progress.delete()
    except Exception:
        pass

    if _is_error(result):
        await query.message.answer("❌ Не удалось заблокировать карту. Попробуйте позже.")
    elif not _block_confirmed(bank, result):
        await query.message.answer(
            f"⚠️ Запрос отправлен, но карта <code>{masked}</code> не выглядит "
            "заблокированной. Проверьте вручную.",
            parse_mode="HTML",
        )
    else:
        await query.message.answer(
            f"✅ Карта <code>{masked}</code> заблокирована.",
            parse_mode="HTML",
        )

    await _show_action_menu(query.message, query.from_user.id, state, bank, card_number)


def _block_confirmed(bank: str, result) -> bool:
    """Подтверждение блокировки по ответу API.

    AdsCard: closed_at заполнен или status == "D".
    MultiCards: статус карты в ответе != ACTIVE.
    Если в ответе нет карты — считаем успехом (отсутствие ошибки уже проверено).
    """
    if bank == "adscard":
        card = _first_card(result)
        if card is None:
            return True
        return bool(card.get("closed_at") or str(card.get("status")) == "D")

    if bank == "ecards":
        # Форма ответа /card/close в API.md не описана; отсутствие ошибки уже
        # проверено выше — считаем блокировку принятой.
        return True

    if isinstance(result, dict) and result.get("status"):
        return str(result.get("status")) != "ACTIVE"
    return True
