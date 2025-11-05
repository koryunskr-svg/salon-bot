# utils/google.py
import json
# import os # Убран неиспользуемый импорт
# from google.auth.transport.requests import Request # Убран неиспользуемый импорт
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from config import GOOGLE_CREDENTIALS_JSON, SHEET_ID, CALENDAR_ID, TIMEZONE # Исправлено имя переменной
import logging # Добавлен импорт logging

# Исправлены SCOPES: убраны лишние пробелы
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar"
]

logger = logging.getLogger(__name__) # Добавлен logger

def get_google_credentials():
    """Получает учётные данные из строки JSON из переменной окружения."""
    # Исправлено имя переменной
    if not GOOGLE_CREDENTIALS_JSON:
        raise EnvironmentError("❌ Переменная окружения GOOGLE_CREDENTIALS_JSON не найдена.")
    try:
        # Исправлено имя переменной
        creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    except json.JSONDecodeError as e:
        raise ValueError(f"❌ Неверный формат JSON в GOOGLE_CREDENTIALS_JSON: {e}")

    credentials = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return credentials

def get_sheet_data(spreadsheet_id: str, range_name: str):
    """Получает данные из Google Таблицы."""
    creds = get_google_credentials()
    service = build("sheets", "v4", credentials=creds)
    sheet = service.spreadsheets()
    result = sheet.values().get(spreadsheetId=spreadsheet_id, range=range_name).execute()
    return result.get("values", [])

def append_to_sheet(spreadsheet_id: str, sheet_name: str, row: list):
    """Добавляет строку в Google Таблицу."""
    creds = get_google_credentials()
    service = build("sheets", "v4", credentials=creds)
    body = {"values": [row]}
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=sheet_name,
        valueInputOption="RAW",
        body=body
    ).execute()

# Исправлено: update_sheet_row теперь обновляет всю строку, а не только A1
def update_sheet_row(spreadsheet_id: str, sheet_name: str, row_index: int, row: list):
    """
    Обновляет строку в Google Таблице.
    row_index: Номер строки (начиная с 1).
    row: Список значений для ячеек строки.
    """
    creds = get_google_credentials()
    service = build("sheets", "v4", credentials=creds)
    
    # Исправлено: range_name теперь охватывает всю строку (предполагаем максимум ZZ колонку)
    # Можно динамически определить последнюю колонку, если известна структура.
    range_name = f"{sheet_name}!A{row_index}:ZZ{row_index}" 
    
    body = {"values": [row]}
    try:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name,
            valueInputOption="RAW",
            body=body
        ).execute()
        logger.info(f"✅ Строка {row_index} в листе '{sheet_name}' обновлена.")
    except Exception as e:
        logger.error(f"❌ Ошибка при обновлении строки {row_index} в листе '{sheet_name}': {e}")
        raise # Пробрасываем исключение


def get_calendar_events(calendar_id: str, time_min, time_max, query=None):
    """Получает события из Google Календаря."""
    creds = get_google_credentials()
    service = build("calendar", "v3", credentials=creds)
    events = service.events().list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        q=query,
        singleEvents=True,
        orderBy="startTime"
    ).execute()
    return events.get("items", [])

# Исправлено: Используем TIMEZONE из config
def create_calendar_event(calendar_id: str, summary: str, start_time: str, end_time: str, color_id=None, description=None):
    """Создаёт событие в Google Календаре."""
    creds = get_google_credentials()
    service = build("calendar", "v3", credentials=creds)
    # Исправлено: Используем TIMEZONE из config
    tz_str = str(TIMEZONE)
    event = {
        "summary": summary,
        "start": {"dateTime": start_time, "timeZone": tz_str},
        "end": {"dateTime": end_time, "timeZone": tz_str},
        "description": description,
    }
    if color_id:
        event["colorId"] = color_id
    created = service.events().insert(calendarId=calendar_id, body=event).execute()
    return created["id"]

# Исправлено: Используем TIMEZONE из config
def update_calendar_event(calendar_id: str, event_id: str, summary=None, color_id=None, description=None):
    """Обновляет событие в Google Календаре."""
    creds = get_google_credentials()
    service = build("calendar", "v3", credentials=creds)
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    # Исправлено: Используем TIMEZONE из config
    tz_str = str(TIMEZONE)
    if summary:
        event["summary"] = summary
    if color_id:
        event["colorId"] = color_id
    if description:
        event["description"] = description
    # Убедимся, что timeZone обновляется, если меняется время или при создании
    # Предполагаем, что start_time/end_time уже в нужном формате с TZ
    # if 'start' in event and 'timeZone' in event['start']:
    #     event['start']['timeZone'] = tz_str
    # if 'end' in event and 'timeZone' in event['end']:
    #     event['end']['timeZone'] = tz_str

    service.events().update(calendarId=calendar_id, eventId=event_id, body=event).execute()

def delete_calendar_event(calendar_id: str, event_id: str):
    """Удаляет событие из Google Календаря."""
    creds = get_google_credentials()
    service = build("calendar", "v3", credentials=creds)
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        # Исправлено: Используем logger вместо print
        logger.info(f"✅ Событие {event_id} удалено из календаря {calendar_id}.")
    except Exception as e:
        # Исправлено: Используем logger вместо print
        logger.error(f"❌ Ошибка при удалении события {event_id}: {e}")
        # Не вызываем исключение, чтобы не прерывать основной поток

# Исправлено: Используем logger вместо print
logger.info("✅ Модуль google.py загружен.")
