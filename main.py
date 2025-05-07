from dotenv import load_dotenv
import bugsnag
import os

load_dotenv()

API_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
BUGSNAG_TOKEN = os.getenv("BUGSNAG_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bugsnag.configure(
    api_key=BUGSNAG_TOKEN
)

import asyncio
from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ContentType, InputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import shortuuid
import gspread
import re
from PIL import Image, ImageFilter, ImageEnhance
from PIL.PngImagePlugin import PngInfo
from PIL import TiffImagePlugin
from typing import Union, Tuple, Optional, Dict, Any
import io
import random
import piexif
from datetime import datetime

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

router = Router()
dp.include_router(router)

menu_kb = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="💰 Заказать пополнение")],
    [KeyboardButton(text="📂 Запросить расходники")],
    [KeyboardButton(text="🌐 Создать/починить лендинг")],
    [KeyboardButton(text="🖼️ Уникализатор")]
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
    entering_canvas_link = State()
    uploading_zip_file = State()
    # unicalisation
    images_unicalization = State()

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
async def write_offer_name(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return

    offer_name = message.text.strip()
    await state.update_data(offer_name=offer_name)

    msg = await message.answer("Введите ТЗ для лендинга:", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [msg.message_id]
    await state.set_state(Form.writing_specification)

ready_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Готово")],
        [KeyboardButton(text="❌ Отмена")]
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)

MAX_IMAGES = 10

@router.message(F.text == "🖼️ Уникализатор")
async def images_unicalization_initiation(message: Message, state: FSMContext):
    m1 = await message.answer("Загрузите изображения")
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.images_unicalization)

@router.message(Form.images_unicalization)
async def images_unicalization(message: Message, state: FSMContext, bot: Bot):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
        
    data = await state.get_data()
    uniq_image_ids = data.get("uniq_image_ids", [])
    uniq_doc_ids = data.get("uniq_doc_ids", [])
    
    if message.text == "✅ Готово":
        if not uniq_image_ids and not uniq_doc_ids:
            await message.answer("У вас нет изображений для обработки. Пожалуйста, загрузите хотя бы одно изображение.", 
                                reply_markup=ready_kb)
            return
            
        await message.answer(f"Подождите, пока изображения обработаются.", reply_markup=cancel_kb)
        try:
            # Process images and send back to user
            for file_id in uniq_image_ids:
                processed_file = await process_image(bot, file_id, message.chat.id)
                await bot.send_photo(ADMIN_ID, photo=processed_file)
                
            # Process documents and send back to user
            for file_id in uniq_doc_ids:
                processed_file, file_name = await process_document(bot, file_id, message.chat.id)
                await bot.send_document(ADMIN_ID, document=processed_file, file_name=file_name)
        except Exception as e:
            await message.answer(f"❌ Ошибка при отправке фото: {e}")
            
        await message.answer(f"Процесс уникализации завершен. Всего уникализировано: {len(uniq_image_ids) + len(uniq_doc_ids)} изображений.", 
                            reply_markup=menu_kb)
        await state.clear()
        return
        
    total_images = len(uniq_image_ids) + len(uniq_doc_ids)
    if total_images >= MAX_IMAGES:
        await message.answer(
            f"Достигнут лимит в {MAX_IMAGES} изображений. Нажмите *Готово* для обработки или *Отмена* для сброса.",
            reply_markup=ready_kb,
            parse_mode="Markdown"
        )
        return
        
    if message.photo:
        largest_photo = message.photo[-1]
        uniq_image_ids.append(largest_photo.file_id)
        
        await state.update_data(uniq_image_ids=uniq_image_ids)
        
        await message.answer(
            f"✅ Добавлено фото {len(uniq_image_ids) + len(uniq_doc_ids)}/{MAX_IMAGES}.\nМожете отправить ещё изображение или нажмите *Готово*",
            reply_markup=ready_kb,
            parse_mode="Markdown"
        )
    elif message.document:
        if message.document.mime_type and message.document.mime_type.startswith('image/'):
            uniq_doc_ids.append(message.document.file_id)
            
            await state.update_data(uniq_doc_ids=uniq_doc_ids)
            
            await message.answer(
                f"✅ Добавлен документ {len(uniq_image_ids) + len(uniq_doc_ids)}/{MAX_IMAGES}.\nМожете отправить ещё изображение или нажмите *Готово*",
                reply_markup=ready_kb,
                parse_mode="Markdown"
            )
        else:
            await message.answer(
                "❌ Документ не является изображением. Пожалуйста, отправьте только графические файлы.",
                reply_markup=ready_kb
            )
    else:
        await message.answer(
            "Пожалуйста, отправьте фото или изображение.",
            reply_markup=ready_kb
        )

async def process_image(bot: Bot, file_id: str, user_id: int) -> InputFile:
    """Process a photo: apply random filter and change metadata, return InputFile"""
    file = await bot.get_file(file_id)
    file_content = await bot.download_file(file.file_path)

    # Обработка изображения
    img_processed, img_format = modify_image(file_content)

    # Сохранение изображения в память
    output = io.BytesIO()
    save_params = {}

    if img_format.upper() in ('JPEG', 'JPG'):
        save_params['quality'] = random.randint(92, 98)
        save_params['optimize'] = True
    elif img_format.upper() == 'PNG':
        save_params['optimize'] = True
        save_params['compress_level'] = random.randint(6, 9)
    elif img_format.upper() == 'WEBP':
        save_params['quality'] = random.randint(92, 98)
        save_params['method'] = 6
    elif img_format.upper() == 'TIFF':
        save_params['compression'] = 'tiff_lzw'

    img_processed.save(output, format=img_format, **save_params)
    output.seek(0)

    # Вернуть InputFile с указанием имени
    return InputFile(output, filename=f"processed_{file_id}.{img_format.lower()}")

async def process_document(bot: Bot, file_id: str, user_id: int) -> InputFile:
    """Process a document assuming it's an image, return InputFile"""
    file = await bot.get_file(file_id)
    file_content = await bot.download_file(file.file_path)
    file_name = file.file_path.split('/')[-1]

    # Уникальное имя
    name_parts = file_name.rsplit('.', 1)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if len(name_parts) > 1:
        unique_file_name = f"{name_parts[0]}_{timestamp}.{name_parts[1]}"
    else:
        unique_file_name = f"{file_name}_{timestamp}"

    # Обработка изображения
    img_processed, img_format = modify_image(file_content)

    output = io.BytesIO()
    save_params = {}

    if img_format.upper() in ('JPEG', 'JPG'):
        save_params['quality'] = random.randint(92, 98)
        save_params['optimize'] = True
    elif img_format.upper() == 'PNG':
        save_params['optimize'] = True
        save_params['compress_level'] = random.randint(6, 9)
    elif img_format.upper() == 'WEBP':
        save_params['quality'] = random.randint(92, 98)
        save_params['method'] = 6
    elif img_format.upper() == 'TIFF':
        save_params['compression'] = 'tiff_lzw'

    img_processed.save(output, format=img_format, **save_params)
    output.seek(0)

    return InputFile(output, filename=unique_file_name)


def modify_image(file_content: bytes) -> Tuple[Image.Image, str]:
    """Apply random subtle filter and change metadata"""
    img = Image.open(io.BytesIO(file_content))
    img_format = img.format or "JPEG"

    if img.mode == 'P':
        img = img.convert('RGBA' if 'transparency' in img.info else 'RGB')

    # Apply a subtle random transformation
    filter_type = random.choice(['brightness', 'contrast', 'color', 'sharpness', 'blur', 'noise', 'rotate'])

    if filter_type == 'brightness':
        img = ImageEnhance.Brightness(img).enhance(random.uniform(0.99, 1.01))
    elif filter_type == 'contrast':
        img = ImageEnhance.Contrast(img).enhance(random.uniform(0.99, 1.01))
    elif filter_type == 'color' and img.mode in ('RGB', 'RGBA'):
        img = ImageEnhance.Color(img).enhance(random.uniform(0.99, 1.01))
    elif filter_type == 'sharpness':
        img = ImageEnhance.Sharpness(img).enhance(random.uniform(0.99, 1.01))
    elif filter_type == 'blur':
        img = img.filter(ImageFilter.GaussianBlur(radius=0.1))
    elif filter_type == 'noise' and img.mode in ('RGB', 'RGBA'):
        for _ in range(3):
            x, y = random.randint(0, img.width - 1), random.randint(0, img.height - 1)
            px = list(img.getpixel((x, y)))
            for i in range(min(3, len(px))):
                px[i] = max(0, min(255, px[i] + random.randint(-1, 1)))
            img.putpixel((x, y), tuple(px))
    elif filter_type == 'rotate':
        img = img.rotate(random.uniform(-0.1, 0.1), resample=Image.BICUBIC, expand=False)

    # Minor pixel edit
    if img.mode in ('RGB', 'RGBA'):
        x, y = random.randint(0, img.width - 1), random.randint(0, img.height - 1)
        px = list(img.getpixel((x, y)))
        i = random.randint(0, min(3, len(px)) - 1)
        px[i] = max(0, min(255, px[i] + random.choice([-1, 1])))
        img.putpixel((x, y), tuple(px))

    unique_id = uuid.uuid4().hex
    current_time = datetime.now().strftime("%Y:%m:%d %H:%M:%S")

    # === Metadata handling by format ===
    if img_format.upper() in ('JPEG', 'JPG'):
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        exif_dict["0th"][piexif.ImageIFD.Make] = f"Camera{random.randint(1,9999)}".encode()
        exif_dict["0th"][piexif.ImageIFD.Model] = f"Model{random.randint(1,9999)}".encode()
        exif_dict["0th"][piexif.ImageIFD.Software] = f"Software{random.randint(1,9999)}".encode()
        exif_dict["0th"][piexif.ImageIFD.Artist] = f"Artist{random.randint(1,999)}".encode()
        exif_dict["0th"][piexif.ImageIFD.Copyright] = f"Copyright{random.randint(1,999)}".encode()
        exif_dict["0th"][piexif.ImageIFD.ImageDescription] = f"Image{random.randint(1,9999)}".encode()
        exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = current_time.encode()
        exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = current_time.encode()
        exif_dict["Exif"][piexif.ExifIFD.ExifVersion] = b"0230"
        exif_dict["Exif"][piexif.ExifIFD.LensMake] = f"Lens{random.randint(1,999)}".encode()
        exif_dict["Exif"][piexif.ExifIFD.LensModel] = f"LensModel{random.randint(1,999)}".encode()
        exif_dict["Exif"][piexif.ExifIFD.UserComment] = f"UniqueID:{unique_id}".encode()

        lat = abs(random.uniform(-90, 90))
        lon = abs(random.uniform(-180, 180))
        lat_ref = 'N' if lat >= 0 else 'S'
        lon_ref = 'E' if lon >= 0 else 'W'

        def to_deg_tuple(val):
            deg = int(val)
            min_ = int((val - deg) * 60)
            sec = int((((val - deg) * 60) - min_) * 60)
            return ((deg, 1), (min_, 1), (sec, 1))

        exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = lat_ref.encode()
        exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = to_deg_tuple(lat)
        exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = lon_ref.encode()
        exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = to_deg_tuple(lon)
        exif_dict["GPS"][piexif.GPSIFD.GPSDateStamp] = datetime.now().strftime("%Y:%m:%d").encode()

        exif_bytes = piexif.dump(exif_dict)
        with io.BytesIO() as output:
            img.save(output, format=img_format, exif=exif_bytes, quality=95)
            output.seek(0)
            img = Image.open(output)

    elif img_format.upper() == 'PNG':
        metadata = PngInfo()
        metadata.add_text("Software", f"Editor{random.randint(1, 9999)}")
        metadata.add_text("Creation Time", current_time)
        metadata.add_text("UniqueID", unique_id)
        metadata.add_text("Description", f"Image {random.randint(1000, 9999)}")
        metadata.add_text("Author", f"Author{random.randint(1, 999)}")
        metadata.add_text("Copyright", f"Copyright{random.randint(1, 999)}")
        metadata.add_text("Comment", f"Processed on {current_time}")
        metadata.add_text("Disclaimer", f"Generated image {random.randint(1, 9999)}")
        metadata.add_text("Source", f"Source{random.randint(1, 999)}")
        metadata.add_text("Title", f"Title{random.randint(1, 999)}")
        with io.BytesIO() as output:
            img.save(output, format="PNG", pnginfo=metadata)
            output.seek(0)
            img = Image.open(output)

    elif img_format.upper() == 'TIFF':
        with io.BytesIO() as output:
            img.save(output, format="TIFF", description=f"Image {random.randint(1000, 9999)}")
            output.seek(0)
            img = Image.open(output)

    elif img_format.upper() == 'WEBP':
        with io.BytesIO() as output:
            img.save(output, format="WEBP", exif=b"UniqueID:" + unique_id.encode())
            output.seek(0)
            img = Image.open(output)

    return img, img_format

@router.message(Form.writing_specification)
async def write_specification(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return

    if message.text == "✅ Готово":
        data = await state.get_data()
        landing_category = data.get("landing_category")

        if landing_category == "create":
            msg = await message.answer("Введите ссылку на canvas из Chat GPT:", reply_markup=cancel_kb)
            last_messages[message.from_user.id] = [msg.message_id]
            await state.set_state(Form.entering_canvas_link)
        else:
            msg = await message.answer("Загрузите ZIP архив с файлами лендинга:", reply_markup=cancel_kb)
            last_messages[message.from_user.id] = [msg.message_id]
            await state.set_state(Form.uploading_zip_file)
        return

    data = await state.get_data()
    spec_text = data.get("specification", "")
    spec_image_ids = data.get("spec_image_ids", [])
    spec_doc_ids = data.get("spec_doc_ids", [])

    if message.text:
        spec_text += ("\n" if spec_text else "") + message.text.strip()

    if message.photo:
        largest_photo = message.photo[-1]
        spec_image_ids.append(largest_photo.file_id)

    elif message.document:
        spec_doc_ids.append(message.document.file_id)

    await state.update_data(
        specification=spec_text,
        spec_image_ids=spec_image_ids,
        spec_doc_ids=spec_doc_ids
    )

    await message.answer(
        "✅ Добавлено. Можете отправить ещё текст или изображение.\nКогда всё готово — нажмите *Готово*",
        reply_markup=ready_kb,
        parse_mode="Markdown"
    )

VALID_LINK_REGEX = re.compile(r"^https:\/\/chatgpt\.com\/canvas\/shared\/[a-zA-Z0-9]+$")

@router.message(Form.entering_canvas_link)
async def enter_canvas_link(message: Message, state: FSMContext):
    # Проверяем, не отмена ли это
    if message.text and message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
        
   # Проверяем, что текст является корректной ссылкой
    if not message.text or not VALID_LINK_REGEX.match(message.text.strip()):
        await message.answer("Пожалуйста, отправьте корректную ссылку вида:\nhttps://chatgpt.com/canvas/shared/...")
        return

    # Сохраняем ссылку
    await state.update_data(canvas_link=message.text.strip())

    msg = await message.answer("Загрузите ZIP архив с картинками:", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [msg.message_id]
    await state.set_state(Form.uploading_zip_file)

@router.message(Form.uploading_zip_file)
async def upload_zip_file(message: Message, state: FSMContext):
    # Проверяем, не отмена ли это
    if message.text and message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
        
    data = await state.get_data()
    landing_category = data.get("landing_category")
        
    text_message = "Пожалуйста, загрузите ZIP архив с картинками." if landing_category == "create" else "Пожалуйста, загрузите ZIP архив с лендингом."
        
    # Проверяем, что это документ
    if not message.document:
        await message.answer(text_message)
        return

    # Проверяем, что это zip файл
    if message.document.mime_type != "application/zip":
        await message.answer(text_message)
        return

    # Сохраняем файл
    await state.update_data(zip_file=message.document.file_id)

    data = await state.get_data()
    user_id = message.from_user.id
    username = message.from_user.username or "нет username"
    offer_name = data.get("offer_name")
    category = "Создать лендинг" if landing_category == "create" else "Починить лендинг" if landing_category == "repair" else "Неизвестно"
    specification = data.get("specification")
    spec_images = data.get("spec_image_ids", [])
    spec_docs = data.get("spec_doc_ids", [])
    order_id = shortuuid.uuid()
    canvas_link = data.get("canvas_link") if landing_category == "create" else None

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Взять в работу", callback_data=f"processing:{user_id}")],
        [InlineKeyboardButton(text="❌ Отклонено", callback_data=f"decline:{user_id}")]
    ])
        
    caption_text = "🖼️ Картинки" if landing_category == "create" else "📄 Лендинг"
    zip_file = data.get("zip_file")
    await bot.send_document(ADMIN_ID, document=zip_file, caption=caption_text)

    for file_id in spec_images:
        await bot.send_photo(ADMIN_ID, file_id)
    for file_id in spec_docs:
        await bot.send_document(ADMIN_ID, file_id)
        
    message_text = (
        f"🆔 Заявка: {order_id}\n"
        f"👤 От: @{username} (ID: {user_id})\n"
        f"📝 Оффер: {offer_name}\n"
        f"🔧 Категория: {category}\n"
        f"📝 ТЗ: {specification}\n"
        f"{f'🔗 Ссылка на Canvas: {canvas_link}\n' if canvas_link else ''}"
    )
    
    await bot.send_message(
        ADMIN_ID,
        message_text,
        reply_markup=kb
    )
    
    gc = gspread.service_account(filename='credentials.json')
    table = gc.open_by_key(GOOGLE_SHEET_ID)
    worksheet = table.sheet1
    worksheet.append_row([order_id, username, user_id, offer_name, category, specification, canvas_link])
    
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
    
@router.callback_query(F.data.startswith("processing:"))
async def processing_request(query: CallbackQuery):
    _, user_id = query.data.split(":")
    user_id = int(user_id)
    
    await bot.send_message(
        user_id,
        "✅ Ваша заявка рассмотрена и взята в работу."
    )
    
    await query.message.edit_text(
        f"{query.message.text}\n\n✅ В РАБОТЕ"
    )
    await query.answer("Пользователь уведомлен о взятии в работу")

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
