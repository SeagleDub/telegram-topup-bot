import os
import asyncio
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import shortuuid
import gspread
from dotenv import load_dotenv
import bugsnag

load_dotenv()

API_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
BUGSNAG_TOKEN = os.getenv("BUGSNAG_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bugsnag.configure(
    api_key=BUGSNAG_TOKEN
)

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

router = Router()
dp.include_router(router)

menu_kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="💰 Заказать пополнение")],
    [KeyboardButton(text="📂 Запросить расходники")],
    [KeyboardButton(text="🌐 Создать/починить лендинг")]
])

cancel_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, keyboard=[
    [KeyboardButton(text="❌ Отмена")]
])

class Form(StatesGroup):
    waiting_for_bank = State()
    waiting_for_amount = State()
    waiting_for_type = State()
    choosing_supply_category = State()
    choosing_account_type = State()
    entering_account_quantity = State()
    entering_domain_quantity = State()
    # landing functionality
    choosing_offer_category = State()
    writing_offer_name = State()
    writing_specification = State()
    uploading_text_file = State()
    uploading_images = State()

last_messages = {}

@router.message(Command("start"))
async def send_welcome(message: Message):
    await message.answer("Выберите действие:", reply_markup=menu_kb)

@router.message(F.text == "🌐 Создать/починить лендинг")
async def create_landing(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💻 Создать лендинг", callback_data="landing:create")],
        [InlineKeyboardButton(text="🔧 Починить лендинг", callback_data="landing:repair")]
    ])
    m1 = await message.answer("Выберите действие:", reply_markup=kb)
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.choosing_offer_category)

@router.callback_query(F.data.startswith("landing:"), Form.choosing_offer_category)
async def landing_category_selected(query: CallbackQuery, state: FSMContext):
    await delete_last_messages(query.from_user.id, query.message)
    _, category = query.data.split(":")

    await state.update_data(landing_category=category)
    msg = await query.message.answer("Введите название оффера:", reply_markup=cancel_kb)
    last_messages[query.from_user.id] = [msg.message_id]
    await state.set_state(Form.writing_offer_name)
    await query.answer()

@router.message(Form.writing_offer_name)
async def write_specification(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return

    offer_name = message.text.strip()
    await state.update_data(offer_name=offer_name)

    msg = await message.answer("Введите ТЗ для лендинга:", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [msg.message_id]
    await state.set_state(Form.writing_specification)


@router.message(Form.writing_specification)
async def upload_text_file(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return

    specification = message.text.strip()
    await state.update_data(specification=specification)

    # Попросим загрузить файлы
    msg = await message.answer("Загрузите Excel файл с текстом сайта:", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [msg.message_id]
    await state.set_state(Form.uploading_text_file)


@router.message(Form.uploading_text_file)
async def upload_text(message: Message, state: FSMContext):
    # Проверяем, не отмена ли это
    if message.text and message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
        
    # Проверяем, что это документ
    if not message.document:
        await message.answer("Пожалуйста, загрузите Excel файл.")
        return
        
    # Проверяем, что это Excel файл
    if message.document.mime_type not in ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.ms-excel"]:
        await message.answer("Пожалуйста, загрузите Excel файл.")
        return

    # Сохраняем файл
    await state.update_data(text_file=message.document.file_id)

    msg = await message.answer("Загрузите ZIP архив с картинками:", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [msg.message_id]
    await state.set_state(Form.uploading_images)

@router.message(Form.uploading_images)
async def upload_images(message: Message, state: FSMContext):
    # Проверяем, не отмена ли это
    if message.text and message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
        
    # Проверяем, что это документ
    if not message.document:
        await message.answer("Пожалуйста, загрузите ZIP архив с картинками.")
        return

    # Проверяем, что это zip файл
    if message.document.mime_type != "application/zip":
        await message.answer("Пожалуйста, загрузите ZIP архив с картинками.")
        return

    # Сохраняем файл
    await state.update_data(images_file=message.document.file_id)

    data = await state.get_data()
    user_id = message.from_user.id
    username = message.from_user.username or "нет username"
    offer_name = data.get("offer_name")
    category = data.get("landing_category")
    specification = data.get("specification")
    text_file = data.get("text_file")
    images_file = data.get("images_file")
    order_id = shortuuid.uuid()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"approve:{user_id}")],
        [InlineKeyboardButton(text="❌ Отклонено", callback_data=f"decline:{user_id}")]
    ])

    await bot.send_document(ADMIN_ID, document=text_file, caption="📄 Текст")
    await bot.send_document(ADMIN_ID, document=images_file, caption="🖼️ Картинки")
    await bot.send_message(
        ADMIN_ID,
        f"🆔 Заявка: {order_id}\n"
        f"👤 От: @{username} (ID: {user_id})\n"
        f"📝 Оффер: {offer_name}\n"
        f"🔧 Категория: {category}\n"
        f"📝 ТЗ: {specification}\n",
        reply_markup=kb
    )
    
    gc = gspread.service_account(filename='credentials.json')
    table = gc.open_by_key(table_url)
    worksheet = table.sheet1
    worksheet.append_row([order_id, username, user_id, offer_name, category, specification])
    
    await message.answer(f"Ваша заявка {order_id} отправлена администратору.", reply_markup=menu_kb)
    await state.clear()

@router.message(F.text == "💰 Заказать пополнение")
async def order_topup(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏦 AdsCard", callback_data="bank:adscard"),
         InlineKeyboardButton(text="💳 Traffic.cards", callback_data="bank:trafficcards")]
    ])
    m1 = await message.answer("Выберите банк:", reply_markup=kb)
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.waiting_for_bank)

@router.callback_query(F.data.startswith("bank:"), Form.waiting_for_bank)
async def bank_selected(query: CallbackQuery, state: FSMContext):
    await delete_last_messages(query.from_user.id, query.message)
    _, bank = query.data.split(":")
    await state.update_data(bank=bank)
    msg = await query.message.answer("Введите сумму пополнения:", reply_markup=cancel_kb)
    last_messages[query.from_user.id] = [msg.message_id]
    await state.set_state(Form.waiting_for_amount)
    await query.answer()

@router.message(Form.waiting_for_amount)
async def get_amount(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    amount = message.text.strip()
    if not amount.isdigit():
        await message.answer("Пожалуйста, введите корректную сумму.")
        return
    await delete_last_messages(message.from_user.id, message)
    await state.update_data(amount=amount)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Срочное", callback_data="type:urgent"),
         InlineKeyboardButton(text="🕘 Не срочное (до 21:00)", callback_data="type:normal")]
    ])
    m1 = await message.answer("Выберите тип пополнения:", reply_markup=kb)
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.waiting_for_type)

@router.callback_query(F.data.startswith("type:"), Form.waiting_for_type)
async def type_selected(query: CallbackQuery, state: FSMContext):
    await delete_last_messages(query.from_user.id, query.message)
    _, topup_type = query.data.split(":")
    await state.update_data(topup_type=topup_type)

    user_id = query.from_user.id
    username = query.from_user.username or "нет username"
    data = await state.get_data()

    bank = data.get("bank", "не указан")
    amount = data.get("amount", "не указано")
    topup_type_text = "⚡ Срочное" if topup_type == "urgent" else "🕘 Не срочное (до 21:00)"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"approve:{user_id}"),
         InlineKeyboardButton(text="❌ Отклонено", callback_data=f"decline:{user_id}")]
    ])

    await bot.send_message(
        ADMIN_ID,
        f"🔔 Новая заявка от @{username} (ID: {user_id})\n"
        f"🏦 Банк: {bank}\n"
        f"💳 Сумма: {amount}\n"
        f"📌 Тип: {topup_type_text}",
        reply_markup=kb
    )
    await query.message.answer("Ваша заявка отправлена администратору.", reply_markup=menu_kb)
    await state.clear()
    await query.answer()

@router.message(F.text == "📂 Запросить расходники")
async def request_supplies(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Аккаунты", callback_data="supply:accounts")],
        [InlineKeyboardButton(text="🌐 Домены", callback_data="supply:domains")]
    ])
    m1 = await message.answer("Выберите категорию расходников:", reply_markup=kb)
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.choosing_supply_category)

@router.callback_query(F.data.startswith("supply:"), Form.choosing_supply_category)
async def supply_category_selected(query: CallbackQuery, state: FSMContext):
    await delete_last_messages(query.from_user.id, query.message)
    _, category = query.data.split(":")
    await state.update_data(category=category)
    
    if category == "accounts":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👤 Сетап КИНГ+\n10 авторегов", callback_data="acc:set1")],
            [InlineKeyboardButton(text="👤 КИНГ + 1-3 БМ", callback_data="acc:set2")],
            [InlineKeyboardButton(text="👤 Автореги", callback_data="acc:set3")]
        ])
        m1 = await query.message.answer("Выберите категорию (если нет в наличии, то будет добавлено то, что есть):", reply_markup=kb)
        m2 = await query.message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
        last_messages[query.from_user.id] = [m1.message_id, m2.message_id]
        await state.set_state(Form.choosing_account_type)
    elif category == "domains":
        msg = await query.message.answer("Введите количество доменов:", reply_markup=cancel_kb)
        last_messages[query.from_user.id] = [msg.message_id]
        await state.set_state(Form.entering_domain_quantity)
    
    await query.answer()

@router.callback_query(F.data.startswith("acc:"), Form.choosing_account_type)
async def account_type_chosen(query: CallbackQuery, state: FSMContext):
    await delete_last_messages(query.from_user.id, query.message)
    _, acc_type = query.data.split(":")
    await state.update_data(account_type=acc_type)
    
    msg = await query.message.answer("Введите количество аккаунтов:", reply_markup=cancel_kb)
    last_messages[query.from_user.id] = [msg.message_id]
    await state.set_state(Form.entering_account_quantity)
    await query.answer()

@router.message(Form.entering_account_quantity)
async def get_account_quantity(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    
    quantity = message.text.strip()
    if not quantity.isdigit():
        await message.answer("Пожалуйста, введите корректное количество.")
        return
    
    await delete_last_messages(message.from_user.id, message)
    await state.update_data(quantity=quantity)
    
    user_id = message.from_user.id
    username = message.from_user.username or "нет username"
    data = await state.get_data()
    
    account_type_text = {
        "set1": "👤 Сетап КИНГ+10 авторегов",
        "set2": "👤 КИНГ + 1-3 БМ",
        "set3": "👤 Автореги"
    }.get(data.get("account_type"), "👤 Неизвестно")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"approve:{user_id}"),
         InlineKeyboardButton(text="❌ Отклонено", callback_data=f"decline:{user_id}")]
    ])
    
    await bot.send_message(
        ADMIN_ID,
        f"🔔 Новый запрос на расходники от @{username} (ID: {user_id})\n"
        f"📁 Тип: Аккаунты\n"
        f"🔑 Платформа: {account_type_text}\n"
        f"🔢 Количество: {quantity}",
        reply_markup=kb
    )
    
    await message.answer("Ваша заявка отправлена администратору.", reply_markup=menu_kb)
    await state.clear()

@router.message(Form.entering_domain_quantity)
async def get_domain_quantity(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
    
    quantity = message.text.strip()
    if not quantity.isdigit():
        await message.answer("Пожалуйста, введите корректное количество.")
        return
    
    await delete_last_messages(message.from_user.id, message)
    await state.update_data(quantity=quantity)
    
    user_id = message.from_user.id
    username = message.from_user.username or "нет username"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"approve:{user_id}"),
         InlineKeyboardButton(text="❌ Отклонено", callback_data=f"decline:{user_id}")]
    ])
    
    await bot.send_message(
        ADMIN_ID,
        f"🔔 Новый запрос на расходники от @{username} (ID: {user_id})\n"
        f"📁 Тип: Домены\n"
        f"🔢 Количество: {quantity}",
        reply_markup=kb
    )
    
    await message.answer("Ваша заявка отправлена администратору.", reply_markup=menu_kb)
    await state.clear()

@router.callback_query(F.data.startswith("approve:"))
async def approve_request(query: CallbackQuery):
    _, user_id = query.data.split(":")
    user_id = int(user_id)
    
    await bot.send_message(
        user_id,
        "✅ Ваша заявка одобрена и выполнена администратором."
    )
    
    await query.message.edit_text(
        f"{query.message.text}\n\n✅ ВЫПОЛНЕНО"
    )
    await query.answer("Пользователь уведомлен об одобрении")

@router.callback_query(F.data.startswith("decline:"))
async def decline_request(query: CallbackQuery):
    _, user_id = query.data.split(":")
    user_id = int(user_id)
    
    await bot.send_message(
        user_id,
        "❌ Ваша заявка отклонена администратором."
    )
    
    await query.message.edit_text(
        f"{query.message.text}\n\n❌ ОТКЛОНЕНО"
    )
    await query.answer("Пользователь уведомлен об отклонении")

@router.message(F.text == "❌ Отмена")
async def cancel_handler(message: Message, state: FSMContext):
    await delete_last_messages(message.from_user.id, message)
    await state.clear()
    await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=menu_kb)

async def delete_last_messages(user_id, current_message):
    ids = last_messages.get(user_id, [])
    for msg_id in ids:
        try:
            await current_message.bot.delete_message(chat_id=user_id, message_id=msg_id)
        except Exception:
            pass
    last_messages[user_id] = []

async def main():
    await bot.delete_webhook(drop_pending_updates=True)  # если запускаешь polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
