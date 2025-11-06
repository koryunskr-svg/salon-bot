# utils/safe_google.py
import time
import logging
from functools import wraps
from .google import * # Импортируем все функции из google.py

logger = logging.getLogger(__name__)

def retry_google_api(max_retries=3, delay=1):
    """Декоратор для повторных попыток вызова Google API."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        logger.error(f"❌ Ошибка Google API после {max_retries} попыток в функции {func.__name__}: {e}")
                        raise e # Пробрасываем ошибку, если все попытки исчерпаны
                    logger.warning(f"⚠️ Попытка {attempt + 1} не удалась в {func.__name__}: {e}. Повтор через {delay * (2 ** attempt)} сек...")
                    time.sleep(delay * (2 ** attempt))
            return None
        return wrapper
    return decorator

# Оборачиваем функции из google.py
@retry_google_api()
def safe_get_sheet_data(spreadsheet_id, range_name):
    return get_sheet_data(spreadsheet_id, range_name)

@retry_google_api()
def safe_append_to_sheet(spreadsheet_id, sheet_name, values):
    return append_to_sheet(spreadsheet_id, sheet_name, values)

@retry_google_api()
def safe_update_sheet_row(spreadsheet_id, sheet_name, row_index, values):
    return update_sheet_row(spreadsheet_id, sheet_name, row_index, values)

@retry_google_api()
def safe_create_calendar_event(calendar_id, summary, start_time, end_time, color_id=None, description=None):
    return create_calendar_event(calendar_id, summary, start_time, end_time, color_id, description)

@retry_google_api()
def safe_get_calendar_events(calendar_id, time_min, time_max, query=None):
    return get_calendar_events(calendar_id, time_min, time_max, query)

@retry_google_api()
def safe_update_calendar_event(calendar_id, event_id, summary=None, color_id=None, description=None):
    return update_calendar_event(calendar_id, event_id, summary, color_id, description)

@retry_google_api()
def safe_delete_calendar_event(calendar_id, event_id):
    return delete_calendar_event(calendar_id, event_id)

print("✅ Модуль safe_google.py загружен.")
