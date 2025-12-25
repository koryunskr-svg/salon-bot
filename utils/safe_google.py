# utils/safe_google.py
import logging
import time
import json
from functools import wraps
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from config import GOOGLE_CREDENTIALS_JSON, TIMEZONE
import pytz

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/calendar']

def get_google_credentials():
    try:
        creds_data = json.loads(GOOGLE_CREDENTIALS_JSON)
        credentials = Credentials.from_service_account_info(creds_data, scopes=SCOPES)
        return credentials
    except Exception as e:
        logger.error(f"❌ Ошибка при создании credentials: {e}")
        return None

def retry_google_api(max_retries=3, delay=2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except HttpError as e:
                    if e.resp.status in [429, 500, 503]:
                        if attempt < max_retries - 1:
                            logger.warning(f"⚠️ Попытка {attempt + 1} не удалась в {func.__name__}: {e}. Повтор через {delay * (2 ** attempt)} сек...")
                            time.sleep(delay * (2 ** attempt))
                        else:
                            logger.error(f"❌ Ошибка Google API после {max_retries} попыток в функции {func.__name__}: {e}")
                            raise
                    else:
                        logger.error(f"❌ Неожиданная ошибка Google API в функции {func.__name__}: {e}")
                        raise
                except Exception as e:
                    logger.error(f"❌ Неожиданная ошибка в функции {func.__name__}: {e}")
                    raise
        return wrapper
    return decorator

@retry_google_api()
def safe_get_sheet_data(spreadsheet_id, range_name):
    credentials = get_google_credentials()
    if not credentials:
        return None
    try:
        service = build('sheets', 'v4', credentials=credentials)
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_name
        ).execute()
        values = result.get('values', [])
        return values
    except Exception as e:
        logger.error(f"❌ Ошибка при чтении данных из таблицы: {e}")
        return None

@retry_google_api()
def safe_append_to_sheet(spreadsheet_id, sheet_name, values):
    credentials = get_google_credentials()
    if not credentials:
        return False
    try:
        service = build('sheets', 'v4', credentials=credentials)
        body = {'values': values}
        result = service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=sheet_name,
            valueInputOption='RAW',
            body=body
        ).execute()
        logger.info(f"✅ Добавлено {result.get('updates', {}).get('updatedCells', 0)} ячеек в {sheet_name}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка при добавлении данных в таблицу: {e}")
        return False

@retry_google_api()
def safe_update_sheet_row(spreadsheet_id, sheet_name, row_index, values):
    credentials = get_google_credentials()
    if not credentials:
        return False
    try:
        service = build('sheets', 'v4', credentials=credentials)
        range_name = f"{sheet_name}!A{row_index}"
        body = {'values': [values]}
        result = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption='RAW',
            body=body
        ).execute()
        logger.info(f"✅ Обновлено {result.get('updatedCells', 0)} ячеек в строке {row_index}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка при обновлении строки в таблице: {e}")
        return False

def safe_get_calendar_events(calendar_id, time_min, time_max):
    credentials = get_google_credentials()
    if not credentials:
        return None
    try:
        service = build('calendar', 'v3', credentials=credentials)
        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        events = events_result.get('items', [])
        return events
    except Exception as e:
        logger.error(f"❌ Ошибка при чтении событий из календаря: {e}")
        return None

def safe_create_calendar_event(calendar_id, summary, start_time, end_time, color_id=None, description=None):
    credentials = get_google_credentials()
    if not credentials:
        return None
    try:
        service = build('calendar', 'v3', credentials=credentials)
        event = {
            'summary': summary,
            'start': {'dateTime': start_time, 'timeZone': str(TIMEZONE)},  # start_time уже строка!
            'end': {'dateTime': end_time, 'timeZone': str(TIMEZONE)},      # end_time уже строка!
            'description': description,
        }
        if color_id:
            event['colorId'] = color_id
        created_event = service.events().insert(calendarId=calendar_id, body=event).execute()
        logger.info(f"✅ Событие '{summary}' создано в календаре")
        return created_event.get('id')
    except Exception as e:
        logger.error(f"❌ Ошибка при создании события в календаре: {e}")
        return None


@retry_google_api()
def safe_update_calendar_event(calendar_id, event_id, summary=None, start_time=None, end_time=None, color_id=None, description=None):
    """Обновляет событие в Google Календаре."""
    creds = get_google_credentials()
    if not creds:
        return None
    try:
        service = build('calendar', 'v3', credentials=creds)
        
        # Сначала получаем текущее событие
        event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        
        # Обновляем поля если они переданы
        if summary:
            event['summary'] = summary
        if color_id:
            event['colorId'] = color_id
        if description:
            event['description'] = description
        if start_time:
            event['start']['dateTime'] = start_time
            event['start']['timeZone'] = str(TIMEZONE)
        if end_time:
            event['end']['dateTime'] = end_time
            event['end']['timeZone'] = str(TIMEZONE)
        
        # Отправляем обновление
        service.events().update(calendarId=calendar_id, eventId=event_id, body=event).execute()
        return True
    except Exception as e:
        logger.error(f'❌ Ошибка обновления события: {e}')
        return None

def safe_delete_calendar_event(calendar_id, event_id):
    credentials = get_google_credentials()
    if not credentials:
        return False
    try:
        service = build('calendar', 'v3', credentials=credentials)
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        logger.info(f"✅ Событие {event_id} удалено из календаря")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка при удалении события {event_id}: {e}")
        return False

print("✅ Модуль safe_google.py загружен.")
