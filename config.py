# config.py 
import os
from dotenv import load_dotenv
import pytz

load_dotenv() # ← ЭТА СТРОКА ОБЯЗАТЕЛЬНА

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON") # Имя без _STR
CALENDAR_ID = os.getenv("CALENDAR_ID")
SHEET_ID = os.getenv("SHEET_ID")
TIMEZONE = pytz.timezone(os.getenv("TZ", "Europe/Moscow"))
# Таймауты теперь берутся напрямую из config, как в анализируемом коде
RESERVATION_TIMEOUT = int(os.getenv("RESERVATION_TIMEOUT", 120)) # 2 минуты
WARNING_TIMEOUT = int(os.getenv("WARNING_TIMEOUT", 60))        # 1 минута

print("✅ config.py загружен.")
