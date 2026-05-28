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
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from states import Form
from keyboards import (
    cancel_kb,
    get_menu_keyboard,
    get_card_bank_keyboard,
    get_card_action_keyboard,
    get_card_block_confirm_keyboard,
)
from utils import last_messages, delete_last_messages
import services.adscard as adscard
import services.multicards as multicards

logger = logging.getLogger(__name__)

router = Router()

MAX_TRANSACTIONS = 10
ADSCARD_TRANSACTIONS_TIME = "month"

BANK_LABELS = {"adscard": "AdsCard", "multicards": "MultiCards"}

# Метки статусов карт по банкам
STATUS_LABELS = {
    "adscard": {"A": "🟢 Активна", "D": "🔴 Заблокирована"},
    "multicards": {"ACTIVE": "🟢 Активна", "FROZEN": "🔵 Заморожена", "CLOSED": "🔴 Закрыта"},
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
    """ID карты (у обоих банков поле id)."""
    return card.get("id")


def get_card_number(bank: str, card: dict):
    """Полный номер карты (adscard: number, multicards: cardNumber)."""
    return card.get("number") if bank == "adscard" else card.get("cardNumber")


def format_card_summary(bank: str, card: dict) -> str:
    """Краткая карточка для показа (без чувствительных данных)."""
    status_raw = str(card.get("status"))
    status = STATUS_LABELS.get(bank, {}).get(status_raw, status_raw)
    currency = str(card.get("currency", "")).upper()
    masked = mask_card_number(get_card_number(bank, card))

    lines = [
        "💳 <b>Карта найдена</b>",
        f"Банк: {BANK_LABELS.get(bank, bank)}",
        f"Номер: <code>{masked}</code>",
        f"Статус: {status}",
    ]
    if bank == "adscard":
        limit_type = {"day": " (дневной)", "month": " (месячный)"}.get(str(card.get("limit_type")), "")
        lines.append(f"Лимит: <b>{card.get('limit', '—')}</b> {currency}{limit_type}")
        lines.append(f"Баланс: <b>{card.get('balance', '—')}</b> {currency}")
        if card.get("comment"):
            lines.append(f"Комментарий: {card.get('comment')}")
    else:
        lines.append(f"Баланс: <b>{card.get('balanceAmount', '—')}</b> {currency}")
        lines.append(f"Потрачено всего: <b>{card.get('spendAmount', '—')}</b> {currency}")
        lines.append(f"Потрачено за день: <b>{card.get('dailySpendAmount', '—')}</b> {currency}")
        if card.get("note"):
            lines.append(f"Заметка: {card.get('note')}")
    return "\n".join(lines)


async def _find_card(bank: str, number: str):
    if bank == "adscard":
        return await adscard.find_card_by_number(number)
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
    m2 = await target.answer("❌ Нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
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
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.card_actions_choose_bank)


# --------------------------------------------------------------------------- #
# Шаг 2. Выбор банка
# --------------------------------------------------------------------------- #
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

    m1 = await query.message.answer(
        f"🏦 <b>{BANK_LABELS[bank]}</b>\n\nВведите полный номер карты:",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    last_messages[query.from_user.id] = [m1.message_id]
    await state.set_state(Form.card_actions_enter_number)


# --------------------------------------------------------------------------- #
# Шаг 3. Ввод номера карты и поиск
# --------------------------------------------------------------------------- #
@router.message(Form.card_actions_enter_number)
async def card_number_entered(message: Message, state: FSMContext):
    """Ищет карту по введённому полному номеру."""
    # "❌ Отмена" перехватывается глобальным обработчиком в common.py
    number = message.text.strip()
    if not any(ch.isdigit() for ch in number):
        await message.answer("❌ Введите номер карты (цифры).", reply_markup=cancel_kb)
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
            reply_markup=cancel_kb,
        )
        return

    if not result:
        await message.answer(
            "❌ Карта с таким номером не найдена. Проверьте номер и попробуйте снова.",
            reply_markup=cancel_kb,
        )
        return

    card = result["card"]
    await delete_last_messages(message.from_user.id, message.bot)
    await state.update_data(card_id=get_card_id(bank, card), card_number=get_card_number(bank, card))

    text = format_card_summary(bank, card)
    if result.get("multiple"):
        text += "\n\n⚠️ Найдено несколько карт, показана первая."

    m1 = await message.answer(text, parse_mode="HTML", reply_markup=get_card_action_keyboard(bank))
    m2 = await message.answer("❌ Нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
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
        m1 = await query.message.answer(LIMIT_PROMPTS[kind], reply_markup=cancel_kb)
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
    items = result.get("items", []) if isinstance(result, dict) else []
    return items or []


def _tx_matches(bank: str, tx: dict, card_id, card_number) -> bool:
    """Транзакция относится к выбранной карте — по id, иначе по последним 4 цифрам."""
    id_field = "card_id" if bank == "adscard" else "cardId"
    num_field = "card_number" if bank == "adscard" else "cardNumber"

    if card_id is not None and tx.get(id_field) is not None:
        return str(tx.get(id_field)) == str(card_id)

    last4 = _digits(card_number)[-4:]
    tx_digits = _digits(tx.get(num_field))
    return bool(last4) and bool(tx_digits) and tx_digits[-4:] == last4


def _format_transaction(bank: str, i: int, tx: dict) -> str:
    currency = str(tx.get("currency", "")).upper()
    amount = tx.get("amount", "—")
    tx_type = tx.get("type") or tx.get("status") or "—"
    if bank == "adscard":
        date = tx.get("date", "—")
        merchant = tx.get("merchant") or "—"
    else:
        date = tx.get("createdAt", "—")
        merchant = tx.get("description") or "—"
    return (
        f"<b>#{i}</b> {date}\n"
        f"   {tx_type} — <b>{amount}</b> {currency}\n"
        f"   🏬 {merchant}"
    )


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
        await message.answer(f"❌ Введите корректное число{limit_hint}.", reply_markup=cancel_kb)
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

    if isinstance(result, dict) and result.get("status"):
        return str(result.get("status")) != "ACTIVE"
    return True
