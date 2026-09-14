"""
Видео для Cloud: выдача ссылки на Mini App.

Сам файл через бота не проходит: Bot API не отдаёт больше 20 МБ, а исходники
доходят до 250 МБ. Поэтому загрузку делает браузер напрямую в R2, а бот отвечает
за вход в Mini App и за доставку результата (см. services.video_queue).

FSM здесь не нужен: состояний у флоу нет, всё взаимодействие — одна кнопка.
"""
import logging

from aiogram import F, Router
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from config import CF_WORKER_URL
from keyboards import VIDEO_CLOUD_TEXT, get_menu_keyboard
from services.video import MAX_OUTPUT_SECONDS, MAX_WIDTH, tools_available

logger = logging.getLogger(__name__)

router = Router()


@router.message(F.text == VIDEO_CLOUD_TEXT)
async def open_video_cloud(message: Message):
    """Отправляет кнопку, открывающую Mini App."""
    # Обе проверки закрывают один и тот же сценарий: показать рабочую на вид
    # кнопку, за которой загруженный файл уйдёт в никуда. Молчаливый чёрный
    # ящик здесь хуже честного отказа — человек потратит время на заливку
    # 250 МБ и не поймёт, почему ответа нет.
    if not CF_WORKER_URL:
        logger.error("[video-cloud] CF_WORKER_URL не задан — Mini App недоступен")
        await message.answer(
            "❌ Загрузчик видео не настроен. Сообщите администратору.",
            reply_markup=get_menu_keyboard(message.from_user.id),
        )
        return

    if not tools_available():
        logger.error("[video-cloud] ffmpeg недоступен — обработка невозможна")
        await message.answer(
            "❌ Обработка видео сейчас недоступна. Сообщите администратору.",
            reply_markup=get_menu_keyboard(message.from_user.id),
        )
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="🎬 Открыть загрузчик",
                web_app=WebAppInfo(url=CF_WORKER_URL),
            )
        ]]
    )

    await message.answer(
        "Загрузите видео — верну ссылку на версию с autostart.\n\n"
        f"Итоговый файл: до {MAX_OUTPUT_SECONDS} секунд, ширина до {MAX_WIDTH} px, H.264.\n"
        f"Ролики длиннее будут обрезаны — это требование autostart.\n\n"
        "Ссылка придёт сюда же, обычно в течение минуты после загрузки.",
        reply_markup=kb,
    )
