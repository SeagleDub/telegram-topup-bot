"""
Аутентификация: единая точка проверки доступа к боту.

Почему middleware, а не проверка в каждом хендлере: ручной вызов
`is_user_allowed` нужно не забыть при добавлении каждой новой функции. На
момент внедрения он стоял на 8 хендлерах из 14 — три пропуска пришлись на
самые опасные (действия с картами, SMS Google Ads, покупка номеров), и
пропущены они были не по злому умыслу, а потому что о проверке просто не
вспомнили. Middleware закрывает всё по умолчанию: чтобы новая функция
оказалась публичной, её нужно явно внести в PUBLIC_COMMANDS.

Закрыты и «скрытые» функции: кнопка, закомментированная в keyboards.py, не
отключает хендлер — он ловит текст сообщения, и его достаточно набрать вручную.

Политика fail-closed: неизвестный пользователь не проходит. Отказ Google
Sheets не блокирует всех, пока жив кэш (см. utils.get_whitelist).
"""
import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from utils import is_admin, is_user_allowed

logger = logging.getLogger(__name__)

# Единственное, что доступно без вайтлиста: команда старта. Всё остальное
# закрыто по умолчанию.
PUBLIC_COMMANDS = frozenset({"/start"})

DENY_TEXT = "❌ У вас нет доступа к этому боту."


def _is_public(event: TelegramObject) -> bool:
    """Событие относится к публичной команде (доступно без вайтлиста)."""
    if isinstance(event, Message) and event.text:
        # "/start", "/start payload", "/start@botname"
        head = event.text.strip().split(maxsplit=1)[0].split("@", 1)[0]
        return head in PUBLIC_COMMANDS
    return False


class AuthMiddleware(BaseMiddleware):
    """Пропускает дальше только админа, тимлидера и пользователей вайтлиста."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            # Событие без пользователя (например, служебное) — не пропускаем:
            # проверить некого, значит подтвердить право доступа нельзя.
            logger.warning("[auth] событие без пользователя отклонено: %s", type(event).__name__)
            return None

        if _is_public(event):
            return await handler(event, data)

        if is_user_allowed(user.id):
            data["is_admin"] = is_admin(user.id)
            return await handler(event, data)

        logger.warning(
            "[auth] доступ запрещён: user_id=%s username=%r событие=%s",
            user.id, user.username, type(event).__name__,
        )
        await _deny(event)
        return None


async def _deny(event: TelegramObject) -> None:
    """Сообщает пользователю об отказе, не раскрывая устройство проверки."""
    try:
        if isinstance(event, CallbackQuery):
            await event.answer(DENY_TEXT, show_alert=True)
        elif isinstance(event, Message):
            await event.answer(DENY_TEXT)
    except Exception as e:
        # Ответ мог не уйти (блок бота, устаревший колбэк) — отказ уже случился,
        # это не повод падать, но и молчать не будем.
        logger.warning("[auth] не удалось отправить отказ: %s: %s", type(e).__name__, e)


def admin_only(handler):
    """Декоратор: пускает только админа и тимлидера.

    Аутентификацию (кто это вообще) делает AuthMiddleware. Этот декоратор —
    про авторизацию: какого уровня доступ нужен конкретному действию. Разница
    существенна: наличие проверки «пользователь известен» не означает, что у
    него есть право на административное действие.
    """
    async def wrapper(event: TelegramObject, *args, **kwargs):
        user = getattr(event, "from_user", None)
        if user is None or not is_admin(user.id):
            uid = getattr(user, "id", None)
            logger.warning(
                "[auth] админское действие отклонено: user_id=%s handler=%s",
                uid, getattr(handler, "__name__", "?"),
            )
            if isinstance(event, CallbackQuery):
                await event.answer("❌ У вас нет доступа к этой функции.", show_alert=True)
            elif isinstance(event, Message):
                await event.answer("❌ У вас нет доступа к этой функции.")
            return None
        return await handler(event, *args, **kwargs)

    wrapper.__name__ = getattr(handler, "__name__", "wrapper")
    wrapper.__doc__ = handler.__doc__
    return wrapper
