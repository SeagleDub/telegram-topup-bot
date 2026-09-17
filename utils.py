"""
Утилиты для работы с сообщениями и администрированием.

Вайтлист пользователей живёт в Google Sheets и кэшируется в памяти на
WHITELIST_TTL секунд: проверка доступа выполняется на каждое событие, а
сетевой вызов на каждое событие недопустим.

Отказ Google Sheets НЕ приводит к тихой блокировке всех: пока есть прошлый
успешный ответ — используется он (устаревший, но рабочий), и в лог пишется
ошибка. Если успешного ответа ещё не было — доступ закрыт всем (fail-closed).
"""
import itertools
import logging
import time
from typing import Dict, List, Optional, Set
import gspread
from config import (
    ADMIN_ID,
    TEAMLEADER_IDS,
    EXPENSE_VIEWER_IDS,
    NOTIFY_IDS,
    ROLE_IDS,
    GOOGLE_SHEET_ID,
)
from aiogram import Bot

logger = logging.getLogger(__name__)

# Глобальные переменные для хранения состояния сообщений
last_messages: Dict[int, List[int]] = {}

# Связь между копиями одного уведомления у разных получателей.
#
# Раньше это была ПАРА (ключ -> единственный связанный ключ): получателей было
# ровно двое. Тимлидеров теперь может быть сколько угодно, поэтому модель —
# ГРУППА: "chat:msg" -> group_id, group_id -> список всех "chat:msg" группы.
# Попытка остаться на паре означала бы, что при трёх получателях кнопка
# обновляется у одного и навсегда зависает у остальных.
linked_messages: Dict[str, str] = {}          # "chat:msg" -> group_id
linked_groups: Dict[str, List[str]] = {}      # group_id -> ["chat:msg", ...]
_group_counter = itertools.count(1)


def _message_key(chat_id: int, message_id: int) -> str:
    return f"{chat_id}:{message_id}"

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
    """Админ или любой из тимлидеров — полный административный доступ.

    Проверяющий расходы сюда НЕ входит: у него своё, более узкое право
    (can_view_buyer_expenses). Расширить is_admin было бы проще всего и
    означало бы выдать роли одобрение заявок и автопродление номеров.
    """
    return user_id == ADMIN_ID or user_id in TEAMLEADER_IDS


def can_view_buyer_expenses(user_id: int) -> bool:
    """Право смотреть расход по ЧУЖОМУ ID («📊 Получить расход по байеру»).

    Отдельный предикат, а не is_admin: это единственное право роли
    «проверяющий расходы». Админы и тимлидеры получают его как надмножество.
    """
    return is_admin(user_id) or user_id in EXPENSE_VIEWER_IDS


def has_configured_role(user_id: int) -> bool:
    """Пользователю роль назначена вручную в .env (любая из них).

    Считается по составу ролей, а не через право доступа: иначе сужение
    любого права молча отрезало бы человеку вход в бота целиком.
    """
    return user_id in ROLE_IDS

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
    if has_configured_role(user_id):
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

async def send_notification_to_admins(bot: Bot, message_text: str, reply_markup=None) -> Dict[int, int]:
    """Рассылает уведомление админу и всем тимлидерам.

    Возвращает {chat_id: message_id} только по успешно доставленным.

    Сбой на одном получателе (заблокировал бота, удалил чат) не должен рвать
    рассылку остальным: заявка одного пользователя не может пропасть у всей
    команды из-за одного чужого чата. Поэтому ошибка — громкая, но поштучная.
    """
    delivered: Dict[int, int] = {}
    for chat_id in NOTIFY_IDS:
        try:
            msg = await bot.send_message(chat_id, message_text, reply_markup=reply_markup)
        except Exception:
            logger.exception(
                "Не доставлено уведомление получателю %s. Текст: %r",
                chat_id, message_text[:200],
            )
            continue
        delivered[chat_id] = msg.message_id

    if not delivered:
        logger.error(
            "Уведомление не дошло НИ ДО КОГО из %s получателей. Текст: %r",
            len(NOTIFY_IDS), message_text[:200],
        )
    return delivered

async def send_document_to_admins(bot: Bot, document, caption=None):
    """Рассылает документ админу и всем тимлидерам (поштучная изоляция ошибок)."""
    for chat_id in NOTIFY_IDS:
        try:
            await bot.send_document(chat_id, document=document, caption=caption)
        except Exception:
            logger.exception("Не доставлен документ получателю %s", chat_id)

async def send_photo_to_admins(bot: Bot, photo):
    """Рассылает фото админу и всем тимлидерам (поштучная изоляция ошибок)."""
    for chat_id in NOTIFY_IDS:
        try:
            await bot.send_photo(chat_id, photo)
        except Exception:
            logger.exception("Не доставлено фото получателю %s", chat_id)

async def update_linked_messages(bot: Bot, current_chat_id: int, current_message_id: int, new_text: str):
    """Проставляет новый текст во всех остальных копиях того же уведомления.

    Заявку обрабатывает кто-то один, но висящая кнопка остаётся у всех
    остальных получателей — их копии надо погасить. Группа снимается целиком,
    повторная обработка того же уведомления становится no-op.
    """
    current_key = _message_key(current_chat_id, current_message_id)
    group_id = linked_messages.pop(current_key, None)
    if group_id is None:
        return

    for member_key in linked_groups.pop(group_id, []):
        linked_messages.pop(member_key, None)
        if member_key == current_key:
            continue  # исходное сообщение уже отредактировал вызывающий хендлер
        chat_id, message_id = member_key.split(":")
        try:
            await bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=new_text
            )
        except Exception:
            logger.exception(
                "Не обновлена связанная копия уведомления %s (группа %s)",
                member_key, group_id,
            )

async def send_notification_with_buttons(bot: Bot, message_text: str, reply_markup):
    """Рассылает уведомление с кнопками и связывает все доставленные копии в группу."""
    delivered = await send_notification_to_admins(bot, message_text, reply_markup=reply_markup)
    if not delivered:
        return delivered

    group_id = f"g{next(_group_counter)}"
    member_keys = [_message_key(chat_id, msg_id) for chat_id, msg_id in delivered.items()]
    linked_groups[group_id] = member_keys
    for key in member_keys:
        linked_messages[key] = group_id
    return delivered
