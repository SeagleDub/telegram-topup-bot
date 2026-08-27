"""
Обработчики для системы пополнения баланса
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from states import Form
from keyboards import get_bank_keyboard, get_topup_type_keyboard, get_admin_action_keyboard, cancel_kb, get_menu_keyboard
from utils import last_messages, delete_last_messages, send_notification_with_buttons

router = Router()

@router.message(F.text == "💰 Заказать пополнение")
async def order_topup(message: Message, state: FSMContext):
    """Начинает процесс заказа пополнения"""

    kb = get_bank_keyboard()
    m1 = await message.answer("Выберите банк:", reply_markup=kb)
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.waiting_for_bank)

@router.callback_query(F.data.startswith("bank:"), Form.waiting_for_bank)
async def bank_selected(query: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор банка"""
    await delete_last_messages(query.from_user.id, query.message.bot)
    _, bank = query.data.split(":")

    if bank == "trafficcards_inactive":
        await query.message.answer("❌ Traffic.cards временно недоступен. Пожалуйста, выберите другой вариант.", reply_markup=cancel_kb)
        await query.answer()
        return

    await state.update_data(bank=bank)
    msg = await query.message.answer("Введите сумму пополнения:", reply_markup=cancel_kb)
    last_messages[query.from_user.id] = [msg.message_id]
    await state.set_state(Form.waiting_for_amount)
    await query.answer()

@router.message(Form.waiting_for_amount)
async def get_amount(message: Message, state: FSMContext):
    """Обрабатывает введенную сумму пополнения"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    amount = message.text.strip()
    if not amount.isdigit():
        await message.answer("Пожалуйста, введите корректную сумму.")
        return

    await delete_last_messages(message.from_user.id, message.bot)
    await state.update_data(amount=amount)

    kb = get_topup_type_keyboard()
    m1 = await message.answer("Выберите тип пополнения:", reply_markup=kb)
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.waiting_for_type)

@router.callback_query(F.data.startswith("type:"), Form.waiting_for_type)
async def type_selected(query: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор типа пополнения и отправляет заявку"""
    await delete_last_messages(query.from_user.id, query.message.bot)
    _, topup_type = query.data.split(":")
    await state.update_data(topup_type=topup_type)

    user_id = query.from_user.id
    username = query.from_user.username or "нет username"
    data = await state.get_data()

    bank = data.get("bank", "не указан")
    amount = data.get("amount", "не указано")
    topup_type_text = "⚡ Срочное" if topup_type == "urgent" else "🕘 Не срочное (до 21:00)"

    # Преобразуем внутреннее название банка в читабельное
    bank_names = {
        "adscard_facebook": "AdsCard (Facebook)",
        "adscard_google": "AdsCard (Google)",
        "trafficcards_inactive": "Traffic.cards (не активно)",
        "multicards_google": "MultiCards (Google)"
    }
    bank_display = bank_names.get(bank, bank)

    kb = get_admin_action_keyboard(user_id)

    await send_notification_with_buttons(
        query.message.bot,
        f"🔔 Новая заявка от @{username} (ID: {user_id})\n"
        f"🏦 Банк: {bank_display}\n"
        f"💳 Сумма: {amount}\n"
        f"📌 Тип: {topup_type_text}",
        reply_markup=kb
    )

    await query.message.answer("Ваша заявка отправлена администратору.", reply_markup=get_menu_keyboard(query.from_user.id))
    await state.clear()
    await query.answer()
