"""
Система уникализации изображений
"""
import random
import string
import uuid
import zipfile
import hashlib
from datetime import datetime
from io import BytesIO
from typing import Tuple
import piexif
from PIL import Image, ImageFilter, ImageEnhance
from PIL.PngImagePlugin import PngInfo
from aiogram import Router, F, Bot
from aiogram.types import Message, BufferedInputFile
from aiogram.fsm.context import FSMContext
import bugsnag

from states import Form
from keyboards import cancel_kb, get_menu_keyboard
from utils import is_user_allowed, last_messages

router = Router()

def generate_random_filename(length=12, ext='jpg'):
    """Генерирует случайное имя файла"""
    random_str = ''.join(random.choices(string.ascii_letters + string.digits, k=length))
    return f"{random_str}.{ext}"


def build_processed_image_bytes(image_data: bytes) -> bytes:
    """Уникализирует одно изображение и возвращает его байты"""
    img_processed, img_format = modify_image(BytesIO(image_data))
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
    return output.read()

def modify_image(file_content: BytesIO) -> Tuple[Image.Image, str]:
    """Применяет случайные фильтры и изменяет метаданные изображения"""
    file_content.seek(0)
    img = Image.open(file_content)
    img_format = img.format or "JPEG"

    # Создаем копию изображения для обеспечения возможности записи
    img = img.copy()

    if img.mode == 'P':
        img = img.convert('RGBA' if 'transparency' in img.info else 'RGB')

    # Применяем случайное тонкое преобразование
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

    # Незначительное редактирование пикселей
    if img.mode in ('RGB', 'RGBA'):
        x, y = random.randint(0, img.width - 1), random.randint(0, img.height - 1)
        px = list(img.getpixel((x, y)))
        i = random.randint(0, min(3, len(px)) - 1)
        px[i] = max(0, min(255, px[i] + random.choice([-1, 1])))
        img.putpixel((x, y), tuple(px))

    unique_id = uuid.uuid4().hex
    current_time = datetime.now().strftime("%Y:%m:%d %H:%M:%S")

    # Подготавливаем метаданные в зависимости от формата
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

        # Применяем EXIF данные напрямую во время возврата вместо переоткрытия изображения
        exif_bytes = piexif.dump(exif_dict)
        img._exif = exif_bytes  # Сохраняем EXIF данные для использования при сохранении

    elif img_format.upper() == 'PNG':
        # Создаем PNG метаданные для использования при сохранении
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
        img._png_info = metadata  # Сохраняем PNG информацию для использования при сохранении

    return img, img_format

async def process_image(bot: Bot, file_id: str, user_id: int, copies: int, archives_count: int) -> BufferedInputFile:
    """Создает общий архив с несколькими архивами, каждый содержит уникальные копии изображения"""
    file = await bot.get_file(file_id)
    file_content = await bot.download_file(file.file_path)
    source_bytes = file_content.getvalue()
    file_name = file.file_path.split('/')[-1]
    name_parts = file_name.rsplit('.', 1)
    ext = name_parts[1] if len(name_parts) > 1 else 'jpg'

    bundle_buffer = BytesIO()
    used_hashes = set()

    with zipfile.ZipFile(bundle_buffer, "w", zipfile.ZIP_DEFLATED) as bundle_zip:
        for archive_index in range(1, archives_count + 1):
            archive_buffer = BytesIO()
            with zipfile.ZipFile(archive_buffer, "w", zipfile.ZIP_DEFLATED) as archive_zip:
                for _ in range(copies):
                    unique_file_name = generate_random_filename(ext=ext)

                    image_bytes = None
                    for _attempt in range(10):
                        candidate = build_processed_image_bytes(source_bytes)
                        candidate_hash = hashlib.sha256(candidate).hexdigest()
                        if candidate_hash not in used_hashes:
                            used_hashes.add(candidate_hash)
                            image_bytes = candidate
                            break

                    if image_bytes is None:
                        raise ValueError("Не удалось сгенерировать достаточное количество уникальных изображений")

                    archive_zip.writestr(unique_file_name, image_bytes)

            archive_buffer.seek(0)
            bundle_zip.writestr(f"archive_{archive_index}.zip", archive_buffer.read())

    bundle_buffer.seek(0)
    zip_filename = f"images_archives_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return BufferedInputFile(bundle_buffer.read(), filename=zip_filename)

async def process_archive(bot: Bot, file_id: str, user_id: int) -> BufferedInputFile:
    """Обрабатывает архив с изображениями - уникализирует каждое изображение (по 1 копии)"""
    file = await bot.get_file(file_id)
    file_content = await bot.download_file(file.file_path)

    # Открываем входящий архив
    input_zip = zipfile.ZipFile(BytesIO(file_content.getvalue()), 'r')

    # Создаем выходной архив
    output_zip_buffer = BytesIO()
    output_zip = zipfile.ZipFile(output_zip_buffer, 'w', zipfile.ZIP_DEFLATED)

    # Поддерживаемые форматы изображений
    image_extensions = ('.jpg', '.jpeg', '.png', '.webp', '.tiff', '.bmp', '.gif')

    try:
        for file_info in input_zip.filelist:
            file_name = file_info.filename

            # Проверяем, является ли файл изображением
            if file_name.lower().endswith(image_extensions):
                try:
                    # Читаем изображение из архива
                    image_data = input_zip.read(file_name)
                    image_buffer = BytesIO(image_data)

                    # Уникализируем изображение
                    processed_image_bytes = build_processed_image_bytes(image_buffer.read())

                    # Генерируем новое имя файла
                    name_parts = file_name.rsplit('.', 1)
                    ext = name_parts[1] if len(name_parts) > 1 else 'jpg'
                    unique_file_name = generate_random_filename(ext=ext)

                    output_zip.writestr(unique_file_name, processed_image_bytes)

                except Exception as e:
                    # Если не удалось обработать изображение, пропускаем его
                    bugsnag.notify(e)
                    continue
    finally:
        input_zip.close()
        output_zip.close()

    output_zip_buffer.seek(0)
    zip_filename = f"unicalized_archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return BufferedInputFile(output_zip_buffer.read(), filename=zip_filename)


async def process_archive_multiple(bot: Bot, file_id: str, user_id: int, archives_count: int) -> BufferedInputFile:
    """Создает общий архив с несколькими уникализированными ZIP-архивами"""
    if archives_count == 1:
        return await process_archive(bot, file_id, user_id)

    bundle_buffer = BytesIO()
    with zipfile.ZipFile(bundle_buffer, "w", zipfile.ZIP_DEFLATED) as bundle_zip:
        for archive_index in range(1, archives_count + 1):
            archive_file = await process_archive(bot, file_id, user_id)
            bundle_zip.writestr(f"archive_{archive_index}.zip", archive_file.data)

    bundle_buffer.seek(0)
    zip_filename = f"unicalized_archives_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return BufferedInputFile(bundle_buffer.read(), filename=zip_filename)

@router.message(F.text == "🖼️ Уникализатор")
async def images_unicalization_initiation(message: Message, state: FSMContext):
    """Начинает процесс уникализации изображений"""
    if not is_user_allowed(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    m1 = await message.answer("Загрузите изображение для уникализации или ZIP архив с несколькими изображениями:")
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.images_unicalization)

@router.message(Form.images_unicalization)
async def receive_image(message: Message, state: FSMContext):
    """Обрабатывает полученное изображение или архив"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    # Проверка на архив
    if message.document and message.document.mime_type in ('application/zip', 'application/x-zip-compressed'):
        file_id = message.document.file_id
        await state.update_data(unicalization_file_id=file_id, is_archive=True)

        await message.answer("Введите количество архивов (например, 3):", reply_markup=cancel_kb)
        await state.set_state(Form.unicalization_archives)
        return

    # Проверка на изображение
    if message.photo or (message.document and message.document.mime_type.startswith('image/')):
        file_id = (
            message.photo[-1].file_id if message.photo
            else message.document.file_id
        )
    else:
        await message.answer("❌ Пожалуйста, отправьте изображение (фото или документ) или ZIP архив с изображениями.", reply_markup=cancel_kb)
        return

    await state.update_data(unicalization_file_id=file_id, is_archive=False)

    await message.answer("Введите количество картинок в каждом архиве (например, 5):", reply_markup=cancel_kb)
    await state.set_state(Form.unicalization_copies)

@router.message(Form.unicalization_copies)
async def receive_copy_count(message: Message, state: FSMContext):
    """Обрабатывает количество картинок в архиве и переводит к шагу выбора количества архивов"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("❌ Введите корректное положительное число картинок.", reply_markup=cancel_kb)
        return

    images_per_archive = int(message.text)

    await state.update_data(unicalization_copies=images_per_archive)
    await message.answer(
        "Введите количество архивов (например, 3):",
        reply_markup=cancel_kb
    )
    await state.set_state(Form.unicalization_archives)


@router.message(Form.unicalization_archives)
async def receive_archive_count(message: Message, state: FSMContext):
    """Обрабатывает количество архивов и запускает уникализацию"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    if not message.text.isdigit() or int(message.text) <= 0:
        await message.answer("❌ Введите корректное положительное число архивов.", reply_markup=cancel_kb)
        return

    archives_count = int(message.text)

    data = await state.get_data()
    is_archive = data.get("is_archive", False)
    unicalization_file_id = data.get("unicalization_file_id")
    images_per_archive = data.get("unicalization_copies")

    if not unicalization_file_id:
        await state.clear()
        await message.answer(
            "❌ Данные сессии потеряны. Запустите уникализатор заново.",
            reply_markup=get_menu_keyboard(message.from_user.id)
        )
        return

    if is_archive:
        await message.answer("🔄 Обрабатываю архив и собираю результат...", reply_markup=cancel_kb)
        try:
            images_zip = await process_archive_multiple(
                message.bot,
                unicalization_file_id,
                message.chat.id,
                archives_count
            )
            await message.bot.send_document(message.chat.id, document=images_zip)
            await message.answer(
                f"✅ Готово! Создано архивов: {archives_count}.",
                reply_markup=get_menu_keyboard(message.chat.id)
            )
        except Exception as e:
            bugsnag.notify(e)
            await message.answer("❌ Произошла ошибка при обработке архива.")
    else:
        if not images_per_archive:
            await state.clear()
            await message.answer(
                "❌ Данные сессии потеряны. Запустите уникализатор заново.",
                reply_markup=get_menu_keyboard(message.from_user.id)
            )
            return

        await message.answer("🔄 Обрабатываю изображение и собираю архивы...", reply_markup=cancel_kb)
        try:
            images_zip = await process_image(
                message.bot,
                unicalization_file_id,
                message.chat.id,
                images_per_archive,
                archives_count
            )
            await message.bot.send_document(message.chat.id, document=images_zip)
            await message.answer(
                f"✅ Готово! Создано архивов: {archives_count}, "
                f"картинок в каждом: {images_per_archive}.",
                reply_markup=get_menu_keyboard(message.chat.id)
            )

        except Exception as e:
            bugsnag.notify(e)
            await message.answer("❌ Произошла ошибка при обработке изображения.")

    await state.clear()
