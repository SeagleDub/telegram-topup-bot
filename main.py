import os
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.utils.executor import start_webhook
from aiohttp import web

API_TOKEN = os.getenv("BOT_TOKEN") or "7829191204:AAFafJxCIapC-0RJwk4N_TKlJxuL19eVk9g"
ADMIN_ID = int(os.getenv("ADMIN_ID") or 582761505)
WEBHOOK_HOST = os.getenv("WEBHOOK_URL") or "https://telegram-topup-bot-1lzl.onrender.com"
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.environ.get("PORT", 10000))

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())

menu_kb = ReplyKeyboardMarkup(resize_keyboard=True)
menu_kb.add(
    KeyboardButton("💰 Заказать пополнение"),
    KeyboardButton("📦 Запросить расходники")
)

cancel_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
cancel_kb.add(KeyboardButton("❌ Отмена"))

class Form(StatesGroup):
    waiting_for_bank = State()
    waiting_for_amount = State()
    waiting_for_type = State()

@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.answer("Выберите действие:", reply_markup=menu_kb)

@dp.message_handler(lambda msg: msg.text == "💰 Заказать пополнение")
async def order_topup(message: types.Message):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🏦 AdsCard", callback_data="bank:adscard"),
        InlineKeyboardButton("💳 Traffic.cards", callback_data="bank:trafficcards")
    )
    await message.answer("Выберите банк:", reply_markup=kb)
    await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    await Form.waiting_for_bank.set()

@dp.callback_query_handler(lambda c: c.data.startswith("bank:"), state=Form.waiting_for_bank)
async def bank_selected(query: types.CallbackQuery, state: FSMContext):
    _, bank = query.data.split(":")
    await state.update_data(bank=bank)
    await query.message.answer("Введите сумму пополнения:", reply_markup=cancel_kb)
    await Form.waiting_for_amount.set()
    await query.answer()

@dp.message_handler(state=Form.waiting_for_amount)
async def get_amount(message: types.Message, state: FSMContext):
    amount = message.text.strip()
    await state.update_data(amount=amount)

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⚡ Срочное", callback_data="type:urgent"),
        InlineKeyboardButton("🕘 Не срочное (до 21:00)", callback_data="type:normal")
    )
    await message.answer("Выберите тип пополнения:", reply_markup=kb)
    await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    await Form.waiting_for_type.set()

@dp.callback_query_handler(lambda c: c.data.startswith("type:"), state=Form.waiting_for_type)
async def type_selected(query: types.CallbackQuery, state: FSMContext):
    _, topup_type = query.data.split(":")
    await state.update_data(topup_type=topup_type)
    await query.answer()

    user_id = query.from_user.id
    username = query.from_user.username or "нет username"
    data = await state.get_data()

    bank = data.get("bank", "не указан")
    amount = data.get("amount", "не указано")
    topup_type_text = "⚡ Срочное" if topup_type == "urgent" else "🕘 Не срочное (до 21:00)"

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("✅ Выполнено", callback_data=f"approve:{user_id}"),
        InlineKeyboardButton("❌ Отклонено", callback_data=f"decline:{user_id}")
    )

    await bot.send_message(
        ADMIN_ID,
        f"🔔 Новая заявка от @{username} (ID: {user_id})\n"
        f"🏦 Банк: {bank}\n"
        f"💳 Сумма: {amount}\n"
        f"📌 Тип: {topup_type_text}",
        reply_markup=kb
    )

    await query.message.answer("Ваша заявка отправлена администратору.", reply_markup=menu_kb)
    await state.finish()



@dp.callback_query_handler(lambda c: c.data.startswith("approve") or c.data.startswith("decline"))
async def process_callback(query: types.CallbackQuery):
    action, user_id = query.data.split(":")
    user_id = int(user_id)

    if action == "approve":
        await bot.send_message(user_id, "✅ Ваша заявка была выполнена.")
        await query.message.edit_reply_markup(reply_markup=None)
        await query.answer("Отмечено как выполнено.")
    elif action == "decline":
        await bot.send_message(user_id, "❌ Ваша заявка была отклонена.")
        await query.message.edit_reply_markup(reply_markup=None)
        await query.answer("Отмечено как отклонено.")

@dp.message_handler(lambda msg: msg.text == "❌ Отмена", state="*")
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Действие отменено. Возвращаю в главное меню ⤴️", reply_markup=menu_kb)

class ResourceForm(StatesGroup):
    choosing_resource_type = State()
    choosing_account_type = State()
    entering_quantity = State()

@dp.message_handler(lambda msg: msg.text == "📦 Запросить расходники")
async def request_resources(message: types.Message):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("👤 Добавить аккаунты", callback_data="res:accounts"),
        InlineKeyboardButton("🌐 Добавить домены", callback_data="res:domains")
    )
    await message.answer("Выберите категорию:", reply_markup=kb)
    await ResourceForm.choosing_resource_type.set()

@dp.callback_query_handler(lambda c: c.data.startswith("res:"), state=ResourceForm.choosing_resource_type)
async def resource_type_chosen(query: types.CallbackQuery, state: FSMContext):
    _, choice = query.data.split(":")
    await state.update_data(type=choice)

    if choice == "accounts":
        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("📘 Сетап КИНГ+10 авторегов", callback_data="acc:king10"),
            InlineKeyboardButton("📘 КИНГ + 1-3 БМ", callback_data="acc:kingbm"),
            InlineKeyboardButton("📘 Автореги", callback_data="acc:autoreg")
        )
        await query.message.answer("Выберите категорию аккаунтов (если нет в наличии — будет добавлено то, что есть):", reply_markup=kb)
        await ResourceForm.choosing_account_type.set()
    else:
        await query.message.answer("Введите количество доменов:")
        await ResourceForm.entering_quantity.set()

    await query.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("acc:"), state=ResourceForm.choosing_account_type)
async def account_type_chosen(query: types.CallbackQuery, state: FSMContext):
    _, acc_type = query.data.split(":")
    await state.update_data(account_type=acc_type)
    await query.message.answer("Введите количество:")
    await ResourceForm.entering_quantity.set()
    await query.answer()

@dp.message_handler(state=ResourceForm.entering_quantity)
async def handle_quantity(message: types.Message, state: FSMContext):
    quantity = message.text.strip()
    user_id = message.from_user.id
    username = message.from_user.username or "нет username"

    data = await state.get_data()
    res_type = data.get("type")

    if res_type == "accounts":
        acc_type = data.get("account_type")
        acc_map = {
            "king10": "📘 Сетап КИНГ+10 авторегов",
            "kingbm": "📘 КИНГ + 1-3 БМ",
            "autoreg": "📘 Автореги"
        }
        type_text = acc_map.get(acc_type, "❓ Неизвестно")
        body = f"🔔 Запрос на аккаунты от @{username} (ID: {user_id})\nКатегория: {type_text}\nКоличество: {quantity}"
    else:
        body = f"🔔 Запрос на домены от @{username} (ID: {user_id})\nКоличество: {quantity}"

    await bot.send_message(ADMIN_ID, body)
    await message.answer("✅ Заявка отправлена администратору.", reply_markup=menu_kb)
    await state.finish()

# ================== Webhook Setup =====================

async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL)
    print("✅ Webhook установлен:", WEBHOOK_URL)

async def on_shutdown(dp):
    await bot.delete_webhook()
    await dp.storage.close()
    await dp.storage.wait_closed()
    print("🧹 Webhook удалён и хранилище закрыто")

if __name__ == '__main__':
    start_webhook(
        dispatcher=dp,
        webhook_path=WEBHOOK_PATH,
        on_startup=on_startup,
        on_shutdown=on_shutdown,
        skip_updates=True,
        host=WEBAPP_HOST,
        port=WEBAPP_PORT,
    )
