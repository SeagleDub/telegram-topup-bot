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

def get_multiple_expenses_data(user_ids: list) -> dict:
    """Получает данные по расходу для нескольких пользователей из 3-й таблицы Google Sheets"""
    try:
        gc = gspread.service_account(filename='credentials.json')
        table = gc.open_by_key(GOOGLE_SHEET_ID)
        worksheet = table.get_worksheet(3)  # Таблица под индексом 3

        # Получаем все данные из первых трех столбцов (ID, Расход, Имя)
        all_data = worksheet.get_all_values()[1:]  # Пропускаем заголовок

        result = {}
        for row in all_data:
            if len(row) >= 3 and row[0].strip():
                row_id = row[0].strip()
                if row_id in user_ids:
                    result[row_id] = {
                        'expense': row[1] if len(row) > 1 else 'N/A',
                        'name': row[2] if len(row) > 2 else 'N/A'
                    }

        return result
    except Exception as e:
        bugsnag.notify(e, meta_data={"context": "get_multiple_expenses_data", "user_ids": user_ids})
        return {}

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

    m1 = await message.answer("Введите ID байера (или несколько ID через запятую) для получения данных по расходу:", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id]
    await state.set_state(Form.entering_buyer_id)

@router.message(Form.entering_buyer_id)
async def process_buyer_id(message: Message, state: FSMContext):
    """Обрабатывает введенный ID байера (или несколько ID через запятую)"""
    await delete_last_messages(message.from_user.id, message.bot)

    # Проверка на отмену
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    # Парсим ID (может быть один или несколько через запятую)
    input_text = message.text.strip()
    buyer_ids_str = [id_str.strip() for id_str in input_text.split(',')]

    # Проверяем, что все ID валидны
    buyer_ids = []
    for id_str in buyer_ids_str:
        try:
            buyer_ids.append(int(id_str))
        except ValueError:
            await message.answer(f"❌ Неверный формат ID: '{id_str}'. Введите числовые ID байеров через запятую:", reply_markup=cancel_kb)
            return

    # Если один ID - используем старую логику
    if len(buyer_ids) == 1:
        buyer_id = buyer_ids[0]
        expense_info = get_expense_data(buyer_id)

        if "Данные не найдены" in expense_info:
            await message.answer(f"❌ Байер с ID {buyer_id} не найден в системе.\nПроверьте правильность введенного ID или проверьте таблицу.")
        else:
            await message.answer(f"📊 Данные по байеру {buyer_id}:\n{expense_info}")
    else:
        # Получаем данные для нескольких ID
        buyer_ids_str_list = [str(bid) for bid in buyer_ids]
        expenses_data = get_multiple_expenses_data(buyer_ids_str_list)

        if not expenses_data:
            await message.answer("❌ Не удалось получить данные. Проверьте подключение к таблице.")
        else:
            # Формируем таблицу
            response = "📊 Данные по байерам:\n\n"
            response += "┌─────────────┬────────────┬──────────────────┐\n"
            response += "│ ID          │ Расход     │ Имя              │\n"
            response += "├─────────────┼────────────┼──────────────────┤\n"

            found_count = 0
            not_found = []

            for buyer_id in buyer_ids:
                buyer_id_str = str(buyer_id)
                if buyer_id_str in expenses_data:
                    data = expenses_data[buyer_id_str]
                    expense = data['expense'] if data['expense'] else 'N/A'
                    name = data['name'] if data['name'] else 'N/A'

                    # Форматируем строки для выравнивания
                    id_col = f"{buyer_id_str:<11}"
                    expense_col = f"{expense:<10}"
                    name_col = f"{name:<16}"

                    response += f"│ {id_col} │ {expense_col} │ {name_col} │\n"
                    found_count += 1
                else:
                    not_found.append(buyer_id_str)

            response += "└─────────────┴────────────┴──────────────────┘\n"

            # Добавляем информацию о ненайденных ID
            if not_found:
                response += f"\n❌ Не найдены: {', '.join(not_found)}"

            response += f"\n\n✅ Найдено: {found_count} из {len(buyer_ids)}"

            await message.answer(response)

    await state.clear()
    menu_kb = get_menu_keyboard(message.from_user.id)
    await message.answer("Возвращаю в главное меню ⬅️", reply_markup=menu_kb)

