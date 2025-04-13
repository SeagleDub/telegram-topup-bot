from aiogram import Dispatcher, types
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from config import ADMIN_ID

menu_kb = ReplyKeyboardMarkup(resize_keyboard=True)
menu_kb.add(KeyboardButton("💰 Заказать пополнение"))
menu_kb.add(KeyboardButton("📦 Запросить расходники"))

class Topup(StatesGroup):
    waiting_for_amount = State()


def register(dp: Dispatcher):
    @dp.message_handler(lambda msg: msg.text == "💰 Заказать пополнение")
    async def topup_start(message: types.Message):
        await message.answer("Введите сумму пополнения:")
        await Topup.waiting_for_amount.set()

    @dp.message_handler(state=Topup.waiting_for_amount)
    async def topup_amount(message: types.Message, state: FSMContext):
        amount = message.text.strip()
        user_id = message.from_user.id
        username = message.from_user.username or "нет username"

        kb = InlineKeyboardMarkup()
        kb.add(
            InlineKeyboardButton("✅ Выполнено", callback_data=f"topup_approve:{user_id}"),
            InlineKeyboardButton("❌ Отклонено", callback_data=f"topup_decline:{user_id}")
        )

        await message.bot.send_message(
            ADMIN_ID,
            f"🔔 Новая заявка на пополнение от @{username} (ID: {user_id})\n💳 Сумма: {amount}",
            reply_markup=kb
        )
        await message.answer("Заявка отправлена админу ✅", reply_markup=menu_kb)
        await state.finish()

    @dp.callback_query_handler(lambda c: c.data.startswith("topup_"))
    async def process_topup_callback(query: types.CallbackQuery):
        action, user_id = query.data.split(":")
        user_id = int(user_id)

        if action == "topup_approve":
            await query.bot.send_message(user_id, "✅ Ваша заявка была выполнена.")
            await query.message.edit_reply_markup()
            await query.answer("Отмечено как выполнено.")
        elif action == "topup_decline":
            await query.bot.send_message(user_id, "❌ Ваша заявка была отклонена.")
            await query.message.edit_reply_markup()
            await query.answer("Отмечено как отклонено.")
