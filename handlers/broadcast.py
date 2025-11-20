"""
Система рассылки сообщений (доступно только админу и тимлидеру)
"""
import gspread
import bugsnag
from aiogram import Router, F
from aiogram.types import Message, ContentType
from aiogram.fsm.context import FSMContext

from states import Form
from keyboards import cancel_kb, get_menu_keyboard
from utils import last_messages, get_user_ids_from_sheet
from config import ADMIN_ID, TEAMLEADER_ID

router = Router()

@router.message(F.text == "📢 Сделать рассылку")
async def admin_broadcast_start(message: Message, state: FSMContext):
    """Начинает процесс создания рассылки (только для админа и тимлидера)"""
    if message.from_user.id not in [ADMIN_ID, TEAMLEADER_ID]:
        return

    m1 = await message.answer(
        "Отправьте сообщение для рассылки (текст, изображение, документ или видео).\n"
        "Можете отправлять несколько сообщений подряд.\n"
        "Когда закончите, нажмите 'Готово':",
        reply_markup=cancel_kb
    )
    last_messages[message.from_user.id] = [m1.message_id]
    await state.set_state(Form.broadcast_collecting)
    await state.update_data(messages=[])

@router.message(Form.broadcast_collecting)
async def collect_broadcast_messages(message: Message, state: FSMContext):
    """Собирает сообщения для рассылки"""
    # Проверяем команду отправки
    if message.text and message.text.lower() in ['готово', 'отправить']:
        await send_broadcast(message, state)
        return

    # Сохраняем сообщение
    data = await state.get_data()
    messages = data.get("messages", [])

    # Добавляем новое сообщение в список
    message_info = {
        'type': message.content_type,
        'message_id': message.message_id,
        'chat_id': message.chat.id
    }

    messages.append(message_info)
    await state.update_data(messages=messages)

    await message.answer(f"✅ Сообщение добавлено ({len(messages)} шт.). Отправьте ещё или напишите 'готово'.")

async def send_broadcast(message: Message, state: FSMContext):
    """Отправляет рассылку всем пользователям"""
    data = await state.get_data()
    messages = data.get("messages", [])

    if not messages:
        await message.answer("❌ Нет сообщений для рассылки!")
        return

    user_ids = get_user_ids_from_sheet()
    # Добавляем админа и тимлидера в список получателей
    user_ids.extend([ADMIN_ID, TEAMLEADER_ID])
    user_ids = list(set(user_ids))  # Убираем дубликаты

    if not user_ids:
        await message.answer("❌ Список пользователей пуст!")
        return

    sent_count = 0
    failed_count = 0

    status_message = await message.answer(f"📤 Начинаю рассылку для {len(user_ids)} пользователей...")

    for user_id in user_ids:
        try:
            # Отправляем все собранные сообщения
            for msg_info in messages:
                await message.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=msg_info['chat_id'],
                    message_id=msg_info['message_id']
                )
            sent_count += 1
        except Exception as e:
            failed_count += 1
            bugsnag.notify(e)

    # Обновляем статус
    await status_message.edit_text(
        f"✅ Рассылка завершена!\n"
        f"📤 Отправлено: {sent_count}\n"
        f"❌ Неудачно: {failed_count}"
    )

    await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))
    await state.clear()

@router.message(F.text == "❌ Отмена", Form.broadcast_collecting)
async def cancel_broadcast(message: Message, state: FSMContext):
    """Отменяет рассылку"""
    await state.clear()
    await message.answer("Рассылка отменена.", reply_markup=get_menu_keyboard(message.from_user.id))
