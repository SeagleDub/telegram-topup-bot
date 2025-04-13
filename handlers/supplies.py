from aiogram import Dispatcher, types
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from config import ADMIN_ID

menu_kb = ReplyKeyboardMarkup(resize_keyboard=True)
menu_kb.add(KeyboardButton("💰 Заказать пополнение"))
menu_kb.add(KeyboardButton("📦 Запросить расходники"))

cancel_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
cancel_kb.add(KeyboardButton("❌ Отмена"))

class Supplies(StatesGroup):
    choosing_supply_type = State()
    choosing_account_category = State()
    entering_quantity = State()


def register(dp: Dispatcher):
    @dp.message_handler(lambda msg: msg.text == "📦 Запросить расходники")
    async def request_supplies(message: types.Message):
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("🧑‍💻 Добавить аккаунты", callback_data="supply:accounts"),
            InlineKeyboardButton("🌐 Добавить домены", callback_data="supply:domains")
        )
        await message.answer("Выберите категорию расходников:", reply_markup=kb)
        await Supplies.choosing_supply_type.set()

    @dp.callback_query_handler(lambda c: c.data.startswith("supply:"), state=Supplies.choosing_supply_type)
    async def choose_supply_type(query: types.CallbackQuery, state: FSMContext):
        supply_type = query.data.split(":")[1]
        await state.update_data(supply_type=supply_type)

        if supply_type == "accounts":
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(
                InlineKeyboardButton("👤 Сетап КИНГ + 10 авторегов", callback_data="acc:setup_king"),
                InlineKeyboardButton("👤 КИНГ + 1–3 БМ", callback_data="acc:king_bm"),
                InlineKeyboardButton("👤 Автореги", callback_data="acc:autoregs")
            )
            await query.message.answer("Выберите категорию (если нет в наличии, то будет добавлено то, что есть):", reply_markup=kb)
            await Supplies.choosing_account_category.set()
        else:
            await query.message.answer("Введите количество доменов:", reply_markup=cancel_kb)
            await Supplies.entering_quantity.set()

    @dp.callback_query_handler(lambda c: c.data.startswith("acc:"), state=Supplies.choosing_account_category)
    async def choose_account_category(query: types.CallbackQuery, state: FSMContext):
        category = query.data.split(":")[1]
        await state.update_data(account_category=category)
        await query.message.answer("Сколько добавить?", reply_markup=cancel_kb)
        await Supplies.entering_quantity.set()

    @dp.message_handler(state=Supplies.entering_quantity)
    async def receive_quantity(message: types.Message, state: FSMContext):
        data = await state.get_data()
        qty = message.text.strip()
        user_id = message.from_user.id
        username = message.from_user.username or "нет username"
        supply_type = data.get("supply_type")

        text = f"🔔 Новая заявка на расходники от @{username} (ID: {user_id})\n"
        if supply_type == "accounts":
            cat = data.get("account_category")
            category_map = {
                "setup_king": "👤 Сетап КИНГ + 10 авторегов",
                "king_bm": "👤 КИНГ + 1–3 БМ",
                "autoregs": "👤 Автореги"
            }
            category = category_map.get(cat, cat)
            text += f"Тип: {category}\nКол-во: {qty}"
        else:
            text += f"🌐 ДОМЕНЫ\nКол-во: {qty}"

        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("✅ Выполнено", callback_data=f"supply_approve:{user_id}"),
            InlineKeyboardButton("❌ Отклонено", callback_data=f"supply_decline:{user_id}")
        )

        await message.bot.send_message(ADMIN_ID, text, reply_markup=kb)
        await message.answer("Заявка отправлена администратору 📤", reply_markup=menu_kb)
        await state.finish()

    @dp.callback_query_handler(lambda c: c.data and c.data.startswith(("supply_approve", "supply_decline")))
    async def process_callback(query: types.CallbackQuery):
        action, user_id = query.data.split(":")
        user_id = int(user_id)

        if action == "supply_approve":
            await query.bot.send_message(user_id, "✅ Ваша заявка была выполнена.")
        elif action == "supply_decline":
            await query.bot.send_message(user_id, "❌ Ваша заявка была отклонена.")
        await query.message.edit_reply_markup()
        await query.answer("Готово")
