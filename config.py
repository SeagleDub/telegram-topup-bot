"""
Конфигурация бота
"""
from dotenv import load_dotenv
import os

load_dotenv()

# Токены и ключи
API_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
BUGSNAG_TOKEN = os.getenv("BUGSNAG_TOKEN")

# ID пользователей
ADMIN_ID = int(os.getenv("ADMIN_ID"))
TEAMLEADER_ID = int(os.getenv("TEAMLEADER_ID"))

# Настройка Bugsnag
import bugsnag
bugsnag.configure(api_key=BUGSNAG_TOKEN)
