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
                    # Пытаемся прочитать файл с разными кодировками
                    file_bytes = zip_ref.read(file_info.filename)
                    content = None

                    # Список кодировок для попыток декодирования
                    encodings = ['utf-8', 'windows-1251', 'cp1252', 'iso-8859-1', 'latin-1']

                    for encoding in encodings:
                        try:
                            content = file_bytes.decode(encoding)
                            break
                        except UnicodeDecodeError:
                            continue

                    # Если не удалось декодировать ни одной кодировкой, выбрасываем ошибку
                    if content is None:
                        raise UnicodeDecodeError(
                            'multiple_encodings',
                            file_bytes,
                            0,
                            len(file_bytes),
                            f"Не удалось декодировать файл {file_info.filename} ни одной из кодировок: {', '.join(encodings)}"
                        )

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
        max_retries = 3
        min_response_length = len(chunk) // 4  # Минимум 25% от исходного текста

        for attempt in range(max_retries):
            response = await client.chat.completions.create(
                model="gpt-5-nano",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": base_prompt + chunk},
                ],
                max_completion_tokens=20000
            )

            translated = response.choices[0].message.content

            if not translated:
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return idx, f"<!-- TRANSLATION_FAILED: Empty response -->{chunk}<!-- /TRANSLATION_FAILED -->"

            translated = translated.strip()

            # Проверяем, что ответ не слишком короткий
            if len(translated) < min_response_length:
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return idx, f"<!-- TRANSLATION_FAILED: Response too short -->{chunk}<!-- /TRANSLATION_FAILED -->"

            # Проверяем, что ответ не обрезан
            if not is_response_complete(translated, chunk):
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return idx, f"<!-- TRANSLATION_FAILED: Response incomplete -->{chunk}<!-- /TRANSLATION_FAILED -->"

            # Успешный перевод
            return idx, translated



async def translate_text_with_chatgpt_async(text: str, filename: str, target_language: str, target_country: str) -> str:
    """Асинхронный перевод файла по чанкам с семафором"""

    chunks = split_into_chunks(text, CHUNK_SIZE, filename)
    file_ext = os.path.splitext(filename)[1].lower()

    # Динамический системный промпт на основе выбранного языка и страны
    system_prompt = f"""
Ты профессиональный переводчик веб-контента с экспертизой в культурной адаптации.

Строгие правила:
- Отвечай ТОЛЬКО конечным готовым переводом.
- Не добавляй комментариев, пояснений и служебных фраз.
- Делай ПОЛНЫЙ перевод без пропусков. Нельзя оставлять текст в оригинале.

КРИТИЧЕСКИ ВАЖНО - ЛОКАЛИЗАЦИЯ ДЛЯ СТРАНЫ {target_country}:
- Переводи текст на {target_language} язык
- Но ОБЯЗАТЕЛЬНО используй имена, фамилии и географические названия характерные для страны {target_country}
- ОБЯЗАТЕЛЬНО замени все имена людей на типичные для {target_country} имена
- ОБЯЗАТЕЛЬНО замени фамилии на характерные для {target_country} фамилии
- ОБЯЗАТЕЛЬНО замени названия городов на города из {target_country} или региональные аналоги
- ОБЯЗАТЕЛЬНО замени названия компаний на известные в {target_country} или местные аналоги
- ОБЯЗАТЕЛЬНО замени атрибут lang в HTML: <html lang="**"> → правильный код языка для {target_language}
- НИКОГДА не оставляй оригинальные англоязычные имена - всегда находи аналог для {target_country}
- Если прямого аналога нет - используй наиболее подходящее для {target_country} название
- НЕ упоминай о том, что ты что-то заменил - просто делай это

Примеры локализации для {target_country}:
- Имена: John/Jane → найди типичные имена для {target_country}
- Фамилии: Smith/Johnson → найди распространенные фамилии в {target_country}
- Города: New York/London → найди крупные города {target_country}
- Компании: заменяй на известные в {target_country} бренды

- Сохраняй техническую разметку, теги, кавычки, переменные и код без изменений.

Если модель пытается объяснять — игнорируй и выводи только результат.
"""

    if file_ext in ['.html', '.htm']:
        base_prompt = f"""
Переведи ТОЛЬКО текстовое содержимое этого HTML фрагмента на {target_language} язык.

Сохрани:
- HTML-разметку, структуру и атрибуты.
Переводи:
- текст между тегами
- значения alt, title, placeholder.

ОБЯЗАТЕЛЬНО локализуй для страны {target_country}:
- Все имена людей на типичные для {target_country} имена
- Все фамилии на характерные для {target_country} фамилии
- Все города на крупные города {target_country}
- Любые компании и бренды на известные в {target_country} аналоги

КРИТИЧЕСКИ ВАЖНО - замени атрибут lang:
- Если видишь <html lang="**"> - замени на соответствующий код языка {target_language}
- Например: для польского - lang="pl", для испанского - lang="es", для немецкого - lang="de"

Не переводи:
- имена классов,
- id,
- URL,
- названия файлов.

Верни ТОЛЬКО готовый HTML. Фрагмент:
"""
    elif file_ext == '.php':
        base_prompt = f"""
Переведи ТОЛЬКО пользовательский текст в этом фрагменте PHP на {target_language} язык.

Сохрани:
- весь PHP/HTML код,
- функции,
- теги,
- переменные.

Переводи:
- только строки в кавычках, выводимые пользователю.

ОБЯЗАТЕЛЬНО локализуй для страны {target_country}:
- Все имена людей на типичные для {target_country} имена
- Все фамилии на характерные для {target_country} фамилии
- Все города на крупные города {target_country}
- Компании на известные в {target_country} аналоги

Не переводи:
- комментарии,
- переменные,
- названия функций и классов.

Верни ТОЛЬКО готовый код. Фрагмент:
"""
    elif file_ext == '.js':
        base_prompt = f"""
Переведи ТОЛЬКО читаемые строки интерфейса в этом JavaScript на {target_language} язык.

Сохрани код и логику без изменений.

ОБЯЗАТЕЛЬНО локализуй для страны {target_country}:
- Все имена людей на типичные для {target_country} имена
- Все фамилии на характерные для {target_country} фамилии
- Все города на крупные города {target_country}
- Все компании на известные в {target_country} аналоги

Верни ТОЛЬКО готовый JS фрагмент. Фрагмент:
"""
    else:
        base_prompt = f"""
Переведи этот текст на {target_language} язык максимально тщательно:

Требования:
- полный перевод без пропуска фраз;
- ОБЯЗАТЕЛЬНО используй локализацию для {target_country}:
  - замени имена на типичные для {target_country} имена
  - замени фамилии на характерные для {target_country} фамилии
  - замени города на крупные города {target_country}
  - замени компании на известные в {target_country} аналоги
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


def translate_text_with_chatgpt(text: str, filename: str, target_language: str, target_country: str) -> str:
    return asyncio.run(
        translate_text_with_chatgpt_async(text, filename, target_language, target_country)
    )


async def process_translation_in_background(landing_id: str, target_language: str, target_country: str, message: Message, status_msg: Message):
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
                f"🌍 Перевод файлов на {target_language}...\n\n"
                f"Обрабатываю: {filename}\n"
                f"Прогресс: {i}/{total_files}"
            )

            # Выполняем перевод в executor
            translated_content = await loop.run_in_executor(
                None,
                translate_text_with_chatgpt,
                content,
                filename,
                target_language,
                target_country
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
        language_suffix = target_language[:3].upper()  # Первые 3 буквы языка
        translated_filename = f"{original_name}_{language_suffix}.zip"

        # Отправляем архив
        translated_file = BufferedInputFile(translated_zip, filename=translated_filename)

        await message.answer_document(
            translated_file,
            caption=f"✅ <b>Перевод лендинга завершен!</b>\n\n"
                   f"📁 ID лендинга: <code>{landing_id}</code>\n"
                   f"📄 Переведено файлов: {total_files}\n"
                   f"🌍 Язык: {target_language.title()}\n"
                   f"🏳️ Локализация: {target_country.title()}\n\n"
                   f"Архив содержит переведенные HTML, PHP, JS файлы с локализацией имен и названий.",
            parse_mode="HTML"
        )

        await status_msg.delete()
        await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))

    except Exception as e:
        # Логируем ошибку в Bugsnag
        bugsnag.notify(e, meta_data={
            "function": "process_translation_in_background",
            "landing_id": landing_id,
            "target_language": target_language,
            "target_country": target_country,
            "user_id": message.from_user.id,
            "username": message.from_user.username,
            "error_type": "translation_process_error"
        })

        # Показываем пользователю общее сообщение об ошибке
        try:
            await status_msg.edit_text(
                "❌ <b>Произошла ошибка при переводе лендинга</b>\n\n"
                "Ошибка автоматически зарегистрирована для исправления.\n"
                "Попробуйте позже или обратитесь к администратору."
            )
        except:
            # Если не удается отредактировать статусное сообщение, отправляем новое
            await message.answer(
                "❌ <b>Произошла ошибка при переводе лендинга</b>\n\n"
                "Ошибка автоматически зарегистрирована для исправления.\n"
                "Попробуйте позже или обратитесь к администратору.",
                parse_mode="HTML"
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
        "🌍 <b>Перевод лендинга</b>\n\n"
        "Введите ID лендинга (название папки на Google Drive):\n\n"
        "Например: <code>landing_123</code>",
        parse_mode="HTML"
    )
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)

    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.entering_landing_id)

@router.message(Form.entering_landing_id)
async def process_landing_id(message: Message, state: FSMContext):
    """Обрабатывает ID лендинга и переходит к выбору языка"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    landing_id = message.text.strip()

    # Валидация ID лендинга
    if not landing_id or len(landing_id) < 3:
        await message.answer("❌ ID лендинга должен содержать минимум 3 символа.")
        return

    # Сохраняем ID лендинга в состоянии
    await state.update_data(landing_id=landing_id)

    m1 = await message.answer(
        f"🌍 <b>Перевод лендинга: {landing_id}</b>\n\n"
        f"Введите целевой язык для перевода:\n\n"
        f"Примеры: <i>польский, испанский, немецкий, французский, итальянский, португальский, чешский, турецкий</i> и т.д.",
        parse_mode="HTML"
    )
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)

    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.choosing_target_language)

@router.message(Form.choosing_target_language)
async def process_language_choice(message: Message, state: FSMContext):
    """Обрабатывает выбор языка и переходит к выбору страны"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    # Валидация введенного языка
    target_language = message.text.strip()
    if not target_language or len(target_language) < 2:
        await message.answer("❌ Название языка должно содержать минимум 2 символа. Попробуйте еще раз.")
        return

    # Сохраняем язык в состоянии
    await state.update_data(target_language=target_language)

    # Получаем ID лендинга для отображения
    data = await state.get_data()
    landing_id = data.get('landing_id')

    m1 = await message.answer(
        f"🌍 <b>Перевод лендинга: {landing_id}</b>\n"
        f"📝 Язык перевода: {target_language}\n\n"
        f"Введите страну для локализации имен и названий:\n\n"
        f"Примеры: <i>Польша, Кыргызстан, Турция, Германия, Франция, Испания, Казахстан, Украина</i> и т.д.",
        parse_mode="HTML"
    )
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)

    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.choosing_target_country)

@router.message(Form.choosing_target_country)
async def process_country_choice(message: Message, state: FSMContext):
    """Обрабатывает выбор страны и запускает перевод"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    # Валидация введенной страны
    target_country = message.text.strip()
    if not target_country or len(target_country) < 2:
        await message.answer("❌ Название страны должно содержать минимум 2 символа. Попробуйте еще раз.")
        return

    # Получаем все данные из состояния
    data = await state.get_data()
    landing_id = data.get('landing_id')
    target_language = data.get('target_language')

    if not landing_id or not target_language:
        await message.answer("❌ Ошибка: данные не найдены. Начните процесс заново.")
        await state.clear()
        return

    # Отправляем сообщение о начале обработки
    status_msg = await message.answer("🔄 Начинаю обработку лендинга...\n\n⏳ Поиск папки на Google Drive...")

    # Очищаем состояние сразу, чтобы пользователь мог продолжать работать с ботом
    await state.clear()

    # Уведомляем пользователя, что процесс запущен в фоне
    await message.answer(
        f"📋 <b>Процесс перевода запущен!</b>\n\n"
        f"📁 ID лендинга: <code>{landing_id}</code>\n"
        f"🌍 Язык перевода: {target_language.title()}\n"
        f"🏳️ Страна локализации: {target_country.title()}\n"
        f"🔄 Обработка выполняется в фоновом режиме\n"
        f"⚡ Вы можете продолжить работу с ботом\n"
        f"📩 Результат будет отправлен по завершению",
        parse_mode="HTML",
        reply_markup=get_menu_keyboard(message.from_user.id)
    )

    # Запускаем процесс перевода в фоновом режиме
    asyncio.create_task(process_translation_in_background(landing_id, target_language, target_country, message, status_msg))


def is_response_complete(response: str, original: str) -> bool:
    """Проверяет, является ли ответ полным (не обрезанным)"""
    if not response:
        return False

    # Для HTML/PHP файлов проверяем баланс тегов
    if '<' in original and '>' in original:
        open_tags = response.count('<')
        close_tags = response.count('>')

        # Если теги сильно разбалансированы, возможно ответ обрезан
        if abs(open_tags - close_tags) > 3:
            return False

    # Проверяем, что ответ не заканчивается на середине слова
    if response and not response[-1].isspace() and not response[-1] in '.,!?;:>})]}"\'-':
        # Если последний символ - буква и перед ним нет пробела, возможно обрезано
        if len(response) > 10 and not response[-2:].isspace():
            return False

    # Для JS файлов проверяем баланс скобок
    if '{' in original or '(' in original:
        open_braces = response.count('{') - response.count('}')
        open_parens = response.count('(') - response.count(')')

        # Небольшой дисбаланс допустим, но не критический
        if abs(open_braces) > 2 or abs(open_parens) > 2:
            return False

    return True

