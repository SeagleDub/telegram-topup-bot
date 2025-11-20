"""
Утилиты для работы с сообщениями и администрированием
"""
from typing import Dict, List
import gspread
from config import ADMIN_ID, TEAMLEADER_ID, GOOGLE_SHEET_ID
from aiogram import Bot

# Глобальные переменные для хранения состояния сообщений
last_messages: Dict[int, List[int]] = {}
linked_messages: Dict[str, str] = {}  # Словарь для связывания сообщений админа и тимлидера

async def delete_last_messages(user_id: int, bot: Bot):
    """Удаляет последние сообщения пользователя"""
    ids = last_messages.get(user_id, [])
    for msg_id in ids:
        try:
            await bot.delete_message(chat_id=user_id, message_id=msg_id)
        except Exception:
            pass
    last_messages[user_id] = []

def is_user_allowed(user_id: int) -> bool:
    """Проверяет, разрешен ли пользователю доступ к функциям бота"""
    # Администратор и тимлидер всегда имеют доступ ко всем функциям
    if user_id == ADMIN_ID or user_id == TEAMLEADER_ID:
        return True

    user_ids = get_user_ids_from_sheet()
    if not user_ids:
        return False  # Если список пуст, доступ запрещен

    return user_id in user_ids

def get_user_ids_from_sheet() -> List[int]:
    """Получает список ID пользователей из Google Sheets"""
    try:
        gc = gspread.service_account(filename='credentials.json')
        table = gc.open_by_key(GOOGLE_SHEET_ID)
        worksheet = table.get_worksheet(1)
        user_ids = worksheet.col_values(1)

        return [int(user_id) for user_id in user_ids if user_id.isdigit()]
    except Exception:
        return []

async def send_notification_to_admins(bot: Bot, message_text: str, reply_markup=None):
    """Отправляет уведомление админу и тимлидеру"""
    # Отправляем админу
    admin_msg = await bot.send_message(ADMIN_ID, message_text, reply_markup=reply_markup)
    # Отправляем тимлидеру
    teamleader_msg = await bot.send_message(TEAMLEADER_ID, message_text, reply_markup=reply_markup)
    return {"admin": admin_msg.message_id, "teamleader": teamleader_msg.message_id}

async def send_document_to_admins(bot: Bot, document, caption=None):
    """Отправляет документ админу и тимлидеру"""
    await bot.send_document(ADMIN_ID, document=document, caption=caption)
    await bot.send_document(TEAMLEADER_ID, document=document, caption=caption)

async def send_photo_to_admins(bot: Bot, photo):
    """Отправляет фото админу и тимлидеру"""
    await bot.send_photo(ADMIN_ID, photo)
    await bot.send_photo(TEAMLEADER_ID, photo)

async def update_linked_messages(bot: Bot, current_chat_id: int, current_message_id: int, new_text: str):
    """Обновляет связанное сообщение у другого админа"""
    current_key = f"{current_chat_id}:{current_message_id}"
    if current_key in linked_messages:
        linked_key = linked_messages[current_key]
        chat_id, message_id = linked_key.split(":")
        try:
            await bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=new_text
            )
        except Exception as e:
            print(f"Ошибка при обновлении связанного сообщения: {e}")

        # Удаляем обе записи из словаря после обработки
        del linked_messages[current_key]
        del linked_messages[linked_key]

async def send_notification_with_buttons(bot: Bot, message_text: str, reply_markup):
    """Отправляет уведомление с кнопками админу и тимлидеру, сохраняет связи между сообщениями"""
    message_ids = await send_notification_to_admins(bot, message_text, reply_markup=reply_markup)

    # Сохраняем связь между сообщениями
    admin_msg_id = message_ids["admin"]
    teamleader_msg_id = message_ids["teamleader"]
    linked_messages[f"{ADMIN_ID}:{admin_msg_id}"] = f"{TEAMLEADER_ID}:{teamleader_msg_id}"
    linked_messages[f"{TEAMLEADER_ID}:{teamleader_msg_id}"] = f"{ADMIN_ID}:{admin_msg_id}"
