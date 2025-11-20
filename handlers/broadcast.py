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
        "Когда закончите, нажмите кнопку «🚀 Послать",
        reply_markup=broadcast_kb
    )
    await state.set_state(Form.broadcast_collecting)

@router.message(Form.broadcast_collecting, F.text == "🚀 Послать")
async def send_broadcast(message: Message, state: FSMContext):
    """Отправляет рассылку всем пользователям"""
    await message.answer("Начинаю рассылку...")
    data = await state.get_data()
    messages = data.get("broadcast_messages", [])

    if not messages:
        await message.answer("⚠️ Список сообщений пуст. Рассылка отменена.", reply_markup=get_menu_keyboard(message.from_user.id))
        await state.clear()
        return

    user_ids = get_user_ids_from_sheet()

    if not user_ids:
        await message.answer("⚠️ Список пользователей пуст. Рассылка отменена.", reply_markup=get_menu_keyboard(message.from_user.id))
        await state.clear()
        return

    success_count = 0
    fail_count = 0

    # Определяем от кого рассылка
    sender_name = "👑 админа" if message.from_user.id == ADMIN_ID else "👨‍💼 тимлидера"

    for user_id in user_ids:
        user_success = True
        try:
            await message.bot.send_message(
                user_id,
                text=f"*📢 Сообщение от {sender_name}*",
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Ошибка при отправке заголовка пользователю {user_id}: {e}")
            user_success = False
            continue

        for msg in messages:
            try:
                await message.bot.copy_message(chat_id=user_id, from_chat_id=msg["chat_id"], message_id=msg["message_id"])
            except Exception as e:
                print(f"Ошибка при отправке пользователю {user_id}: {e}")
                user_success = False

        if user_success:
            success_count += 1
        else:
            fail_count += 1

    await message.answer(
        f"✅ Рассылка завершена.\n"
        f"Отправлено: {success_count}\n"
        f"Не доставлено: {fail_count}",
        reply_markup=get_menu_keyboard(message.from_user.id)
    )
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


    await state.clear()

@router.message(F.text == "❌ Отмена", Form.broadcast_collecting)
async def cancel_broadcast(message: Message, state: FSMContext):
    """Отменяет рассылку"""
    await state.clear()
    await message.answer("Рассылка отменена.", reply_markup=get_menu_keyboard(message.from_user.id))
