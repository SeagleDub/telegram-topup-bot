"""
Главный файл телеграм бота для пополнения и управления расходниками
"""
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

from config import API_TOKEN
from middlewares import AuthMiddleware, ThrottleMiddleware
from handlers import (
    common,
    topup,
    supplies,
    landing,
    unicalization,
    pixel,
    translation,
    expenses,
    google_sms,
    purchase_numbers,
    auto_renewal,
    card_actions,
    card_group_expenses
)

async def main():
    """Главная функция запуска бота"""
    # Создаем экземпляры бота и диспетчера
    bot = Bot(token=API_TOKEN)
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
    dp.include_router(pixel.router)
    dp.include_router(translation.router)
    dp.include_router(expenses.router)
    dp.include_router(google_sms.router)
    dp.include_router(purchase_numbers.router)
    dp.include_router(auto_renewal.router)
    dp.include_router(card_actions.router)
    dp.include_router(card_group_expenses.router)

    # Удаляем вебхук и запускаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
