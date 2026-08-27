"""
Обработчики для системы создания и починки лендингов
"""
import re
import gspread
import shortuuid
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from states import Form
from keyboards import (get_landing_category_keyboard, get_admin_processing_keyboard,
                         cancel_kb, get_menu_keyboard, ready_kb)
from utils import (last_messages, send_notification_with_buttons,
                     send_document_to_admins, send_photo_to_admins, delete_last_messages)
from config import GOOGLE_SHEET_ID

router = Router()

# Регулярное выражение для проверки ссылок Canvas
VALID_LINK_REGEX = re.compile(r"^https:\/\/chatgpt\.com\/canvas\/shared\/[a-zA-Z0-9]+$")

@router.message(F.text == "🌐 Создать/починить лендинг")
async def create_landing(message: Message, state: FSMContext):
    """Начинает процесс создания или починки лендинга"""

    kb = get_landing_category_keyboard()
    m1 = await message.answer("Выберите действие:", reply_markup=kb)
    m2 = await message.answer("❌ В любой момент нажмите 'Отмена', чтобы выйти", reply_markup=cancel_kb)
    last_messages[message.from_user.id] = [m1.message_id, m2.message_id]
    await state.set_state(Form.choosing_offer_category)

@router.callback_query(F.data.startswith("landing:"), Form.choosing_offer_category)
async def landing_category_selected(query: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор категории лендинга"""
    _, landing_category = query.data.split(":")
    await state.update_data(landing_category=landing_category)

    action_text = "создания" if landing_category == "create" else "починки"
    await query.message.edit_text(f"Напишите название оффера для {action_text} лендинга:")
    await state.set_state(Form.writing_offer_name)
    await query.answer()

@router.message(Form.writing_offer_name)
async def write_offer_name(message: Message, state: FSMContext):
    """Обрабатывает введенное название оффера"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    offer_name = message.text.strip()
    if not offer_name:
        await message.answer("Пожалуйста, введите название оффера.")
        return

    await state.update_data(offer_name=offer_name)

    msg = await message.answer(
        "Напишите ТЗ (техническое задание).\n"
        "Можете отправлять текст и изображения.\n"
        "Когда закончите, нажмите 'Готово':",
        reply_markup=ready_kb
    )
    last_messages[message.from_user.id] = [msg.message_id]
    await state.set_state(Form.writing_specification)

@router.message(Form.writing_specification)
async def write_specification(message: Message, state: FSMContext):
    """Обрабатывает техническое задание"""
    if message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    if message.text == "✅ Готово":
        data = await state.get_data()
        landing_category = data.get("landing_category")

        if landing_category == "create":
            msg = await message.answer("Введите ссылку на canvas из Chat GPT:", reply_markup=cancel_kb)
            last_messages[message.from_user.id] = [msg.message_id]
            await state.set_state(Form.entering_canvas_link)
        else:
            msg = await message.answer(
                "Загрузите ZIP архивы с файлами лендинга (можно несколько).\n"
                "Когда закончите, нажмите 'Готово':",
                reply_markup=ready_kb
            )
            last_messages[message.from_user.id] = [msg.message_id]
            await state.set_state(Form.uploading_multiple_zip_files)
        return

    data = await state.get_data()
    spec_text = data.get("specification", "")
    spec_image_ids = data.get("spec_image_ids", [])
    spec_doc_ids = data.get("spec_doc_ids", [])

    # Обрабатываем текст (как обычное сообщение, так и подпись к медиа)
    text_content = message.text or message.caption
    if text_content:
        spec_text += ("\n" if spec_text else "") + text_content.strip()

    # Обрабатываем фото
    if message.photo:
        largest_photo = message.photo[-1]
        spec_image_ids.append(largest_photo.file_id)

    # Обрабатываем документы
    if message.document:
        spec_doc_ids.append(message.document.file_id)

    await state.update_data(
        specification=spec_text,
        spec_image_ids=spec_image_ids,
        spec_doc_ids=spec_doc_ids
    )

    await message.answer(
        "✅ Добавлено. Можете отправить ещё текст или изображение.\n"
        "Когда всё готово — нажмите *Готово*",
        reply_markup=ready_kb,
        parse_mode="Markdown"
    )

@router.message(Form.entering_canvas_link)
async def enter_canvas_link(message: Message, state: FSMContext):
    """Обрабатывает ссылку на Canvas"""
    # Проверяем, не отмена ли это
    if message.text and message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
        return

    # Проверяем, что текст является корректной ссылкой
    if not message.text or not VALID_LINK_REGEX.match(message.text.strip()):
        await message.answer("Пожалуйста, отправьте корректную ссылку вида:\nhttps://chatgpt.com/canvas/shared/...")
        return

    # Сохраняем ссылку
    await state.update_data(canvas_link=message.text.strip())

    msg = await message.answer(
        "Загрузите ZIP архивы с картинками (можно несколько).\n"
        "Когда закончите, нажмите 'Готово':",
        reply_markup=ready_kb
    )
    last_messages[message.from_user.id] = [msg.message_id]
    await state.set_state(Form.uploading_multiple_zip_files)

@router.message(Form.uploading_multiple_zip_files)
async def upload_multiple_zip_files(message: Message, state: FSMContext):
    """Обрабатывает загрузку ZIP файлов"""
    # Проверяем, не отмена ли это
    if message.text and message.text == "❌ Отмена":
        await state.clear()
        await message.answer("Действие отменено. Возвращаю в главное меню ⬅️", reply_markup=get_menu_keyboard(message.from_user.id))
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
    """Финализирует заявку на лендинг"""
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

    kb = get_admin_processing_keyboard(user_id)

    caption_text = "🖼️ Картинки" if landing_category == "create" else "📄 Лендинг"

    # Отправляем все ZIP файлы
    for i, zip_file_id in enumerate(zip_files, 1):
        caption = f"{caption_text} ({i}/{len(zip_files)})" if len(zip_files) > 1 else caption_text
        await send_document_to_admins(message.bot, document=zip_file_id, caption=caption)

    # Отправляем изображения и документы из ТЗ
    for file_id in spec_images:
        await send_photo_to_admins(message.bot, file_id)
    for file_id in spec_docs:
        await send_document_to_admins(message.bot, document=file_id)

    message_text = (
        f"🆔 Заявка: {order_id}\n"
        f"👤 От: @{username} (ID: {user_id})\n"
        f"📝 Оффер: {offer_name}\n"
        f"🔧 Категория: {category}\n"
        f"📦 Количество архивов: {len(zip_files)}\n"
        f"📝 ТЗ: {specification}\n"
        + (f"🔗 Ссылка на Canvas: {canvas_link}\n" if canvas_link else "")
    )

    await send_notification_with_buttons(
        message.bot,
        message_text,
        reply_markup=kb
    )

    # Сохраняем в Google Sheets
    try:
        gc = gspread.service_account(filename='credentials.json')
        table = gc.open_by_key(GOOGLE_SHEET_ID)
        worksheet = table.get_worksheet(0)
        worksheet.append_row([order_id, username, user_id, offer_name, category, specification, canvas_link])
    except Exception:
        pass  # Если не удалось сохранить в таблицу, продолжаем работу

    await message.answer(f"Ваша заявка {order_id} отправлена администратору.", reply_markup=get_menu_keyboard(message.from_user.id))
    await state.clear()
