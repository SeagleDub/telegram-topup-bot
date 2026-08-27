"""
Утилиты для работы с сообщениями и администрированием.

Вайтлист пользователей живёт в Google Sheets и кэшируется в памяти на
WHITELIST_TTL секунд: проверка доступа выполняется на каждое событие, а
сетевой вызов на каждое событие недопустим.

Отказ Google Sheets НЕ приводит к тихой блокировке всех: пока есть прошлый
успешный ответ — используется он (устаревший, но рабочий), и в лог пишется
ошибка. Если успешного ответа ещё не было — доступ закрыт всем (fail-closed).
"""
import logging
import time
from typing import Dict, List, Optional, Set
import gspread
from config import ADMIN_ID, TEAMLEADER_ID, GOOGLE_SHEET_ID
from aiogram import Bot

logger = logging.getLogger(__name__)

# Глобальные переменные для хранения состояния сообщений
last_messages: Dict[int, List[int]] = {}
linked_messages: Dict[str, str] = {}  # Словарь для связывания сообщений админа и тимлидера

# --------------------------------------------------------------------------- #
# Вайтлист: кэш и загрузка
# --------------------------------------------------------------------------- #
WHITELIST_TTL = 300           # сколько секунд считать кэш свежим
GOOGLE_CREDENTIALS_FILE = "credentials.json"
WHITELIST_WORKSHEET_INDEX = 1  # лист с ID пользователей
WHITELIST_COLUMN = 1           # колонка с ID


class WhitelistUnavailable(RuntimeError):
    """Вайтлист не удалось прочитать и годного кэша нет."""


_whitelist_cache: Dict[str, object] = {"ids": None, "loaded_at": 0.0}


def _fetch_whitelist_from_sheet() -> Set[int]:
    """Читает ID пользователей из Google Sheets. Бросает исключение при ошибке.

    Намеренно не глушит исключение: отличить «таблица недоступна» от
    «таблица пуста» иначе невозможно, а разница между ними — это разница
    между сбоем инфраструктуры и осознанно пустым списком доступа.
    """
    gc = gspread.service_account(filename=GOOGLE_CREDENTIALS_FILE)
    table = gc.open_by_key(GOOGLE_SHEET_ID)
    worksheet = table.get_worksheet(WHITELIST_WORKSHEET_INDEX)
    raw = worksheet.col_values(WHITELIST_COLUMN)
    return {int(value) for value in raw if str(value).strip().isdigit()}


def get_whitelist(force_refresh: bool = False) -> Set[int]:
    """Возвращает вайтлист из кэша, обновляя его по TTL.

    При ошибке чтения отдаёт последний успешный кэш (устаревший) и пишет в лог.
    Если успешного чтения ещё не было — бросает WhitelistUnavailable.
    """
    cached = _whitelist_cache.get("ids")
    age = time.monotonic() - float(_whitelist_cache.get("loaded_at") or 0.0)

    if cached is not None and not force_refresh and age < WHITELIST_TTL:
        return cached  # type: ignore[return-value]

    try:
        ids = _fetch_whitelist_from_sheet()
    except Exception as e:
        if cached is not None:
            logger.error(
                "[whitelist] не удалось обновить список из Google Sheets (%s: %s). "
                "Использую кэш возрастом %.0f с — доступ может быть неактуальным.",
                type(e).__name__, e, age,
            )
            return cached  # type: ignore[return-value]
        logger.error(
            "[whitelist] не удалось прочитать список из Google Sheets и кэша нет "
            "(%s: %s). Доступ закрыт всем, кроме админа и тимлидера.",
            type(e).__name__, e,
        )
        raise WhitelistUnavailable(str(e)) from e

    if not ids:
        # Пустая таблица — валидный ответ, но почти наверняка ошибка настройки.
        logger.warning(
            "[whitelist] Google Sheets вернул пустой список пользователей "
            "(лист %s, колонка %s). Доступ будет только у админа и тимлидера.",
            WHITELIST_WORKSHEET_INDEX, WHITELIST_COLUMN,
        )

    _whitelist_cache["ids"] = ids
    _whitelist_cache["loaded_at"] = time.monotonic()
    return ids


def is_admin(user_id: int) -> bool:
    """Админ или тимлидер — повышенный уровень доступа."""
    return user_id == ADMIN_ID or user_id == TEAMLEADER_ID

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
    """Разрешён ли пользователю доступ к функциям бота (fail-closed).

    Основная точка проверки — middlewares.auth.AuthMiddleware. Эта функция
    оставлена как переиспользуемый предикат и для проверок вне middleware.
    """
    if is_admin(user_id):
        return True
    try:
        return user_id in get_whitelist()
    except WhitelistUnavailable:
        return False


def get_user_ids_from_sheet() -> List[int]:
    """Список ID пользователей из вайтлиста. Пустой список при недоступности."""
    try:
        return sorted(get_whitelist())
    except WhitelistUnavailable:
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
        if linked_key in linked_messages:
            del linked_messages[linked_key]

async def send_notification_with_buttons(bot: Bot, message_text: str, reply_markup):
    """Отправляет уведомление с кнопками админу и тимлидеру, сохраняет связи между сообщениями"""
    message_ids = await send_notification_to_admins(bot, message_text, reply_markup=reply_markup)

    # Сохраняем связь между сообщениями
    admin_msg_id = message_ids["admin"]
    teamleader_msg_id = message_ids["teamleader"]
    linked_messages[f"{ADMIN_ID}:{admin_msg_id}"] = f"{TEAMLEADER_ID}:{teamleader_msg_id}"
    linked_messages[f"{TEAMLEADER_ID}:{teamleader_msg_id}"] = f"{ADMIN_ID}:{admin_msg_id}"
