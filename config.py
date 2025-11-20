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
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")

# ID пользователей
ADMIN_ID = int(os.getenv("ADMIN_ID"))
TEAMLEADER_ID = int(os.getenv("TEAMLEADER_ID"))

# Настройка Bugsnag
import bugsnag
if BUGSNAG_TOKEN:
    bugsnag.configure(api_key=BUGSNAG_TOKEN)
