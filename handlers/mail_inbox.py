"""
Почта доменов: письма на info@<domain> в боте.

Флоу: кнопка → домен → список последних писем (инлайн-кнопки) → письмо.
Письма хранит отдельный Worker mail-inbox; бот только читает их через
services.mail_inbox. Устройство и причины: docs/mail-inbox-design.md.

Кнопки списка работают по данным FSM (адрес, письма, id сообщения со
списком): в callback_data помещается только 64 байта, id письма с длинным
доменом туда не влезает. Кнопка из старого списка отвечает «устарел», а не
показывает письма другого домена.
"""
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from keyboards import MAIL_INBOX_TEXT, cancel_kb, get_menu_keyboard
from services import mail_inbox
from services.ecards import KYIV_TZ
from states import Form

logger = logging.getLogger(__name__)

router = Router()

UNAVAILABLE_TEXT = "❌ Почта сейчас недоступна, попробуйте через минуту."
STALE_TEXT = "Список устарел — введите домен ещё раз."

if not mail_inbox.is_configured():
    # Не ошибка старта: без почты остальной бот работает, а кнопка честно
    # ответит «не настроено» (см. start_mail_inbox).
    logger.warning("[mail-inbox] MAIL_INBOX_URL / MAIL_READ_TOKEN не заданы — раздел почты выключен")


def _list_view(address: str, items: list):
    """Текст и клавиатура списка писем. address уже проверен регуляркой — экранировать нечего."""
    now = datetime.now(KYIV_TZ)
    rows = [
        [InlineKeyboardButton(text=mail_inbox.button_label(item, now), callback_data=f"mail:open:{i}")]
        for i, item in enumerate(items)
    ]
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="mail:refresh")])
    text = f"📬 <b>{address}</b>\n" + ("Последние письма:" if items else "Писем пока нет.")
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _current_list(query: CallbackQuery, state: FSMContext) -> dict | None:
    """Данные списка, если кнопка нажата в последнем показанном списке, иначе None."""
    # InaccessibleMessage (сообщение удалено или недоступно боту) не умеет
    # edit_text — для пользователя это тот же устаревший список.
    if not isinstance(query.message, Message):
        return None
    data = await state.get_data()
    if not data.get("mail_address") or data.get("mail_list_message_id") != query.message.message_id:
        return None
    return data


@router.message(F.text == MAIL_INBOX_TEXT)
async def start_mail_inbox(message: Message, state: FSMContext):
    if not mail_inbox.is_configured():
        await message.answer(
            "❌ Почта доменов не настроена. Сообщите администратору.",
            reply_markup=get_menu_keyboard(message.from_user.id),
        )
        return
    await state.set_state(Form.mail_waiting_for_domain)
    await message.answer(
        "📧 Введите домен, например <b>site1.com</b> — покажу письма на info@ этого домена.",
        parse_mode="HTML",
        reply_markup=cancel_kb,
    )


@router.message(Form.mail_waiting_for_domain)
async def show_inbox(message: Message, state: FSMContext):
    try:
        address = mail_inbox.normalize_address(message.text or "")
    except ValueError:
        await message.answer("❌ Не похоже на домен. Пример: site1.com", reply_markup=cancel_kb)
        return

    try:
        items = await mail_inbox.list_messages(address)
    except mail_inbox.MailInboxError:
        logger.exception("[mail-inbox] список писем не получен: %s", address)
        await message.answer(UNAVAILABLE_TEXT, reply_markup=cancel_kb)
        return

    text, kb = _list_view(address, items)
    sent = await message.answer(text, parse_mode="HTML", reply_markup=kb)
    await state.update_data(mail_address=address, mail_items=items, mail_list_message_id=sent.message_id)


@router.callback_query(F.data == "mail:refresh")
async def refresh_inbox(query: CallbackQuery, state: FSMContext):
    data = await _current_list(query, state)
    if data is None:
        await query.answer(STALE_TEXT, show_alert=True)
        return
    address = data["mail_address"]
    try:
        items = await mail_inbox.list_messages(address)
    except mail_inbox.MailInboxError:
        logger.exception("[mail-inbox] обновление списка не удалось: %s", address)
        await query.answer(UNAVAILABLE_TEXT, show_alert=True)
        return

    text, kb = _list_view(address, items)
    try:
        await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except TelegramBadRequest as e:
        # Список не изменился — Telegram отказывается «редактировать» в то же самое.
        if "message is not modified" not in str(e):
            raise
        await state.update_data(mail_items=items)
        await query.answer("Новых писем нет")
        return
    # Только после успешной правки: кнопки выбирают письмо по номеру, и новый
    # список при старых кнопках открыл бы не то письмо, что написано на кнопке.
    await state.update_data(mail_items=items)
    await query.answer("Обновлено")


@router.callback_query(F.data.startswith("mail:open:"))
async def open_mail(query: CallbackQuery, state: FSMContext):
    data = await _current_list(query, state)
    try:
        item = data["mail_items"][int(query.data.rsplit(":", 1)[1])] if data else None
    except (ValueError, IndexError):
        item = None
    if item is None:
        await query.answer(STALE_TEXT, show_alert=True)
        return

    # Ответ на нажатие — сразу: у Telegram на него 15 секунд, а письмо ещё
    # нужно скачать.
    await query.answer()
    try:
        raw = await mail_inbox.fetch_message(item["id"])
    except mail_inbox.MailInboxError:
        logger.exception("[mail-inbox] письмо не получено: %s", item["id"])
        await query.message.answer(UNAVAILABLE_TEXT)
        return

    try:
        await query.message.answer(
            mail_inbox.render_message(raw, item.get("receivedAt")),
            parse_mode="HTML",
            # Для превью Telegram сам открывает ссылку — одноразовая ссылка
            # «войти / подтвердить» сработала бы раньше человека.
            disable_web_page_preview=True,
        )
    except TelegramBadRequest:
        # Нажатие уже подтверждено — без этого ответа человек не увидит ничего.
        logger.exception("[mail-inbox] Telegram не принял письмо: %s", item["id"])
        await query.message.answer("❌ Не удалось показать это письмо в Telegram.")
