"""
Система перевода лендингов с Google Drive
"""
import os
import io
import zipfile
import tempfile
import re
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
from openai import OpenAI
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# Расширения файлов для перевода
TRANSLATABLE_EXTENSIONS = {'.html', '.htm', '.php', '.js'}

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

def translate_text_with_chatgpt(text: str, filename: str) -> str:
    """Переводит текст с помощью ChatGPT API с разбиением на чанки"""

    if not client:
        return text

    CHUNK_SIZE = 10000

    def split_into_chunks(s, size):
        return [s[i:i + size] for i in range(0, len(s), size)]

    chunks = split_into_chunks(text, CHUNK_SIZE)
    translated_chunks = []

    file_ext = os.path.splitext(filename)[1].lower()

    # Системное правило – строго запрещаем комментарии и болтовню
    system_prompt = """
Ты профессиональный переводчик веб-контента.

Строгие правила:
- Отвечай ТОЛЬКО итоговым переведённым фрагментом.
- Не добавляй комментариев, пояснений, примечаний и фраз типа:
  «Вот перевод», «Первая часть», «Если нужно — продолжу», «Спасибо», «Готово», и т.п.
- Никаких описаний процесса.
- Только чистый переведённый результат.

Если модель пытается объяснять – просто игнорируй и выводи только результат.
"""

    # Базовые подсказки для типов файлов
    if file_ext in ['.html', '.htm']:
        base_prompt = """
Переведи ТОЛЬКО текстовое содержимое этого HTML фрагмента на польский язык.
Сохрани:
- HTML-разметку,
- структуру,
- теги,
- атрибуты,
- порядок текста.

Переводи:
- текст между тегами,
- alt/title/placeholder.

НЕ переводи:
- class, id, URL,
- названия файлов,
- технические атрибуты,
- скрипты.

Верни ТОЛЬКО готовый HTML фрагмент. Фрагмент:
"""
    elif file_ext == '.php':
        base_prompt = """
Переведи ТОЛЬКО пользовательский текст в этом фрагменте PHP на польский.

Сохрани:
- PHP/HTML код,
- теги,
- переменные,
- функции,
- синтаксис.

Переводи:
- строки в кавычках, выводимые пользователю.

НЕ переводи:
- названия переменных,
- функции,
- классы,
- комментарии.

Верни ТОЛЬКО готовый результат. Фрагмент:
"""
    elif file_ext == '.js':
        base_prompt = """
Переведи ТОЛЬКО пользовательские читаемые строки в этом JavaScript фрагменте на польский язык.

Не изменяй:
- логику,
- переменные,
- код.

Верни ТОЛЬКО готовый результат без лишних пояснений. Фрагмент:
"""
    else:
        base_prompt = """
Переведи этот текст на польский язык, сохранив форматирование и структуру.
Верни ТОЛЬКО готовый текст без пояснений. Фрагмент:
"""

    # Перевод чанков по одному
    for chunk in chunks:
        prompt = base_prompt + chunk

        response = client.chat.completions.create(
            model="gpt-5-nano",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            max_completion_tokens=20000
        )

        translation = response.choices[0].message.content.strip()

        translated_chunks.append(translation)

    return "".join(translated_chunks)


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
    """Обрабатывает ID лендинга и выполняет перевод"""
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

    try:
        # Инициализируем Google Drive сервис
        drive_service = get_google_drive_service()
        if not drive_service:
            await status_msg.edit_text("❌ Ошибка подключения к Google Drive. Попробуйте позже.")
            await state.clear()
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
            await state.clear()
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
            await state.clear()
            return

        # Скачиваем архив
        await status_msg.edit_text(f"⬇️ Скачивание архива '{zip_info['name']}'...")

        zip_content = download_file_from_drive(drive_service, zip_info['id'])
        if not zip_content:
            await status_msg.edit_text("❌ Ошибка скачивания архива с Google Drive.")
            await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))
            await state.clear()
            return

        # Извлекаем переводимые файлы
        await status_msg.edit_text("📂 Анализ содержимого архива...")

        translatable_files = extract_translatable_files(zip_content)
        if not translatable_files:
            await status_msg.edit_text(
                "❌ В архиве не найдено файлов для перевода.\n\n"
                "Поддерживаемые форматы: HTML, PHP, JS"
            )
            await message.answer("Выберите действие:", reply_markup=get_menu_keyboard(message.from_user.id))
            await state.clear()
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

            translated_content = translate_text_with_chatgpt(content, filename)
            translated_files[filename] = translated_content

        # Создаем новый архив с переведенными файлами
        await status_msg.edit_text("📦 Создание архива с переведенными файлами...")

        translated_zip = create_translated_zip(zip_content, translated_files)

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
            "function": "process_landing_translation",
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


    await state.clear()
