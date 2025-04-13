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
    KeyboardButton("\ud83d\udcb0 Заказать пополнение"),
    KeyboardButton("\ud83d\udcc2 Запросить расходники")
)

cancel_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
cancel_kb.add(KeyboardButton("\u274c Отмена"))

class Form(StatesGroup):
    waiting_for_bank = State()
    waiting_for_amount = State()
    waiting_for_type = State()
    choosing_supply_category = State()
    choosing_account_type = State()
    entering_account_quantity = State()
    entering_domain_quantity = State()

@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.answer("Выберите действие:", reply_markup=menu_kb)

# ========== Пополнение ==========

@dp.message_handler(lambda msg: msg.text == "\ud83d\udcb0 Заказать пополнение")
async def order_topup(message: types.Message):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("\ud83c\udfe6 AdsCard", callback_data="bank:adscard"),
        InlineKeyboardButton("\ud83d\udcb3 Traffic.cards", callback_data="bank:trafficcards")
    )
    await message.answer("Выберите банк:", reply_markup=kb)
    await message.answer("\u274c В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
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
        InlineKeyboardButton("\u26a1 Срочное", callback_data="type:urgent"),
        InlineKeyboardButton("\ud83d\udd58 Не срочное (до 21:00)", callback_data="type:normal")
    )
    await message.answer("Выберите тип пополнения:", reply_markup=kb)
    await message.answer("\u274c В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
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
    topup_type_text = "\u26a1 Срочное" if topup_type == "urgent" else "\ud83d\udd58 Не срочное (до 21:00)"

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("\u2705 Выполнено", callback_data=f"approve:{user_id}"),
        InlineKeyboardButton("\u274c Отклонено", callback_data=f"decline:{user_id}")
    )

    await bot.send_message(
        ADMIN_ID,
        f"\ud83d\udd14 Новая заявка от @{username} (ID: {user_id})\n"
        f"\ud83c\udfe6 Банк: {bank}\n"
        f"\ud83d\udcb3 Сумма: {amount}\n"
        f"\ud83d\udccc Тип: {topup_type_text}",
        reply_markup=kb
    )

    await query.message.answer("Ваша заявка отправлена администратору.", reply_markup=menu_kb)
    await state.finish()

# ========== Расходники ==========

@dp.message_handler(lambda msg: msg.text == "\ud83d\udcc2 Запросить расходники")
async def request_supplies(message: types.Message):
    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("\ud83d\udc64 Добавить аккаунты", callback_data="supply:accounts"),
        InlineKeyboardButton("\ud83d\udcc4 Добавить домены", callback_data="supply:domains")
    )
    await message.answer("Выберите категорию:", reply_markup=kb)
    await Form.choosing_supply_category.set()

@dp.callback_query_handler(lambda c: c.data.startswith("supply:"), state=Form.choosing_supply_category)
async def supply_category_selected(query: types.CallbackQuery, state: FSMContext):
    _, category = query.data.split(":")
    await query.answer()
    if category == "accounts":
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("\ud83d\udc64 Сетап КИНГ+10 авторегов (с ФП и почтой)", callback_data="acc:set1"),
            InlineKeyboardButton("\ud83d\udc64 КИНГ + 1-3 БМ (с ФП и почтой)", callback_data="acc:set2"),
            InlineKeyboardButton("\ud83d\udc64 Автореги (с ФП и почтой)", callback_data="acc:set3")
        )
        await query.message.answer("Выберите категорию (если нет в наличии, то будет добавлено то, что есть):", reply_markup=kb)
        await Form.choosing_account_type.set()
    else:
        await query.message.answer("Введите количество доменов:", reply_markup=cancel_kb)
        await Form.entering_domain_quantity.set()

@dp.callback_query_handler(lambda c: c.data.startswith("acc:"), state=Form.choosing_account_type)
async def account_type_chosen(query: types.CallbackQuery, state: FSMContext):
    _, acc_type = query.data.split(":")
    await state.update_data(account_type=acc_type)
    await query.message.answer("Введите количество аккаунтов:", reply_markup=cancel_kb)
    await Form.entering_account_quantity.set()
    await query.answer()

@dp.message_handler(state=Form.entering_account_quantity)
async def handle_account_quantity(message: types.Message, state: FSMContext):
    quantity = message.text.strip()
    data = await state.get_data()
    acc_type = data.get("account_type")

    acc_text = {
        "set1": "\ud83d\udc64 Сетап КИНГ+10 авторегов",
        "set2": "\ud83d\udc64 КИНГ + 1-3 БМ",
        "set3": "\ud83d\udc64 Автореги"
    }.get(acc_type, "\ud83d\udc64 Неизвестно")

    user_id = message.from_user.id
    username = message.from_user.username or "нет username"

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("\u2705 Выполнено", callback_data=f"approve:{user_id}"),
        InlineKeyboardButton("\u274c Отклонено", callback_data=f"decline:{user_id}")
    )

    await bot.send_message(
        ADMIN_ID,
        f"\ud83d\udd14 Запрос аккаунтов от @{username} (ID: {user_id})\n"
        f"Категория: {acc_text}\nКоличество: {quantity}",
        reply_markup=kb
    )

    await message.answer("Запрос отправлен администратору.", reply_markup=menu_kb)
    await state.finish()

@dp.message_handler(state=Form.entering_domain_quantity)
async def handle_domain_quantity(message: types.Message, state: FSMContext):
    quantity = message.text.strip()
    user_id = message.from_user.id
    username = message.from_user.username or "нет username"

    kb = InlineKeyboardMarkup()
    kb.add(
        InlineKeyboardButton("\u2705 Выполнено", callback_data=f"approve:{user_id}"),
        InlineKeyboardButton("\u274c Отклонено", callback_data=f"decline:{user_id}")
    )

    await bot.send_message(
        ADMIN_ID,
        f"\ud83d\udd14 Запрос доменов от @{username} (ID: {user_id})\nКоличество: {quantity}",
        reply_markup=kb
    )

    await message.answer("Запрос отправлен администратору.", reply_markup=menu_kb)
    await state.finish()

# ========= Общие ==========

@dp.callback_query_handler(lambda c: c.data.startswith("approve") or c.data.startswith("decline"))
async def process_callback(query: types.CallbackQuery):
    action, user_id = query.data.split(":")
    user_id = int(user_id)

    if action == "approve":
        await bot.send_message(user_id, "\u2705 Ваша заявка была выполнена.")
        await query.message.edit_reply_markup(reply_markup=None)
        await query.answer("Отмечено как выполнено.")
    elif action == "decline":
        await bot.send_message(user_id, "\u274c Ваша заявка была отклонена.")
        await query.message.edit_reply_markup(reply_markup=None)
        await query.answer("Отмечено как отклонено.")

@dp.message_handler(lambda msg: msg.text == "\u274c Отмена", state="*")
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.finish()
    await message.answer("Действие отменено. Возвращаю в главное меню \u2b05\ufe0f", reply_markup=menu_kb)

# ================== Webhook Setup =====================

async def on_startup(dp):
    await bot.set_webhook(WEBHOOK_URL)
    print("\u2705 Webhook установлен:", WEBHOOK_URL)

async def on_shutdown(dp):
    await bot.delete_webhook()
    await dp.storage.close()
    await dp.storage.wait_closed()
    print("\ud83e\uddf9 Webhook удалён и хранилище закрыто")

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
