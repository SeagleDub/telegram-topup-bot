"""
Система добавления пикселей
"""
import re
import gspread
import bugsnag
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from ..states import Form
from ..keyboards import cancel_kb, get_menu_keyboard
from ..utils import is_user_allowed, last_messages, send_notification_to_admins
from ..config import GOOGLE_SHEET_ID

router = Router()

@router.message(F.text == "📊 Добавить пиксель в систему")
async def add_pixel_to_system(message: Message, state: FSMContext):
    """Начинает процесс добавления пикселя в систему"""
    if not is_user_allowed(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    m1 = await message.answer("Введите Pixel ID:")
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.entering_pixel_id)

@router.message(Form.entering_pixel_id)
async def receive_pixel_id(message: Message, state: FSMContext):
    """Обрабатывает введенный Pixel ID"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    pixel_id = message.text.strip()
    if not pixel_id:
        await message.answer("❌ Pixel ID не может быть пустым. Пожалуйста, введите корректный Pixel ID.", reply_markup=cancel_kb)
        return

    # Валидация Pixel ID (только цифры)
    if not pixel_id.isdigit():
        await message.answer("❌ Pixel ID должен содержать только цифры. Пример: 123456789012345", reply_markup=cancel_kb)
        return

    await state.update_data(pixel_id=pixel_id)
    await message.answer("Введите Pixel Key:", reply_markup=cancel_kb)
    await state.set_state(Form.entering_pixel_key)

@router.message(Form.entering_pixel_key)
async def receive_pixel_key(message: Message, state: FSMContext):
    """Обрабатывает введенный Pixel Key и сохраняет пиксель"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    pixel_key = message.text.strip()
    if not pixel_key:
        await message.answer("❌ Pixel Key не может быть пустым. Пожалуйста, введите корректный Pixel Key.", reply_markup=cancel_kb)
        return

    # Валидация Pixel Key (буквы, цифры, дефисы, подчеркивания)
    if not re.match(r'^[a-zA-Z0-9_-]+$', pixel_key):
        await message.answer("❌ Pixel Key может содержать только буквы, цифры, дефисы и подчеркивания.", reply_markup=cancel_kb)
        return

    data = await state.get_data()
    pixel_id = data.get("pixel_id")
    user_id = message.from_user.id
    username = message.from_user.username or "нет username"

    try:
        # Добавляем пиксель в Google таблицу
        gc = gspread.service_account(filename='credentials.json')
        table = gc.open_by_key(GOOGLE_SHEET_ID)
        worksheet = table.get_worksheet(2)

        # Добавляем новую строку с Pixel ID и Pixel Key
        worksheet.append_row([pixel_id, pixel_key])

        # Уведомляем администратора и тимлидера о добавлении нового пикселя
        await send_notification_to_admins(
            message.bot,
            f"🔔 Новый пиксель добавлен в систему\n"
            f"👤 От: @{username} (ID: {user_id})\n"
            f"📊 Pixel ID: {pixel_id}\n"
            f"🔑 Pixel Key: {pixel_key}"
        )

        await message.answer(
            f"✅ Пиксель успешно добавлен в систему!\n"
            f"📊 Pixel ID: {pixel_id}\n"
            f"🔑 Pixel Key: {pixel_key}",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )

    except Exception as e:
        bugsnag.notify(e)
        await message.answer(
            "❌ Произошла ошибка при добавлении пикселя в систему. Попробуйте еще раз.",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )

    await state.clear()
