# 🟢 Старт
print("✅ CLEAN START — main.py точно используется")

from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.contrib.middlewares.logging import LoggingMiddleware

API_TOKEN = "ТОКЕН"  # 👈 замени на свой
ADMIN_ID = 582761505  # 👈 замени на свой

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())

# Главное меню
menu_kb = ReplyKeyboardMarkup(resize_keyboard=True)
menu_kb.add(KeyboardButton("💰 Заказать пополнение"))
menu_kb.add(KeyboardButton("📦 Запросить расходники"))

cancel_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
cancel_kb.add(KeyboardButton("❌ Отмена"))

# FSM
class Form(StatesGroup):
    waiting_for_bank = State()
    waiting_for_amount = State()
    waiting_for_type = State()

    supplies_choice = State()
    account_category = State()
    account_quantity = State()
    domain_quantity = State()

# /start
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    await message.answer("Выберите действие:", reply_markup=menu_kb)

# 💰 Пополнение
@dp.message_handler(lambda msg: msg.text == "💰 Заказать пополнение")
async def topup_start(message: types.Message):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🏦 AdsCard", callback_data="bank:adscard"),
        InlineKeyboardButton("💳 Traffic.cards", callback_data="bank:trafficcards")
    )
    await message.answer("Выберите банк:", reply_markup=kb)
    await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    await Form.waiting_for_bank.set()

@dp.callback_query_handler(lambda c: c.data.startswith("bank:"), state=Form.waiting_for_bank)
async def topup_bank(query: types.CallbackQuery, state: FSMContext):
    _, bank = query.data.split(":")
    await state.update_data(bank=bank)
    await query.message.answer("Введите сумму пополнения:", reply_markup=cancel_kb)
    await Form.waiting_for_amount.set()
    await query.answer()

@dp.message_handler(state=Form.waiting_for_amount)
async def topup_amount(message: types.Message, state: FSMContext):
    await state.update_data(amount=message.text.strip())

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("⚡ Срочное", callback_data="type:urgent"),
        InlineKeyboardButton("🕘 Не срочное (до 21:00)", callback_data="type:normal")
    )
    await message.answer("Выберите тип пополнения:", reply_markup=kb)
    await Form.waiting_for_type.set()

@dp.callback_query_handler(lambda c: c.data.startswith("type:"), state=Form.waiting_for_type)
async def topup_type(query: types.CallbackQuery, state: FSMContext):
    _, topup_type = query.data.split(":")
    await state.update_data(topup_type=topup_type)

    user_id = query.from_user.id
    username = query.from_user.username or "нет username"
    data = await state.get_data()
    topup_type_text = "⚡ Срочное" if topup_type == "urgent" else "🕘 Не срочное (до 21:00)"

    text = (
        f"🔔 Новая заявка от @{username} (ID: {user_id})\n"
        f"🏦 Банк: {data['bank']}\n"
        f"💳 Сумма: {data['amount']}\n"
        f"📌 Тип: {topup_type_text}"
    )

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("✅ Выполнено", callback_data=f"approve:{user_id}"),
        InlineKeyboardButton("❌ Отклонено", callback_data=f"decline:{user_id}")
    )

    await bot.send_message(ADMIN_ID, text, reply_markup=kb)
    await query.message.answer("Ваша заявка отправлена администратору ✅", reply_markup=menu_kb)
    await state.finish()

# 📦 Расходники
@dp.message_handler(lambda msg: msg.text == "📦 Запросить расходники")
async def supplies_start(message: types.Message):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("👤 Добавить аккаунты", callback_data="supply:accounts"),
        InlineKeyboardButton("🌐 Добавить домены", callback_data="supply:domains")
    )
    await message.answer("Выберите тип расходника:", reply_markup=kb)
    await Form.supplies_choice.set()

# 👤 Аккаунты
@dp.callback_query_handler(lambda c: c.data == "supply:accounts", state=Form.supplies_choice)
async def supply_accounts(query: types.CallbackQuery):
    kb = InlineKeyboardMarkup(row_width=1)
    await query.message.answer(
        "Выберите категорию (если нет в наличии, то будет добавлено то, что есть):",
        reply_markup=kb.add(
            InlineKeyboardButton("📘 Сетап КИНГ+10 авторегов", callback_data="acc:setup"),
            InlineKeyboardButton("📘 КИНГ + 1-3 БМ", callback_data="acc:bm"),
            InlineKeyboardButton("📘 Автореги", callback_data="acc:regs")
        )
    )
    await Form.account_category.set()
    await query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("acc:"), state=Form.account_category)
async def supply_account_quantity(query: types.CallbackQuery, state: FSMContext):
    _, acc_type = query.data.split(":")
    await state.update_data(account_type=acc_type)
    await query.message.answer("Введите количество:", reply_markup=cancel_kb)
    await Form.account_quantity.set()
    await query.answer()

@dp.message_handler(state=Form.account_quantity)
async def send_account_request(message: types.Message, state: FSMContext):
    quantity = message.text.strip()
    user_id = message.from_user.id
    username = message.from_user.username or "нет username"
    data = await state.get_data()

    acc_names = {
        "setup": "📘 Сетап КИНГ+10 авторегов",
        "bm": "📘 КИНГ + 1-3 БМ",
        "regs": "📘 Автореги"
    }

    text = (
        f"📦 Запрос на расходники от @{username} (ID: {user_id})\n"
        f"Тип: {acc_names.get(data['account_type'], '❓')}\n"
        f"Количество: {quantity}"
    )

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("✅ Выполнено", callback_data=f"approve:{user_id}"),
        InlineKeyboardButton("❌ Отклонено", callback_data=f"decline:{user_id}")
    )

    await bot.send_message(ADMIN_ID, text, reply_markup=kb)
    await message.answer("Запрос отправлен ✅", reply_markup=menu_kb)
    await state.finish()

# 🌐 Домены
@dp.callback_query_handler(lambda c: c.data == "supply:domains", state=Form.supplies_choice)
async def supply_domains(query: types.CallbackQuery):
    await query.message.answer("Введите количество доменов:", reply_markup=cancel_kb)
    await Form.domain_quantity.set()
    await query.answer()

@dp.message_handler(state=Form.domain_quantity)
async def send_domain_request(message: types.Message, state: FSMContext):
    quantity = message.text.strip()
    user_id = message.from_user.id
    username = message.from_user.username or "нет username"

    text = (
        f"📦 Запрос на домены от @{username} (ID: {user_id})\n"
        f"Количество: {quantity}"
    )

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("✅ Выполнено", callback_data=f"approve:{user_id}"),
        InlineKeyboardButton("❌ Отклонено", callback_data=f"decline:{user_id}")
    )

    await bot.send_message(ADMIN_ID, text, reply_markup=kb)
    await message.answer("Запрос отправлен ✅", reply_markup=menu_kb)
    await state.finish()

# ✅ / ❌ обработка
@dp.callback_query_handler(lambda c: c.data and c.data.startswith(("approve", "decline")))
async def process_admin_action(query: types.CallbackQuery):
    action, user_id = query.data.split(":")
    user_id = int(user_id)

    if action == "approve":
        await bot.send_message(user_id, "✅ Ваша заявка была выполнена.")
        await query.message.edit_reply_markup()
        await query.answer("Отмечено как выполнено.")
    elif action == "decline":
        await bot.send_message(user_id, "❌ Ваша заявка была отклонена.")
        await query.message.edit_reply_markup()
        await query.answer("Отмечено как отклонено.")

# ❌ Отмена
@dp.message_handler(lambda msg: msg.text == "❌ Отмена", state="*")
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Действие отменено. Возвращаю в главное меню ⤴️", reply_markup=menu_kb)

if __name__ == "__main__":
    executor.start_polling(dp)
