# utils/safe_google.py
import time
import logging
from functools import wraps
from .google import *

logger = logging.getLogger(__name__)

def retry_google_api(max_retries=3, delay=1):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        logger.error(f"Google API error after {max_retries} attempts: {e}")
                        raise e
                    logger.warning(f"Google API attempt {attempt + 1} failed: {e}")
                    time.sleep(delay * (2 ** attempt))
            return None
        return wrapper
    return decorator

@retry_google_api()
def safe_get_sheet_data(sheet_id, range_name):
    return get_sheet_data(sheet_id, range_name)

@retry_google_api()
def safe_append_to_sheet(sheet_id, range_name, values):
    return append_to_sheet(sheet_id, range_name, values)

@retry_google_api()
def safe_update_sheet_row(sheet_id, sheet_name, row_index, values):
    return update_sheet_row(sheet_id, sheet_name, row_index, values)

@retry_google_api()
def safe_create_calendar_event(calendar_id, summary, start_time, end_time, color_id, description):
    return create_calendar_event(calendar_id, summary, start_time, end_time, color_id, description)

# Остальные функции Google API...
