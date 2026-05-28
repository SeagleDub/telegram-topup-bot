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
LUBOYDOMEN_API_TOKEN = os.getenv("LUBOYDOMEN_API_TOKEN")

# AdsCard API (банк для функции "Действия с картами")
ADSCARD_TOKEN = os.getenv("ADSCARD_TOKEN")            # Bearer-токен (заголовок Application-Authorization)
ADSCARD_AUTH_TOKEN = os.getenv("ADSCARD_AUTH_TOKEN")  # auth_token в теле запроса

# MultiCards API (банк для функции "Действия с картами")
# Токен получается логином по email/password, кэшируется в памяти сервиса.
MULTICARDS_EMAIL = os.getenv("MULTICARDS_EMAIL")
MULTICARDS_PASSWORD = os.getenv("MULTICARDS_PASSWORD")

# ID пользователей
ADMIN_ID = int(os.getenv("ADMIN_ID"))
TEAMLEADER_ID = int(os.getenv("TEAMLEADER_ID"))

# Настройка Bugsnag
import bugsnag
if BUGSNAG_TOKEN:
    bugsnag.configure(api_key=BUGSNAG_TOKEN)
