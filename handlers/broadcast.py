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
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    await state.clear()
    await state.update_data(broadcast_messages=[])

    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
    broadcast_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Послать")],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )

    await message.answer(
        "Отправьте мне любые сообщения, которые хотите разослать.\n"
        "Когда закончите, нажмите кнопку «🚀 Послать»",
        reply_markup=broadcast_kb
    )
    await state.set_state(Form.broadcast_collecting)

@router.message(Form.broadcast_collecting, F.text == "🚀 Послать")
async def send_broadcast(message: Message, state: FSMContext):
    """Отправляет рассылку всем пользователям"""
    status_msg = await message.answer("🔄 Подготовка к рассылке...")
    data = await state.get_data()
    messages = data.get("broadcast_messages", [])

    if not messages:
        await status_msg.edit_text("⚠️ Список сообщений пуст. Рассылка отменена.")
        await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))
        await state.clear()
        return

    await status_msg.edit_text("📋 Получение списка пользователей...")
    user_ids = get_user_ids_from_sheet()

    if not user_ids:
        await status_msg.edit_text("⚠️ Список пользователей пуст. Рассылка отменена.")
        await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))
        await state.clear()
        return

    total_users = len(user_ids)
    success_count = 0
    fail_count = 0

    # Определяем от кого рассылка
    sender_name = "👑 админа" if message.from_user.id == ADMIN_ID else "👨‍💼 тимлидера"

    await status_msg.edit_text(f"📢 Начинаю рассылку для {total_users} пользователей...")

    for i, user_id in enumerate(user_ids, 1):
        user_success = True
        try:
            # Отправляем заголовок
            await message.bot.send_message(
                user_id,
                text=f"*📢 Сообщение от {sender_name}*",
                parse_mode="Markdown"
            )

            # Отправляем все сообщения рассылки
            for msg in messages:
                try:
                    await message.bot.copy_message(
                        chat_id=user_id,
                        from_chat_id=msg["chat_id"],
                        message_id=msg["message_id"]
                    )
                except Exception as e:
                    bugsnag.notify(e, meta_data={
                        "function": "send_broadcast",
                        "user_id": user_id,
                        "message_id": msg["message_id"],
                        "error_type": "message_copy_error"
                    })
                    print(f"Ошибка при отправке сообщения пользователю {user_id}: {e}")
                    user_success = False
                    break

        except Exception as e:
            bugsnag.notify(e, meta_data={
                "function": "send_broadcast",
                "user_id": user_id,
                "error_type": "header_send_error"
            })
            print(f"Ошибка при отправке заголовка пользователю {user_id}: {e}")
            user_success = False

        if user_success:
            success_count += 1
        else:
            fail_count += 1

        # Обновляем прогресс каждые 10 пользователей или в конце
        if i % 10 == 0 or i == total_users:
            await status_msg.edit_text(
                f"📢 Рассылка в процессе...\n"
                f"Прогресс: {i}/{total_users}\n"
                f"✅ Отправлено: {success_count}\n"
                f"❌ Ошибок: {fail_count}"
            )

    await status_msg.edit_text(
        f"✅ Рассылка завершена!\n\n"
        f"📊 Статистика:\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"✅ Успешно отправлено: {success_count}\n"
        f"❌ Не доставлено: {fail_count}\n"
        f"📈 Успешность: {round(success_count/total_users*100, 1)}%"
    )

    await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))
    await state.clear()

@router.message(Form.broadcast_collecting, F.text == "❌ Отмена")
async def cancel_broadcast(message: Message, state: FSMContext):
    """Отменяет рассылку"""
    await state.clear()
    await message.answer("Рассылка отменена.", reply_markup=get_menu_keyboard(message.from_user.id))

@router.message(Form.broadcast_collecting)
async def collect_broadcast_messages(message: Message, state: FSMContext):
    """Собирает сообщения для рассылки"""
    data = await state.get_data()
    broadcast_messages = data.get("broadcast_messages", [])

    # Сохраняем необходимую информацию из сообщения для пересылки
    msg_data = {
        "message_id": message.message_id,
        "chat_id": message.chat.id,  # для пересылки
    }
    broadcast_messages.append(msg_data)
    await state.update_data(broadcast_messages=broadcast_messages)

    await message.answer("Сообщение добавлено в рассылку. Отправьте ещё или нажмите «🚀 Послать».")


