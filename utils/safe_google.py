# utils/safe_google.py
import logging
import time
import json
import os
from functools import wraps
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from config import GOOGLE_CREDENTIALS_JSON, SHEET_ID, TIMEZONE
from datetime import datetime
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
    print("\n" + "="*80)
    print("🔧🔧🔧 DEBUG SAFE_APPEND_TO_SHEET ВЫЗВАНА!")
    # ... принты ...
    
    credentials = get_google_credentials()
    if not credentials:
        print("❌ Нет credentials для Google API")
        return False
    
    try:
        service = build('sheets', 'v4', credentials=credentials)
        body = {'values': values}
        print(f"🔧 Отправляю запрос к Google Sheets...")
        
        result = service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=sheet_name,
            valueInputOption='RAW',  # ← ДОЛЖНО БЫТЬ RAW!
            body=body
        ).execute()
        
        print(f"🔧 Google Sheets ответил: {result}")
        print(f"✅ Добавлено {result.get('updates', {}).get('updatedCells', 0)} ячеек в {sheet_name}")
        return True

    except Exception as e:
        print(f"❌❌❌ ОШИБКА в safe_append_to_sheet: {e}")
        import traceback
        traceback.print_exc()
        return False

@retry_google_api()
def safe_update_sheet_row(spreadsheet_id, sheet_name, row_index, values):
    """Обновляет строку в таблице по индексу строки"""
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

@retry_google_api()
def safe_update_sheet_row_by_id(spreadsheet_id, sheet_name, record_id, updated_values):
    """Находит и обновляет строку по ID записи (более надежно)"""
    credentials = get_google_credentials()
    if not credentials:
        return False
    
    try:
        service = build('sheets', 'v4', credentials=credentials)
        
        # 1. Сначала находим строку с нужным ID
        # Читаем колонку A (ID записей) начиная с 3 строки
        range_name = f"{sheet_name}!A3:A"
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_name
        ).execute()
        
        values = result.get('values', [])
        
        # 2. Ищем нужный ID
        for i, row in enumerate(values):
            if row and str(row[0]).strip() == str(record_id):
                # Нашли! Строка = индекс + 3 (т.к. данные с 3 строки)
                row_number = i + 3
                
                # 3. Обновляем строку
                update_range = f"{sheet_name}!A{row_number}"
                body = {'values': [updated_values]}
                
                update_result = service.spreadsheets().values().update(
                    spreadsheetId=spreadsheet_id,
                    range=update_range,
                    valueInputOption='RAW',
                    body=body
                ).execute()
                
                logger.info(f"✅ Обновлена запись {record_id} в строке {row_number}")
                return True
        
        logger.error(f"❌ Запись с ID {record_id} не найдена в таблице")
        return False
        
    except Exception as e:
        logger.error(f"❌ Ошибка при обновлении записи {record_id}: {e}")
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
        
        # Убедимся, что время в правильном формате с часовым поясом
        # Если пришло datetime object, конвертируем в строку с часовым поясом
        tz_str = str(TIMEZONE)
        
        event = {
            'summary': summary,
            'start': {
                'dateTime': start_time if isinstance(start_time, str) else start_time.isoformat(),
                'timeZone': tz_str
            },
            'end': {
                'dateTime': end_time if isinstance(end_time, str) else end_time.isoformat(),
                'timeZone': tz_str
            },
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
    
    logger.info(f"🔄🔄🔄 safe_update_calendar_event ВЫЗВАНА!")
    logger.info(f"🔄 calendar_id: {calendar_id}")
    logger.info(f"🔄 event_id: {event_id}")
    logger.info(f"🔄 summary: {summary}")
    logger.info(f"🔄 start_time: {start_time}")
    logger.info(f"🔄 end_time: {end_time}")
    logger.info(f"🔄 color_id: {color_id}")

    creds = get_google_credentials()
    if not creds:
        logger.error("❌ Нет credentials для Google API")
        return None
    try:
        service = build('calendar', 'v3', credentials=creds)
        
        # Сначала получаем текущее событие
        logger.info(f"🔄 Получаю событие {event_id} из календаря...")
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
        
        logger.info(f"🔄 Обновляю событие в календаре...")
        updated_event = service.events().update(
            calendarId=calendar_id,
            eventId=event_id,
            body=event
        ).execute()
        
        logger.info(f"✅ Событие обновлено: {updated_event.get('id')}")
        return updated_event.get('id')
        
    except Exception as e:
        logger.error("❌❌❌ КРИТИЧЕСКАЯ ОШИБКА в safe_update_calendar_event!")
        logger.error(f"❌ Тип ошибки: {type(e).__name__}")
        logger.error(f"❌ Сообщение: {str(e)}")
        logger.error(f"❌ calendar_id: {calendar_id}")
        logger.error(f"❌ event_id: {event_id}")
        logger.error(f"❌ summary: {summary}")
        logger.error(f"❌ start_time: {start_time}")
        logger.error(f"❌ end_time: {end_time}")
        
        # Добавить полный traceback
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"❌ Traceback:\n{error_details}")
        
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

@retry_google_api()
def safe_sort_sheet_records(spreadsheet_id):
    """
    Сортирует лист 'Записи' по дате (колонка G) и времени (колонка H)
    Возвращает True при успехе, False при ошибке
    """
    try:
        logger.info(f"🔄 Начинаю сортировку таблицы 'Записи'...")
        credentials = get_google_credentials()
        if not credentials:
            logger.error("❌ Нет credentials для Google API")
            return False
        
        service = build('sheets', 'v4', credentials=credentials)
        
        # 1. Находим sheet_id листа "Записи"
        logger.info("🔍 Ищу лист 'Записи'...")
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheet_id = None
        
        for sheet in spreadsheet.get('sheets', []):
            sheet_title = sheet.get('properties', {}).get('title', '').strip()
            logger.info(f"🔍 Найден лист: '{sheet_title}'")
            if sheet_title == 'Записи':
                sheet_id = sheet.get('properties', {}).get('sheetId')
                break
        
        if not sheet_id:
            logger.error("❌ Лист 'Записи' не найден в таблице")
            return False
        
        logger.info(f"✅ Найден лист 'Записи', sheet_id: {sheet_id}")
        
        # 2. Создаем запрос на сортировку (БЕЗ ЛИШНИХ ПРОБЕЛОВ В КЛЮЧАХ!)
        sort_request = {
            "requests": [
                {
                    "sortRange": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 2,      # Начинаем с 3 строки (A3)
                            "endRowIndex": 1000,     # До 1000 строк
                            "startColumnIndex": 0,   # Колонка A
                            "endColumnIndex": 15     # До колонки O (15 колонок)
                        },
                        "sortSpecs": [
                            {
                                "dimensionIndex": 6,  # Колонка G (дата)
                                "sortOrder": "ASCENDING"
                            },
                            {
                                "dimensionIndex": 7,  # Колонка H (время)
                                "sortOrder": "ASCENDING"
                            }
                        ]
                    }
                }
            ]
        }
        
        # 3. Выполняем сортировку
        logger.info("🔧 Отправляю запрос на сортировку...")
        result = service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body=sort_request
        ).execute()
        
        logger.info("✅ Таблица 'Записи' отсортирована успешно")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка при сортировке таблицы: {e}")
        import traceback
        logger.error(f"❌ Traceback: {traceback.format_exc()}")
        return False

def safe_log_missed_call(phone_from: str, admin_phone: str, note: str = "", 
                         is_message: bool = True, client_name: str = None):
    """Записывает сообщение или запрос обратного звонка в таблицу"""
    try:
        timestamp = datetime.now(TIMEZONE).strftime("%d.%m.%Y %H:%M")
        
        # Если имя не указано, используем "Неизвестно"
        if not client_name or client_name.strip() == "":
            client_name = "Неизвестно"
        
        if is_message:
            # Сообщение через Telegram
            row = [
                str(int(time.time())),  # A: ID
                timestamp,              # B: Дата запроса
                client_name or "Неизвестно",  # C: Имя клиента ← ПЕРЕДАЕМ ИМЯ!
                phone_from,             # D: Контакты клиента
                "Сообщение",           # E: Тип запроса
                "ожидает",             # F: Статус
                note,                  # G: Примечание
                "1"                    # H: Приоритет
            ]
        else:
            # Запрос обратного звонка
            row = [
                str(int(time.time())),  # A: ID
                timestamp,              # B: Дата запроса
                client_name or "Неизвестно",  # C: Имя клиента ← ПЕРЕДАЕМ ИМЯ!
                phone_from,             # D: Контакты клиента
                "Звонок",              # E: Тип запроса
                "ожидает",             # F: Статус
                note,                  # G: Примечание
                "2"                    # H: Приоритет
            ]
        
        # Записываем в колонки A:H (8 колонок)
        success = safe_append_to_sheet(SHEET_ID, "Обратные звонки!A3:H", [row])
        return success
        
    except Exception as e:
        print(f"❌ Ошибка записи: {e}")
        return False



print("✅ Модуль safe_google.py загружен.")
