"""
Обработчики для функции "Действия с картами".

Поток: выбор банка -> ввод номера карты -> выбор действия
(поменять лимит / заблокировать / последние транзакции).

Сейчас реализован только банк AdsCard. MultiCards — заглушка ("скоро").
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
from services.adscard import (
    find_card_by_number,
    set_card_limit,
    block_card,
    get_team_transactions,
    card_digits,
)

logger = logging.getLogger(__name__)

router = Router()

# Период для выборки транзакций и максимум выводимых записей
TRANSACTIONS_TIME = "month"
MAX_TRANSACTIONS = 10

# Человекочитаемые статусы карт AdsCard
CARD_STATUS_LABELS = {
    "A": "🟢 Активна",
    "D": "🔴 Заблокирована",
}


# TODO(diagnostic): временный вывод технических деталей ошибки пользователю.
# Убрать после диагностики проблемы с AdsCard (вернуть нейтральные сообщения).
def _error_detail(result: dict) -> str:
    """Достаёт человекочитаемую деталь ошибки из ответа сервиса."""
    if not isinstance(result, dict):
        return ""
    detail = result.get("details") or result.get("error") or ""
    return f"\n\n🛠 {detail}" if detail else ""


def mask_card_number(number) -> str:
    """Маскирует номер карты, оставляя последние 4 цифры."""
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    if len(digits) < 4:
        return "****"
    return f"**** **** **** {digits[-4:]}"


def format_card_summary(card: dict) -> str:
    """Формирует краткую карточку для показа пользователю (без чувствительных данных)."""
    status = CARD_STATUS_LABELS.get(str(card.get("status")), str(card.get("status", "—")))
    currency = str(card.get("currency", "")).upper()
    limit = card.get("limit", "—")
    balance = card.get("balance", "—")
    comment = card.get("comment")

    lines = [
        "💳 <b>Карта найдена</b>",
        f"Номер: <code>{mask_card_number(card.get('number'))}</code>",
        f"Статус: {status}",
        f"Лимит: <b>{limit}</b> {currency}",
        f"Баланс: <b>{balance}</b> {currency}",
    ]
    if comment:
        lines.append(f"Комментарий: {comment}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Шаг 1. Запуск — выбор банка
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Шаг 2. Выбор банка
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("card_bank:"), Form.card_actions_choose_bank)
async def card_bank_selected(query: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор банка."""
    bank = query.data.split(":", 1)[1]

    if bank == "multicards":
        await query.answer("MultiCards временно недоступен", show_alert=True)
        return

    if bank != "adscard":
        await query.answer("❌ Неизвестный банк", show_alert=True)
        return

    await state.update_data(bank=bank)
    await query.answer()
    await delete_last_messages(query.from_user.id, query.message.bot)

    m1 = await query.message.answer(
        "🏦 <b>AdsCard</b>\n\nВведите полный номер карты:",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )
    last_messages[query.from_user.id] = [m1.message_id]
    await state.set_state(Form.card_actions_enter_number)


# ---------------------------------------------------------------------------
# Шаг 3. Ввод номера карты и поиск
# ---------------------------------------------------------------------------
@router.message(Form.card_actions_enter_number)
async def card_number_entered(message: Message, state: FSMContext):
    """Ищет карту по введённому номеру."""
    # "❌ Отмена" перехватывается глобальным обработчиком в common.py
    number = message.text.strip()
    if not any(ch.isdigit() for ch in number):
        await message.answer("❌ Введите номер карты (цифры).", reply_markup=cancel_kb)
        return

    progress = await message.answer("🔄 Ищу карту...")
    result = await find_card_by_number(number)

    try:
        await progress.delete()
    except Exception:
        pass

    if isinstance(result, dict) and result.get("error"):
        await message.answer(
            "❌ Не удалось получить список карт AdsCard. Попробуйте позже." + _error_detail(result),
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
    await state.update_data(card_id=card.get("id"), card_number=card.get("number"))

    text = format_card_summary(card)
    if result.get("multiple"):
        text += "\n\n⚠️ Найдено несколько карт, показана первая."

    m1 = await message.answer(text, parse_mode="HTML", reply_markup=get_card_action_keyboard())
    m2 = await message.answer("❌ Нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.card_actions_choose_action)


# ---------------------------------------------------------------------------
# Шаг 4. Выбор действия
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("card_action:"), Form.card_actions_choose_action)
async def card_action_selected(query: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор действия с картой."""
    action = query.data.split(":", 1)[1]
    data = await state.get_data()
    card_id = data.get("card_id")

    if card_id is None:
        await query.answer()
        await query.message.answer(
            "❌ Карта не выбрана. Начните заново.",
            reply_markup=get_menu_keyboard(query.from_user.id),
        )
        await state.clear()
        return

    if action == "limit":
        await query.answer()
        await delete_last_messages(query.from_user.id, query.message.bot)
        m1 = await query.message.answer(
            "💵 Введите новый лимит карты (число):",
            reply_markup=cancel_kb,
        )
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
        await _show_transactions(query, state)

    else:
        await query.answer("❌ Неизвестное действие", show_alert=True)


async def _show_transactions(query: CallbackQuery, state: FSMContext):
    """Загружает транзакции команды и выводит последние по выбранной карте.

    teams/cards_transactions не принимает card_id, поэтому фильтруем результат
    по номеру выбранной карты (по последним 4 цифрам) на стороне бота.
    """
    data = await state.get_data()
    card_number = data.get("card_number")

    await delete_last_messages(query.from_user.id, query.message.bot)
    progress = await query.message.answer("🔄 Загружаю транзакции...")

    result = await get_team_transactions(TRANSACTIONS_TIME)

    try:
        await progress.delete()
    except Exception:
        pass

    menu_kb = get_menu_keyboard(query.from_user.id)

    if result.get("error") or result.get("success") is False:
        await query.message.answer(
            "❌ Не удалось получить транзакции AdsCard. Попробуйте позже." + _error_detail(result),
            reply_markup=menu_kb,
        )
        await state.clear()
        return

    data_field = result.get("data", {})
    transactions = list(data_field.values()) if isinstance(data_field, dict) else (data_field or [])

    # Фильтр по выбранной карте: совпадение последних 4 цифр номера
    card_last4 = card_digits(card_number)[-4:]
    if card_last4:
        transactions = [
            tx for tx in transactions
            if card_digits(tx.get("card_number")) and card_digits(tx.get("card_number"))[-4:] == card_last4
        ]

    if not transactions:
        await query.message.answer("📭 Транзакций по карте за период не найдено.", reply_markup=menu_kb)
        await state.clear()
        return

    lines = ["📜 <b>Последние транзакции</b>\n"]
    for i, tx in enumerate(transactions[:MAX_TRANSACTIONS], 1):
        date = tx.get("date", "—")
        status = tx.get("status", "—")
        amount = tx.get("amount", "—")
        currency = str(tx.get("currency", "")).upper()
        merchant = tx.get("merchant") or "—"
        lines.append(
            f"<b>#{i}</b> {date}\n"
            f"   {status} — <b>{amount}</b> {currency}\n"
            f"   🏬 {merchant}"
        )

    await query.message.answer("\n".join(lines), parse_mode="HTML", reply_markup=menu_kb)
    await state.clear()


# ---------------------------------------------------------------------------
# Шаг 5. Ввод нового лимита
# ---------------------------------------------------------------------------
@router.message(Form.card_actions_enter_limit)
async def card_limit_entered(message: Message, state: FSMContext):
    """Применяет новый лимит карты."""
    # "❌ Отмена" перехватывается глобальным обработчиком в common.py
    raw = message.text.strip().replace(",", ".")
    try:
        value = float(raw)
        if value < 0:
            raise ValueError()
    except ValueError:
        await message.answer("❌ Введите корректное число (≥ 0).", reply_markup=cancel_kb)
        return

    limit = int(value) if value.is_integer() else value

    data = await state.get_data()
    card_id = data.get("card_id")
    menu_kb = get_menu_keyboard(message.from_user.id)

    if card_id is None:
        await state.clear()
        await message.answer("❌ Карта не выбрана. Начните заново.", reply_markup=menu_kb)
        return

    await delete_last_messages(message.from_user.id, message.bot)
    progress = await message.answer("🔄 Меняю лимит...")
    result = await set_card_limit(card_id, limit)

    try:
        await progress.delete()
    except Exception:
        pass

    if result.get("error") or result.get("success") is False:
        await message.answer(
            "❌ Не удалось изменить лимит. Попробуйте позже." + _error_detail(result),
            reply_markup=menu_kb,
        )
    else:
        await message.answer(
            f"✅ Лимит карты <code>{mask_card_number(data.get('card_number'))}</code> "
            f"изменён на <b>{limit}</b>.",
            parse_mode="HTML",
            reply_markup=menu_kb,
        )

    await state.clear()


# ---------------------------------------------------------------------------
# Шаг 6. Подтверждение блокировки
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("card_block_confirm:"), Form.card_actions_confirm_block)
async def card_block_confirmed(query: CallbackQuery, state: FSMContext):
    """Обрабатывает подтверждение/отмену блокировки карты."""
    choice = query.data.split(":", 1)[1]
    data = await state.get_data()
    card_id = data.get("card_id")
    menu_kb = get_menu_keyboard(query.from_user.id)

    await query.answer()
    await delete_last_messages(query.from_user.id, query.message.bot)

    if choice != "yes":
        await query.message.answer("Блокировка отменена.", reply_markup=menu_kb)
        await state.clear()
        return

    if card_id is None:
        await query.message.answer("❌ Карта не выбрана. Начните заново.", reply_markup=menu_kb)
        await state.clear()
        return

    progress = await query.message.answer("🔄 Блокирую карту...")
    result = await block_card(card_id)

    try:
        await progress.delete()
    except Exception:
        pass

    if result.get("error") or result.get("success") is False:
        await query.message.answer(
            "❌ Не удалось заблокировать карту. Попробуйте позже." + _error_detail(result),
            reply_markup=menu_kb,
        )
    else:
        await query.message.answer(
            f"✅ Карта <code>{mask_card_number(data.get('card_number'))}</code> заблокирована.",
            parse_mode="HTML",
            reply_markup=menu_kb,
        )

    await state.clear()
