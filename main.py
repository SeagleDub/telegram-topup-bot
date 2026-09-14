"""
Главный файл телеграм бота для пополнения и управления расходниками
"""
import asyncio
import logging
from typing import List
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)

from config import API_TOKEN
from middlewares import AuthMiddleware, ThrottleMiddleware
from handlers import (
    common,
    topup,
    supplies,
    landing,
    unicalization,
    translation,
    expenses,
    google_sms,
    purchase_numbers,
    auto_renewal,
    card_actions,
    card_group_expenses,
    video_cloud
)
from services import video
from services.video_queue import run_video_queue
from services.whitelist_sync import run_whitelist_sync

def create_dispatcher() -> Dispatcher:
    """Собирает диспетчер: middleware + роутеры.

    Вынесено из main() отдельной функцией, чтобы обвязку можно было проверить
    без запуска polling: подключение middleware — то место, где уже пряталась
    ошибка (inner вместо outer), и оно должно быть тестируемо без обращения к
    Telegram API.
    """
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # ВАЖНО: именно outer_middleware, а не middleware.
    #
    # В aiogram 3 inner-middleware (observer.middleware) применяется только к
    # хендлерам, зарегистрированным на этом же обсервере. Собственных хендлеров
    # у Dispatcher нет — все они в дочерних роутерах, — поэтому inner-вариант
    # не выполнился бы ни разу, и бот остался бы полностью открытым, выглядя
    # при этом защищённым.
    #
    # outer_middleware оборачивает распространение события целиком, включая
    # все дочерние роутеры, и срабатывает до фильтров хендлеров.
    #
    # Регистрируем на message и callback_query: инлайн-кнопки — отдельный тип
    # события, проверка только на message их не покрывает. Других типов
    # обновлений бот не обрабатывает.
    #
    # Порядок: сначала аутентификация — посторонний не должен даже расходовать
    # лимит частоты.
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(AuthMiddleware())
        observer.outer_middleware(ThrottleMiddleware())

    # Подключаем роутеры обработчиков
    dp.include_router(common.router)
    dp.include_router(topup.router)
    dp.include_router(supplies.router)
    dp.include_router(landing.router)
    dp.include_router(unicalization.router)
    dp.include_router(translation.router)
    dp.include_router(expenses.router)
    dp.include_router(google_sms.router)
    dp.include_router(purchase_numbers.router)
    dp.include_router(auto_renewal.router)
    dp.include_router(card_actions.router)
    dp.include_router(card_group_expenses.router)
    dp.include_router(video_cloud.router)

    return dp


def start_video_background_tasks(bot: Bot) -> List[asyncio.Task]:
    """Поднимает фоновые задачи фичи «Видео для Cloud».

    Отсутствие ffmpeg не роняет бота целиком: остальные функции от него не
    зависят, и лишать команду работы с картами из-за неустановленного пакета
    было бы хуже самой проблемы. Но и продолжать молча нельзя — без опросчика
    загруженное видео уходило бы в никуда. Поэтому: громкая ошибка в лог,
    задачи не стартуют, а кнопка в меню честно отвечает «недоступно»
    (см. handlers/video_cloud.py).
    """
    try:
        video.ensure_tools_available()
    except video.VideoError as e:
        logger.error(
            "[startup] обработка видео отключена: %s. "
            "Кнопка «Видео для Cloud» будет отвечать отказом.", e,
        )
        return []

    tasks = [
        asyncio.create_task(run_whitelist_sync(), name="whitelist-sync"),
        asyncio.create_task(run_video_queue(bot), name="video-queue"),
    ]
    logger.info("[startup] фоновые задачи обработки видео запущены")
    return tasks


async def main():
    """Главная функция запуска бота"""
    bot = Bot(token=API_TOKEN)
    dp = create_dispatcher()

    tasks = start_video_background_tasks(bot)

    # Удаляем вебхук и запускаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        # Без явной отмены интерпретатор при выходе ругается на незавершённые
        # задачи, а сами они могут успеть сделать лишний проход по очереди.
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

if __name__ == "__main__":
    asyncio.run(main())
