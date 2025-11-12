# config.py
import os
from dotenv import load_dotenv
import pytz

# Загрузка переменных окружения из файла .env
load_dotenv()

# Основные переменные из .env
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_CREDENTIALS_JSON_STR = os.getenv("GOOGLE_CREDENTIALS_JSON") # В виде строки
SHEET_ID = os.getenv("SHEET_ID")
CALENDAR_ID = os.getenv("CALENDAR_ID")
TIMEZONE = pytz.timezone(os.getenv("TZ", "Europe/Moscow"))

# --- Настройки, которые могут быть загружены из листа "Настройки" (см. utils/settings.py) ---
# Пока что можно задать здесь, но ТЗ v2.0 предполагает гибкость через таблицу.
# Эти значения будут перезаписаны при загрузке настроек из таблицы.
WORK_HOURS_START = "10:00" # Пример из ТЗ
WORK_HOURS_END = "20:00"   # Пример из ТЗ
SLOT_GENERATION_DAYS = 10  # Пример из ТЗ
RESERVATION_TIMEOUT = 120  # Секунды
WARNING_TIMEOUT = 60       # Секунды
TIMEZONE_NAME = "Europe/Moscow" # Может быть загружено из таблицы

# --- Пример загрузки настроек (реализация в utils/settings.py) ---
# from utils.settings import load_settings_from_table
# settings = load_settings_from_table()
# WORK_HOURS_START = settings.get("Время начала работы", WORK_HOURS_START)
# и т.д. для остальных настроек

# --- Валидация основных переменных ---
REQUIRED_CONFIG = [
    "TELEGRAM_BOT_TOKEN",
    "GOOGLE_CREDENTIALS_JSON_STR",
    "SHEET_ID",
    "CALENDAR_ID",
    "TIMEZONE"
]

missing = [key for key, value in locals().items() if key in REQUIRED_CONFIG and not value]
if missing:
    raise ValueError(f"❌ Не заданы обязательные переменные окружения: {', '.join(missing)}")

print("✅ Конфигурация загружена.")




