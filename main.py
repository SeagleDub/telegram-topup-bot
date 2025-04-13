from aiogram import Bot, Dispatcher, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from config import API_TOKEN
from handlers import topup, supplies

bot = Bot(token=7829191204:AAF3utRWorh8gVGp-JbLngiZlaog4F6gf7k)
dp = Dispatcher(bot, storage=MemoryStorage())

# Регистрация хендлеров
supplies.register(dp)
topup.register(dp)

if __name__ == '__main__':
    executor.start_polling(dp)
