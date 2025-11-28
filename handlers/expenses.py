"""
Обработчики для системы получения данных по расходам
"""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
import gspread
import bugsnag
from config import GOOGLE_SHEET_ID, ADMIN_ID, TEAMLEADER_ID
from utils import is_user_allowed, last_messages, delete_last_messages
from keyboards import cancel_kb, get_menu_keyboard
from states import Form

router = Router()

def get_expense_data(user_id: int) -> str:
    """Получает данные по расходу пользователя из 3-й таблицы Google Sheets"""
    try:
        gc = gspread.service_account(filename='credentials.json')
        table = gc.open_by_key(GOOGLE_SHEET_ID)
        worksheet = table.get_worksheet(3)  # Таблица под индексом 3

        # Получаем все значения первого столбца начиная со второй строки
        user_ids_column = worksheet.col_values(1)[1:]  # Пропускаем заголовок

        # Ищем пользователя по ID
        for i, cell_value in enumerate(user_ids_column, start=2):  # +2 потому что пропустили заголовок и индекс начинается с 1
            if cell_value.strip() == str(user_id):
                # Получаем значение из второго столбца той же строки
                expense_value = worksheet.cell(i, 2).value
                if expense_value:
                    return f"💸 Ваш расход за текущий период: ${expense_value}"
                else:
                    return "Данные не найдены. Обратитесь к администратору."

        return "Данные не найдены. Обратитесь к администратору."
    except Exception as e:
        bugsnag.notify(e, meta_data={"context": "get_expense_data", "user_id": user_id})
        return "Ошибка при получении данных о расходе."

@router.message(F.text == "💸 Получить данные по расходу")
async def get_expense_info(message: Message):
    """Обрабатывает запрос на получение данных по расходу"""
    if not is_user_allowed(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    user_id = message.from_user.id
    expense_info = get_expense_data(user_id)
    await message.answer(expense_info)

@router.message(F.text == "📊 Получить расход по байеру")
async def get_buyer_expense_start(message: Message, state: FSMContext):
    """Начинает процесс получения расхода по байеру (только для админов)"""
    if message.from_user.id != ADMIN_ID and message.from_user.id != TEAMLEADER_ID:
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    m1 = await message.answer("Введите ID байера для получения данных по расходу:", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id]
    await state.set_state(Form.entering_buyer_id)

@router.message(Form.entering_buyer_id)
async def process_buyer_id(message: Message, state: FSMContext):
    """Обрабатывает введенный ID байера"""
    await delete_last_messages(message.from_user.id, message.bot)

    try:
        buyer_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Неверный формат ID. Введите числовой ID байера:", reply_markup=cancel_kb)
        return

    expense_info = get_expense_data(buyer_id)

    if "Данные не найдены" in expense_info:
        await message.answer(f"❌ Байер с ID {buyer_id} не найден в системе.\nПроверьте правильность введенного ID или проверьте таблицу.")
    else:
        await message.answer(f"📊 Данные по байеру {buyer_id}: ${expense_info}")

    await state.clear()
    menu_kb = get_menu_keyboard(message.from_user.id)
    await message.answer("Возвращаю в главное меню ⬅️", reply_markup=menu_kb)

