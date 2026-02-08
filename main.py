"""
Главный файл телеграм бота для пополнения и управления расходниками
"""
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from config import API_TOKEN
from handlers import (
    common,
    topup,
    supplies,
    landing,
    unicalization,
    pixel,
    broadcast,
    translation,
    expenses,
    google_sms,
    purchase_numbers
)

async def main():
    """Главная функция запуска бота"""
    # Создаем экземпляры бота и диспетчера
    bot = Bot(token=API_TOKEN)
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Подключаем роутеры обработчиков
    dp.include_router(common.router)
    dp.include_router(topup.router)
    dp.include_router(supplies.router)
    dp.include_router(landing.router)
    dp.include_router(unicalization.router)
    dp.include_router(pixel.router)
    dp.include_router(broadcast.router)
    dp.include_router(translation.router)
    dp.include_router(expenses.router)
    dp.include_router(google_sms.router)
    dp.include_router(purchase_numbers.router)

    # Удаляем вебхук и запускаем polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
