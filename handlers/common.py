"""
Обработчики callback-запросов и общих команд
"""
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from keyboards import get_menu_keyboard, cancel_kb
from utils import (last_messages, delete_last_messages, update_linked_messages,
                     send_notification_to_admins)
from config import ADMIN_ID, TEAMLEADER_ID

router = Router()

@router.message(Command("start"))
async def send_welcome(message: Message):
    """Обрабатывает команду /start"""
    if message.from_user.id == ADMIN_ID:
        await message.answer("👑 Админ-панель:", reply_markup=get_menu_keyboard(message.from_user.id))
    elif message.from_user.id == TEAMLEADER_ID:
        await message.answer("👨‍💼 Тимлидер-панель:", reply_markup=get_menu_keyboard(message.from_user.id))
    else:
        await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))

@router.callback_query(F.data.startswith("approve:"))
async def approve_request(query: CallbackQuery):
    """Одобряет заявку пользователя"""
    _, user_id = query.data.split(":")
    user_id = int(user_id)

    await query.bot.send_message(
        user_id,
        "✅ Ваша заявка одобрена и выполнена администратором."
    )

    updated_text = f"{query.message.text}\n\n✅ ВЫПОЛНЕНО"
    await query.message.edit_text(updated_text)

    # Обновляем связанное сообщение у другого админа
    await update_linked_messages(query.bot, query.message.chat.id, query.message.message_id, updated_text)

    await query.answer("Пользователь уведомлен об одобрении")

@router.callback_query(F.data.startswith("processing:"))
async def processing_request(query: CallbackQuery):
    """Берет заявку в работу"""
    _, user_id = query.data.split(":")
    user_id = int(user_id)

    await query.bot.send_message(
        user_id,
        "✅ Ваша заявка рассмотрена и взята в работу."
    )

    updated_text = f"{query.message.text}\n\n✅ В РАБОТЕ"
    await query.message.edit_text(updated_text)

    # Обновляем связанное сообщение у другого админа
    await update_linked_messages(query.bot, query.message.chat.id, query.message.message_id, updated_text)

    await query.answer("Пользователь уведомлен о взятии в работу")

@router.callback_query(F.data.startswith("decline:"))
async def decline_request(query: CallbackQuery):
    """Отклоняет заявку пользователя"""
    _, user_id = query.data.split(":")
    user_id = int(user_id)

    await query.bot.send_message(
        user_id,
        "❌ Ваша заявка отклонена администратором."
    )

    updated_text = f"{query.message.text}\n\n❌ ОТКЛОНЕНО"
    await query.message.edit_text(updated_text)

    # Обновляем связанное сообщение у другого админа
    await update_linked_messages(query.bot, query.message.chat.id, query.message.message_id, updated_text)

    await query.answer("Пользователь уведомлен об отклонении")

@router.message(F.text == "❌ Отмена")
async def cancel_handler(message: Message, state: FSMContext):
    """Общий обработчик отмены действий"""
    await delete_last_messages(message.from_user.id, message.bot)
    await state.clear()
    menu_kb = get_menu_keyboard(message.from_user.id)
    await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=menu_kb)
