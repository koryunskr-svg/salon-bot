# config.py
import os
from dotenv import load_dotenv
import pytz

load_dotenv()  # ← ЭТА СТРОКА ОБЯЗАТЕЛЬНА

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")
CALENDAR_ID = os.getenv("CALENDAR_ID")
SHEET_ID = os.getenv("SHEET_ID")
TIMEZONE = pytz.timezone(os.getenv("TZ", "Europe/Moscow"))
RESERVATION_TIMEOUT = 120
WARNING_TIMEOUT = 60
