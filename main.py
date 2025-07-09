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
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ContentType, BufferedInputFile
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
from io import BytesIO
import random
import string
import piexif
from datetime import datetime
import uuid
import zipfile

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

router = Router()
dp.include_router(router)

menu_kb_user = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="💰 Заказать пополнение")],
    [KeyboardButton(text="📂 Запросить расходники")],
    [KeyboardButton(text="🌐 Создать/починить лендинг")],
    [KeyboardButton(text="🖼️ Уникализатор")]
])

menu_kb_admin = ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
    [KeyboardButton(text="📢 Сделать рассылку")]
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
    uploading_multiple_zip_files = State()
    # unicalisation
    images_unicalization = State()
    unicalization_copies = State()
    # broadcast
    broadcast_collecting = State()

last_messages = {}

@router.message(Command("start"))
async def send_welcome(message: Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("👑 Админ-панель:", reply_markup=menu_kb_admin)
    else:
        await message.answer("Выберите действие:", reply_markup=menu_kb_user)

@router.message(F.text == "📢 Сделать рассылку")
async def admin_broadcast_start(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("❌ У вас нет доступа к этой функции.")
        return
    
    await state.clear()
    await state.update_data(broadcast_messages=[])
    await message.answer(
        "Отправьте мне любые сообщения, которые хотите разослать.\n"
        "Когда закончите, нажмите кнопку «🚀 Послать",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="🚀 Послать")],
                [KeyboardButton(text="❌ Отмена")]
            ],
            resize_keyboard=True,
            one_time_keyboard=False
        )
    )
    await state.set_state(Form.broadcast_collecting)

@router.message(Form.broadcast_collecting, F.text == "🚀 Послать")
async def send_broadcast(message: Message, state: FSMContext):
    await message.answer("Начинаю рассылку...")
    data = await state.get_data()
    messages = data.get("broadcast_messages", [])

    if not messages:
        await message.answer("⚠️ Список сообщений пуст. Рассылка отменена.", reply_markup=menu_kb_admin)
        await state.clear()
        return

    user_ids = get_user_ids_from_sheet()

    if not user_ids:
        await message.answer("⚠️ Список пользователей пуст. Рассылка отменена.", reply_markup=menu_kb_admin)
        await state.clear()
        return

    success_count = 0
    fail_count = 0

    for user_id in user_ids:
        user_success = True
        try:
            await bot.send_message(
                user_id,
                text="*📢 Сообщение от админа*",
                parse_mode="Markdown"
            )
        except Exception as e:
            print(f"Ошибка при отправке заголовка пользователю {user_id}: {e}")
            user_success = False
            continue  # если не удалось отправить заголовок, пропускаем этого пользователя
        
        for msg in messages:
            try:
                await bot.copy_message(chat_id=user_id, from_chat_id=msg["chat_id"], message_id=msg["message_id"])
            except Exception as e:
                print(f"Ошибка при отправке пользователю {user_id}: {e}")
                user_success = False
                # не делаем break, чтобы попытаться отправить остальные сообщения

        if user_success:
            success_count += 1
        else:
            fail_count += 1

    await message.answer(
        f"✅ Рассылка завершена.\n"
        f"Отправлено: {success_count}\n"
        f"Не доставлено: {fail_count}",
        reply_markup=menu_kb_admin
    )
    await state.clear()

@router.message(Form.broadcast_collecting, F.text == "❌ Отмена")
async def cancel_broadcast(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Рассылка отменена.", reply_markup=menu_kb_admin)

@router.message(Form.broadcast_collecting)
async def collect_broadcast_messages(message: Message, state: FSMContext):
    data = await state.get_data()
    broadcast_messages = data.get("broadcast_messages", [])

    # Сохраняем необходимую информацию из сообщения для пересылки
    msg_data = {
        "message_id": message.message_id,
        "chat_id": message.chat.id,  # для пересылки
    }
    broadcast_messages.append(msg_data)
    await state.update_data(broadcast_messages=broadcast_messages)

    await message.answer("Сообщение добавлено в рассылку. Отправьте ещё или нажмите «🚀 Послать».")

@router.message(F.text == "🌐 Создать/починить лендинг")
async def create_landing(message: Message, state: FSMContext):
    if not is_user_allowed(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой функции.")
        return
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

@router.message(F.text == "🖼️ Уникализатор")
async def images_unicalization_initiation(message: Message, state: FSMContext):
    if not is_user_allowed(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой функции.")
        return
    m1 = await message.answer("Загрузите изображение для уникализации (одно изображение)")
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.images_unicalization)

@router.message(Form.images_unicalization)
async def receive_image(message: Message, state: FSMContext):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return

    if message.photo or (message.document and message.document.mime_type.startswith('image/')):
        file_id = (
            message.photo[-1].file_id if message.photo
            else message.document.file_id
        )
        await state.update_data(unicalization_file_id=file_id)
        await message.answer("Введите количество уникализированных копий (например, 5):", reply_markup=cancel_kb)
        await state.set_state(Form.unicalization_copies)
    else:
        await message.answer("❌ Пожалуйста, отправьте изображение (фото или документ).", reply_markup=cancel_kb)

@router.message(Form.unicalization_copies)
async def receive_copy_count(message: Message, state: FSMContext, bot: Bot):
    if message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return

    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("❌ Введите корректное положительное число копий.", reply_markup=cancel_kb)
        return

    count = int(message.text)
    if count > 40:
        await message.answer("⚠️ Нельзя создать более 40 копий за раз. Пожалуйста, введите число от 1 до 40.", reply_markup=cancel_kb)
        return

    data = await state.get_data()
    unicalization_file_id = data.get("unicalization_file_id")

    await message.answer("🔄 Обрабатываю изображение...", reply_markup=cancel_kb)
    try:
        images_zip = await process_image(bot, unicalization_file_id, message.chat.id, count)
        await bot.send_document(message.chat.id, document=images_zip)
        await message.answer(f"✅ Уникализировано {count} копий.", reply_markup=menu_kb_user)
    except Exception as e:
        bugsnag.notify(e)
        await message.answer("❌ Произошла ошибка при обработке изображения.")

    await state.clear()

def generate_random_filename(length=12, ext='jpg'):
    random_str = ''.join(random.choices(string.ascii_letters + string.digits, k=length))
    return f"{random_str}.{ext}"

async def process_image(bot: Bot, file_id: str, user_id: int, copies: int) -> BufferedInputFile:
    file = await bot.get_file(file_id)
    file_content = await bot.download_file(file.file_path)
    file_name = file.file_path.split('/')[-1]
    name_parts = file_name.rsplit('.', 1)
    ext = name_parts[1] if len(name_parts) > 1 else 'jpg'

    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for i in range(copies):
            unique_file_name = generate_random_filename(ext=ext)

            img_processed, img_format = modify_image(BytesIO(file_content.getvalue()))
            output = BytesIO()
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

            if img_format.upper() == 'JPEG' and hasattr(img_processed, '_exif'):
                img_processed.save(output, format=img_format, exif=img_processed._exif, **save_params)
            elif img_format.upper() == 'PNG' and hasattr(img_processed, '_png_info'):
                img_processed.save(output, format=img_format, pnginfo=img_processed._png_info, **save_params)
            else:
                img_processed.save(output, format=img_format, **save_params)

            output.seek(0)
            zip_file.writestr(unique_file_name, output.read())

    zip_buffer.seek(0)
    zip_filename = f"images_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return BufferedInputFile(zip_buffer.read(), filename=zip_filename)

def modify_image(file_content: BytesIO) -> Tuple[Image.Image, str]:
    """Apply random filter and change metadata"""
    file_content.seek(0)
    img = Image.open(file_content)
    img_format = img.format or "JPEG"
    
    # Make a copy of the image to ensure it's writable
    img = img.copy()

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

    # === Prepare metadata based on format ===
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

        # Apply EXIF data directly during the return instead of reopening the image
        exif_bytes = piexif.dump(exif_dict)
        img._exif = exif_bytes  # Store the EXIF data to be used during save()
        
    elif img_format.upper() == 'PNG':
        # Create PNG metadata to be used during save()
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
        img._png_info = metadata  # Store PNG info to be used during save()

    # No need to save and reopen the image here - we'll return the processed image
    # and its format, and let the caller save it with the appropriate parameters
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
            msg = await message.answer("Загрузите ZIP архивы с файлами лендинга (можно несколько).\nКогда закончите, нажмите 'Готово':", reply_markup=ready_kb)
            last_messages[message.from_user.id] = [msg.message_id]
            await state.set_state(Form.uploading_multiple_zip_files)
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

    msg = await message.answer(
        "Загрузите ZIP архивы с картинками (можно несколько).\nКогда закончите, нажмите 'Готово':",
        reply_markup=ready_kb
    )
    last_messages[message.from_user.id] = [msg.message_id]
    await state.set_state(Form.uploading_multiple_zip_files)

@router.message(Form.uploading_multiple_zip_files)
async def upload_multiple_zip_files(message: Message, state: FSMContext):
    # Проверяем, не отмена ли это
    if message.text and message.text == "❌ Отмена":
        await cancel_handler(message, state)
        return
        
    # Проверяем, готово ли
    if message.text and message.text == "✅ Готово":
        data = await state.get_data()
        zip_files = data.get("zip_files", [])

        if not zip_files:
            await message.answer("❌ Необходимо загрузить хотя бы один ZIP архив перед завершением.")
            return

        # Переходим к отправке заявки
        await finalize_landing_request(message, state)
        return

    # Проверяем, что это документ
    if not message.document:
        await message.answer("Пожалуйста, загрузите ZIP архив или нажмите 'Готово' для завершения.")
        return

    # Проверяем, что это zip файл
    if message.document.mime_type != "application/zip":
        await message.answer("Пожалуйста, загрузите ZIP архив или нажмите 'Готово' для завершения.")
        return

    # Добавляем файл к списку
    data = await state.get_data()
    zip_files = data.get("zip_files", [])
    zip_files.append(message.document.file_id)
    await state.update_data(zip_files=zip_files)

    await message.answer(f"✅ Архив добавлен ({len(zip_files)} загружено). Можете загрузить ещё или нажать 'Готово'.")

async def finalize_landing_request(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = message.from_user.id
    username = message.from_user.username or "нет username"
    offer_name = data.get("offer_name")
    landing_category = data.get("landing_category")
    category = "Создать лендинг" if landing_category == "create" else "Починить лендинг" if landing_category == "repair" else "Неизвестно"
    specification = data.get("specification")
    spec_images = data.get("spec_image_ids", [])
    spec_docs = data.get("spec_doc_ids", [])
    order_id = shortuuid.uuid()
    canvas_link = data.get("canvas_link") if landing_category == "create" else None
    zip_files = data.get("zip_files", [])

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Взять в работу", callback_data=f"processing:{user_id}")],
        [InlineKeyboardButton(text="❌ Отклонено", callback_data=f"decline:{user_id}")]
    ])
        
    caption_text = "🖼️ Картинки" if landing_category == "create" else "📄 Лендинг"

    # Отправляем все ZIP файлы
    for i, zip_file_id in enumerate(zip_files, 1):
        caption = f"{caption_text} ({i}/{len(zip_files)})" if len(zip_files) > 1 else caption_text
        await bot.send_document(ADMIN_ID, document=zip_file_id, caption=caption)

    # Отправляем изображения и документы из ТЗ
    for file_id in spec_images:
        await bot.send_photo(ADMIN_ID, file_id)
    for file_id in spec_docs:
        await bot.send_document(ADMIN_ID, file_id)
        
    message_text = (
        f"🆔 Заявка: {order_id}\n"
        f"👤 От: @{username} (ID: {user_id})\n"
        f"📝 Оффер: {offer_name}\n"
        f"🔧 Категория: {category}\n"
        f"📦 Количество архивов: {len(zip_files)}\n"
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
    worksheet = table.get_worksheet(0)
    worksheet.append_row([order_id, username, user_id, offer_name, category, specification, canvas_link])
    
    await message.answer(f"Ваша заявка {order_id} отправлена администратору.", reply_markup=menu_kb_user)
    await state.clear()

@router.message(F.text == "💰 Заказать пополнение")
async def order_topup(message: Message, state: FSMContext):
    if not is_user_allowed(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой функции.")
        return
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
    await query.message.answer("Ваша заявка отправлена администратору.", reply_markup=menu_kb_user)
    await state.clear()
    await query.answer()

@router.message(F.text == "📂 Запросить расходники")
async def request_supplies(message: Message, state: FSMContext):
    if not is_user_allowed(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой функции.")
        return
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
    
    await message.answer("Ваша заявка отправлена администратору.", reply_markup=menu_kb_user)
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
    
    await message.answer("Ваша заявка отправлена администратору.", reply_markup=menu_kb_user)
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
    await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=menu_kb_user)

async def delete_last_messages(user_id, current_message):
    ids = last_messages.get(user_id, [])
    for msg_id in ids:
        try:
            await current_message.bot.delete_message(chat_id=user_id, message_id=msg_id)
        except Exception:
            pass
    last_messages[user_id] = []

def is_user_allowed(user_id: int) -> bool:
    user_ids = get_user_ids_from_sheet()
    if not user_ids:
        return False  # Если список пуст, доступ запрещен

    return user_id in user_ids

def get_user_ids_from_sheet() -> list[int]:
    gc = gspread.service_account(filename='credentials.json')
    table = gc.open_by_key(GOOGLE_SHEET_ID)
    worksheet = table.get_worksheet(1)
    user_ids = worksheet.col_values(1)

    return [int(user_id) for user_id in user_ids if user_id.isdigit()]

async def main():
    await bot.delete_webhook(drop_pending_updates=True)  # если запускаешь polling
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
