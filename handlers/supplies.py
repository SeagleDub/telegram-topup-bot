"""
Обработчики для системы запроса расходников
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from states import Form
from keyboards import (get_supply_category_keyboard, get_account_type_keyboard,
                       get_admin_processing_keyboard, cancel_kb, get_menu_keyboard)
from utils import last_messages, send_notification_with_buttons

router = Router()

@router.message(F.text == "📂 Запросить расходники")
async def request_supplies(message: Message, state: FSMContext):
    """Начинает процесс запроса расходников"""

    kb = get_supply_category_keyboard()
    m1 = await message.answer("Выберите категорию расходников:", reply_markup=kb)
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.choosing_supply_category)

@router.callback_query(F.data.startswith("supply:"), Form.choosing_supply_category)
async def supply_category_selected(query: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор категории расходников"""
    _, category = query.data.split(":")

    if category == "accounts":
        kb = get_account_type_keyboard()
        await query.message.edit_text("Выберите тип аккаунтов:", reply_markup=kb)
        await state.set_state(Form.choosing_account_type)
    elif category == "domains":
        await query.message.edit_text("Введите количество доменов:")
        await state.set_state(Form.entering_domain_quantity)

    await query.answer()

@router.callback_query(F.data.startswith("account_type:"), Form.choosing_account_type)
async def account_type_selected(query: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор типа аккаунтов"""
    _, account_type = query.data.split(":")
    await state.update_data(account_type=account_type)

    account_types = {
        "tiktok": "TikTok",
        "facebook": "Facebook",
        "google": "Google"
    }

    type_name = account_types.get(account_type, account_type)
    await query.message.edit_text(f"Введите количество аккаунтов {type_name}:")
    await state.set_state(Form.entering_account_quantity)
    await query.answer()

@router.message(Form.entering_account_quantity)
async def get_account_quantity(message: Message, state: FSMContext):
    """Обрабатывает введенное количество аккаунтов"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    quantity = message.text.strip()
    if not quantity.isdigit():
        await message.answer("Пожалуйста, введите корректное количество.")
        return

    data = await state.get_data()
    account_type = data.get("account_type")
    user_id = message.from_user.id
    username = message.from_user.username or "нет username"

    account_types = {
        "tiktok": "TikTok",
        "facebook": "Facebook",
        "google": "Google"
    }

    type_name = account_types.get(account_type, account_type)

    kb = get_admin_processing_keyboard(user_id)

    await send_notification_with_buttons(
        message.bot,
        f"🔔 Новый запрос на расходники от @{username} (ID: {user_id})\n"
        f"📁 Тип: Аккаунты {type_name}\n"
        f"🔢 Количество: {quantity}",
        reply_markup=kb
    )

    await message.answer("Ваша заявка отправлена администратору.", reply_markup=get_menu_keyboard(message.from_user.id))
    await state.clear()

@router.message(Form.entering_domain_quantity)
async def get_domain_quantity(message: Message, state: FSMContext):
    """Обрабатывает введенное количество доменов"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    quantity = message.text.strip()
    if not quantity.isdigit():
        await message.answer("Пожалуйста, введите корректное количество.")
        return

    user_id = message.from_user.id
    username = message.from_user.username or "нет username"

    kb = get_admin_processing_keyboard(user_id)

    await send_notification_with_buttons(
        message.bot,
        f"🔔 Новый запрос на расходники от @{username} (ID: {user_id})\n"
        f"📁 Тип: Домены\n"
        f"🔢 Количество: {quantity}",
        reply_markup=kb
    )

    await message.answer("Ваша заявка отправлена администратору.", reply_markup=get_menu_keyboard(message.from_user.id))
    await state.clear()
