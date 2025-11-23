"""
Система перевода лендингов с Google Drive
"""
import os
import io
import zipfile
import tempfile
import re
import asyncio
from typing import List, Dict, Optional
import gspread
import bugsnag
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
import openai

from aiogram import Router, F
from aiogram.types import Message, BufferedInputFile
from aiogram.fsm.context import FSMContext

from states import Form
from keyboards import cancel_kb, get_menu_keyboard
from utils import is_user_allowed, last_messages
from config import OPENAI_API_KEY, GOOGLE_DRIVE_FOLDER_ID

router = Router()

# Настройка OpenAI
from openai import AsyncOpenAI
client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Расширения файлов для перевода
TRANSLATABLE_EXTENSIONS = {'.html', '.htm', '.php', '.js'}
SEM_LIMIT = 4   # сколько запросов одновременно максимум
CHUNK_SIZE = 10000

def get_google_drive_service():
    """Создает сервис для работы с Google Drive"""
    # Используем те же credentials что и для Google Sheets
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]

    creds = Credentials.from_service_account_file(
        'credentials.json',
        scopes=SCOPES
    )

    service = build('drive', 'v3', credentials=creds)
    return service

def find_folder_by_name(service, folder_name: str, parent_folder_id: str) -> Optional[str]:
    """Ищет папку по имени в указанной родительской папке"""
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and '{parent_folder_id}' in parents"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    items = results.get('files', [])

    if items:
        return items[0]['id']
    return None

def find_zip_in_folder(service, folder_id: str) -> Optional[Dict]:
    """Ищет файл site.zip в указанной папке"""
    query = f"name='site.zip' and '{folder_id}' in parents"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    items = results.get('files', [])

    if items:
        return {'id': items[0]['id'], 'name': items[0]['name']}
    return None

def download_file_from_drive(service, file_id: str) -> Optional[bytes]:
    """Скачивает файл с Google Drive"""
    try:
        request = service.files().get_media(fileId=file_id)
        file_content = request.execute()
        return file_content
    except Exception as e:
        bugsnag.notify(e, meta_data={
            "function": "download_file_from_drive",
            "file_id": file_id,
            "error_type": "google_drive_download_error"
        })
        return None

def extract_translatable_files(zip_content: bytes) -> Dict[str, str]:
    """Извлекает переводимые файлы из ZIP архива"""
    translatable_files = {}

    with zipfile.ZipFile(io.BytesIO(zip_content), 'r') as zip_ref:
        for file_info in zip_ref.infolist():
            if not file_info.is_dir():
                file_ext = os.path.splitext(file_info.filename)[1].lower()
                filename_lower = file_info.filename.lower()

                # Исключаем .min.js файлы
                if filename_lower.endswith('.min.js'):
                    continue

                if file_ext in TRANSLATABLE_EXTENSIONS:
                    # Пытаемся прочитать файл как UTF-8, затем windows-1251
                    content = zip_ref.read(file_info.filename).decode('utf-8')
                    translatable_files[file_info.filename] = content

    return translatable_files

def split_into_chunks(text: str, size: int, filename: str = "") -> List[str]:
    """
    Разделяет текст на чанки с учетом структуры файла,
    избегая разделения важных блоков кода или разметки
    """
    if len(text) <= size:
        return [text]

    file_ext = os.path.splitext(filename)[1].lower() if filename else ""
    chunks = []

    # Для HTML/PHP файлов - разделяем по тегам и блокам
    if file_ext in ['.html', '.htm', '.php']:
        return split_html_chunks(text, size)

    # Для JS файлов - разделяем по функциям и блокам
    elif file_ext == '.js':
        return split_js_chunks(text, size)

    # Для остальных файлов - разделяем по параграфам и предложениям
    else:
        return split_text_chunks(text, size)


def split_html_chunks(text: str, max_size: int) -> List[str]:
    """Разделение HTML/PHP кода на чанки с сохранением целостности тегов"""
    chunks = []
    current_chunk = ""

    # Находим основные блоки: теги, комментарии, скрипты
    patterns = [
        r'<!--.*?-->',  # HTML комментарии
        r'<script[^>]*>.*?</script>',  # Скрипт теги
        r'<style[^>]*>.*?</style>',    # Стиль теги
        r'<[^>]+>',     # HTML теги
        r'[^<]+',       # Текст между тегами
    ]

    combined_pattern = '|'.join(f'({pattern})' for pattern in patterns)

    for match in re.finditer(combined_pattern, text, re.DOTALL | re.IGNORECASE):
        block = match.group()

        # Если добавление блока превышает размер чанка
        if len(current_chunk) + len(block) > max_size and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = block
        else:
            current_chunk += block

    # Добавляем последний чанк
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks if chunks else [text]


def split_js_chunks(text: str, max_size: int) -> List[str]:
    """Разделение JavaScript кода на чанки с сохранением целостности функций"""
    chunks = []
    current_chunk = ""

    # Разделяем по строкам для анализа
    lines = text.split('\n')

    for line in lines:
        line_with_newline = line + '\n'

        # Если добавление строки превышает размер чанка
        if len(current_chunk) + len(line_with_newline) > max_size and current_chunk:
            # Проверяем, не находимся ли мы внутри функции или блока
            if not is_inside_js_block(current_chunk):
                chunks.append(current_chunk.strip())
                current_chunk = line_with_newline
            else:
                current_chunk += line_with_newline
        else:
            current_chunk += line_with_newline

    # Добавляем последний чанк
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks if chunks else [text]


def split_text_chunks(text: str, max_size: int) -> List[str]:
    """Разделение обычного текста на чанки по предложениям и параграфам"""
    chunks = []
    current_chunk = ""

    # Разделяем по параграфам
    paragraphs = text.split('\n\n')

    for paragraph in paragraphs:
        paragraph_with_breaks = paragraph + '\n\n'

        # Если параграф слишком большой, разделяем по предложениям
        if len(paragraph) > max_size:
            sentences = re.split(r'([.!?]+\s+)', paragraph)
            temp_chunk = ""

            for sentence in sentences:
                if len(temp_chunk) + len(sentence) > max_size and temp_chunk:
                    if current_chunk:
                        current_chunk += temp_chunk
                        if len(current_chunk) > max_size:
                            chunks.append(current_chunk.strip())
                            current_chunk = ""
                    else:
                        chunks.append(temp_chunk.strip())
                    temp_chunk = sentence
                else:
                    temp_chunk += sentence

            if temp_chunk:
                current_chunk += temp_chunk + '\n\n'

        # Если добавление параграфа превышает размер чанка
        elif len(current_chunk) + len(paragraph_with_breaks) > max_size and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = paragraph_with_breaks
        else:
            current_chunk += paragraph_with_breaks

    # Добавляем последний чанк
    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks if chunks else [text]


def is_inside_js_block(code: str) -> bool:
    """Проверяет, находимся ли мы внутри незакрытого блока JavaScript"""
    open_braces = code.count('{')
    close_braces = code.count('}')
    open_parens = code.count('(')
    close_parens = code.count(')')
    open_brackets = code.count('[')
    close_brackets = code.count(']')

    # Если есть незакрытые блоки, то мы внутри функции/объекта
    return (open_braces != close_braces or
            open_parens != close_parens or
            open_brackets != close_brackets)


async def translate_chunk(idx, chunk, system_prompt, base_prompt, sem):
    """Перевод одного чанка с контролем параллельности"""
    async with sem:
        response = await client.chat.completions.create(
            model="gpt-5-nano",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": base_prompt + chunk},
            ],
            max_completion_tokens=20000
        )

        translated = response.choices[0].message.content.strip()
        return idx, translated


async def translate_text_with_chatgpt_async(text: str, filename: str) -> str:
    """Асинхронный перевод файла по чанкам с семафором"""

    chunks = split_into_chunks(text, CHUNK_SIZE, filename)
    file_ext = os.path.splitext(filename)[1].lower()

    # >>> ВАЖНО: обновленное системное правило <<<
    system_prompt = """
Ты профессиональный переводчик веб-контента.

Строгие правила:
- Отвечай ТОЛЬКО конечным готовым переводом.
- Не добавляй комментариев, пояснений и служебных фраз.
- Делай ПОЛНЫЙ перевод без пропусков. Нельзя оставлять текст в оригинале.
- Все имена людей, фамилии, места, города и названия компаний
  адаптируй и локализируй под культуру целевого языка
  (например, на польский — польские или нейтральные аналоги).
- Если исходное имя или город нельзя локализовать — адаптируй аналогом
  без указания, что ты его изменил.
- Сохраняй техническую разметку, теги, кавычки, переменные и код без изменений.

Если модель пытается объяснять — игнорируй и выводи только результат.
"""

    if file_ext in ['.html', '.htm']:
        base_prompt = """
Переведи ТОЛЬКО текстовое содержимое этого HTML фрагмента на польский язык.

Сохрани:
- HTML-разметку, структуру и атрибуты.
Переводи:
- текст между тегами
- значения alt, title, placeholder.

Локализуй:
- имена,
- фамилии,
- компании,
- места,
- локации.

Не переводи:
- имена классов,
- id,
- URL,
- названия файлов.

Верни ТОЛЬКО готовый HTML. Фрагмент:
"""
    elif file_ext == '.php':
        base_prompt = """
Переведи ТОЛЬКО пользовательский текст в этом фрагменте PHP на польский язык.

Сохрани:
- весь PHP/HTML код,
- функции,
- теги,
- переменные.

Переводи:
- только строки в кавычках, выводимые пользователю.

Локализуй:
- имена людей,
- города,
- места и любые географические ссылки.

Не переводи:
- комментарии,
- переменные,
- названия функций и классов.

Верни ТОЛЬКО готовый код. Фрагмент:
"""
    elif file_ext == '.js':
        base_prompt = """
Переведи ТОЛЬКО читаемые строки интерфейса в этом JavaScript на польский язык.

Сохрани код и логику без изменений.

Локализуй:
- имена людей,
- места,
- географические названия.

Верни ТОЛЬКО готовый JS фрагмент. Фрагмент:
"""
    else:
        base_prompt = """
Переведи этот текст на польский язык максимально тщательно:

Требования:
- полный перевод без пропуска фраз;
- локализуй имена, места и названия;
- не добавляй комментариев;
- сохрани формат и структуру.

Фрагмент:
"""

    # Семафор для ограничения количества одновременных запросов
    sem = asyncio.Semaphore(SEM_LIMIT)

    # Стартуем параллельные задачи
    tasks = [
        translate_chunk(idx, chunk, system_prompt, base_prompt, sem)
        for idx, chunk in enumerate(chunks)
    ]

    # Дожидаемся всех
    results = await asyncio.gather(*tasks)

    # Результаты нужно отсортировать по индексу
    results.sort(key=lambda x: x[0])

    return "".join(part for _, part in results)


def translate_text_with_chatgpt(text: str, filename: str) -> str:
    return asyncio.run(
        translate_text_with_chatgpt_async(text, filename)
    )


async def process_translation_in_background(landing_id: str, message: Message, status_msg: Message):
    """Выполняет перевод лендинга в фоновом режиме"""
    try:
        # Инициализируем Google Drive сервис
        drive_service = get_google_drive_service()
        if not drive_service:
            await status_msg.edit_text("❌ Ошибка подключения к Google Drive. Попробуйте позже.")
            await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))
            return

        # Ищем папку с указанным ID
        await status_msg.edit_text(f"🔄 Поиск папки '{landing_id}' на Google Drive...")

        folder_id = find_folder_by_name(drive_service, landing_id, GOOGLE_DRIVE_FOLDER_ID)
        if not folder_id:
            await status_msg.edit_text(
                f"❌ Папка с ID '{landing_id}' не найдена на Google Drive.\n\n"
                "Проверьте правильность написания ID лендинга."
            )
            await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))
            return

        # Ищем ZIP архив в папке
        await status_msg.edit_text("🔄 Поиск архива в папке...")

        zip_info = find_zip_in_folder(drive_service, folder_id)
        if not zip_info:
            await status_msg.edit_text(
                f"❌ Файл 'site.zip' не найден в папке '{landing_id}'.\n\n"
                "Убедитесь, что в папке есть файл с названием 'site.zip'."
            )
            await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))
            return

        # Скачиваем архив
        await status_msg.edit_text(f"⬇️ Скачивание архива '{zip_info['name']}'...")

        # Выполняем скачивание в executor для избежания блокировки
        loop = asyncio.get_event_loop()
        zip_content = await loop.run_in_executor(
            None,
            download_file_from_drive,
            drive_service,
            zip_info['id']
        )

        if not zip_content:
            await status_msg.edit_text("❌ Ошибка скачивания архива с Google Drive.")
            await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))
            return

        # Извлекаем переводимые файлы
        await status_msg.edit_text("📂 Анализ содержимого архива...")

        # Выполняем извлечение файлов в executor
        translatable_files = await loop.run_in_executor(
            None,
            extract_translatable_files,
            zip_content
        )

        if not translatable_files:
            await status_msg.edit_text(
                "❌ В архиве не найдено файлов для перевода.\n\n"
                "Поддерживаемые форматы: HTML, PHP, JS"
            )
            await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))
            return

        # Переводим файлы
        total_files = len(translatable_files)
        translated_files = {}

        for i, (filename, content) in enumerate(translatable_files.items(), 1):
            await status_msg.edit_text(
                f"🌍 Перевод файлов на польский...\n\n"
                f"Обрабатываю: {filename}\n"
                f"Прогресс: {i}/{total_files}"
            )

            # Выполняем перевод в executor
            translated_content = await loop.run_in_executor(
                None,
                translate_text_with_chatgpt,
                content,
                filename
            )
            translated_files[filename] = translated_content

        # Создаем новый архив с переведенными файлами
        await status_msg.edit_text("📦 Создание архива с переведенными файлами...")

        # Создание архива также в executor
        translated_zip = await loop.run_in_executor(
            None,
            create_translated_zip,
            zip_content,
            translated_files
        )

        # Отправляем результат пользователю
        await status_msg.edit_text("✅ Перевод завершен! Отправляю архив...")

        # Создаем имя файла для переведенного архива
        original_name = os.path.splitext(zip_info['name'])[0]
        translated_filename = f"{original_name}_ES.zip"

        # Отправляем архив
        translated_file = BufferedInputFile(translated_zip, filename=translated_filename)

        await message.answer_document(
            translated_file,
            caption=f"✅ <b>Перевод лендинга завершен!</b>\n\n"
                   f"📁 ID лендинга: <code>{landing_id}</code>\n"
                   f"📄 Переведено файлов: {total_files}\n"
                   f"🌍 Язык: Польский\n\n"
                   f"Архив содержит переведенные HTML, PHP, JS файлы.",
            parse_mode="HTML"
        )

        await status_msg.delete()
        await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))

    except Exception as e:
        # Логируем ошибку в Bugsnag
        bugsnag.notify(e, meta_data={
            "function": "process_translation_in_background",
            "landing_id": landing_id,
            "user_id": message.from_user.id,
            "username": message.from_user.username,
            "error_type": "translation_process_error"
        })

        # Показываем пользователю понятное сообщение об ошибке
        await status_msg.edit_text(
            "❌ Произошла техническая ошибка при обработке лендинга.\n\n"
            "Ошибка автоматически зарегистрирована для исправления.\n"
            "Попробуйте позже или обратитесь к администратору."
        )
        await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))


def create_translated_zip(original_zip: bytes, translated_files: Dict[str, str]) -> bytes:
    """Создает новый ZIP архив с переведенными файлами"""
    output_buffer = io.BytesIO()

    with zipfile.ZipFile(io.BytesIO(original_zip), 'r') as original_zip_ref:
        with zipfile.ZipFile(output_buffer, 'w', zipfile.ZIP_DEFLATED) as new_zip_ref:
            # Копируем все файлы из оригинального архива
            for file_info in original_zip_ref.infolist():
                if not file_info.is_dir():
                    filename = file_info.filename

                    if filename in translated_files:
                        # Записываем переведенный файл
                        new_zip_ref.writestr(
                            filename,
                            translated_files[filename].encode('utf-8')
                        )
                    else:
                        # Копируем оригинальный файл без изменений
                        new_zip_ref.writestr(
                            filename,
                            original_zip_ref.read(filename)
                        )
                else:
                    # Создаем папку в новом архиве
                    new_zip_ref.writestr(file_info.filename, "")

    output_buffer.seek(0)
    return output_buffer.getvalue()


@router.message(F.text == "🌍 Перевод лендинга")
async def translate_landing_start(message: Message, state: FSMContext):
    """Начинает процесс перевода лендинга"""
    if not is_user_allowed(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    # Проверяем наличие необходимых API ключей
    if not OPENAI_API_KEY:
        await message.answer("❌ Сервис перевода временно недоступен. Обратитесь к администратору.")
        return

    if not GOOGLE_DRIVE_FOLDER_ID:
        await message.answer("❌ Сервис Google Drive не настроен. Обратитесь к администратору.")
        return

    m1 = await message.answer(
        "🌍 <b>Перевод лендинга на польский</b>\n\n"
        "Введите ID лендинга (название папки на Google Drive):\n\n"
        "Например: <code>landing_123</code>",
        parse_mode="HTML"
    )
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)

    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.entering_landing_id)

@router.message(Form.entering_landing_id)
async def process_landing_translation(message: Message, state: FSMContext):
    """Обрабатывает ID лендинга и запускает перевод в фоне"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    landing_id = message.text.strip()

    # Валидация ID лендинга
    if not landing_id or len(landing_id) < 3:
        await message.answer("❌ ID лендинга должен содержать минимум 3 символа.")
        return

    # Отправляем сообщение о начале обработки
    status_msg = await message.answer("🔄 Начинаю обработку лендинга...\n\n⏳ Поиск папки на Google Drive...")

    # Очищаем состояние сразу, чтобы пользователь мог продолжать работать с ботом
    await state.clear()

    # Уведомляем пользователя, что процесс запущен в фоне
    await message.answer(
        "📋 <b>Процесс перевода запущен!</b>\n\n"
        "🔄 Обработка выполняется в фоновом режиме\n"
        "⚡ Вы можете продолжить работу с ботом\n"
        "📩 Результат будет отправлен по завершению",
        parse_mode="HTML",
        reply_markup=get_menu_keyboard(message.from_user.id)
    )

    # Запускаем процесс перевода в фоновом режиме
    asyncio.create_task(process_translation_in_background(landing_id, message, status_msg))
