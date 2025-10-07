# utils/google.py
import json
import os
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from config import SHEET_ID, CALENDAR_ID

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/calendar"
]

def get_google_credentials():
    creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise EnvironmentError("GOOGLE_CREDENTIALS_JSON не найден")
    creds_info = json.loads(creds_json)
    credentials = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return credentials

def get_sheet_data(spreadsheet_id: str, range_name: str):
    creds = get_google_credentials()
    service = build("sheets", "v4", credentials=creds)
    sheet = service.spreadsheets()
    result = sheet.values().get(spreadsheetId=spreadsheet_id, range=range_name).execute()
    return result.get("values", [])

def append_to_sheet(spreadsheet_id: str, sheet_name: str, row: list):
    creds = get_google_credentials()
    service = build("sheets", "v4", credentials=creds)
    body = {"values": [row]}
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=sheet_name,
        valueInputOption="RAW",
        body=body
    ).execute()

def update_sheet_row(spreadsheet_id: str, sheet_name: str, row_index: int, row: list):
    creds = get_google_credentials()
    service = build("sheets", "v4", credentials=creds)
    range_name = f"{sheet_name}!A{row_index}"
    body = {"values": [row]}
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueInputOption="RAW",
        body=body
    ).execute()

def get_calendar_events(calendar_id: str, time_min, time_max, query=None):
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

def create_calendar_event(calendar_id: str, summary: str, start_time: str, end_time: str, color_id=None, description=None):
    creds = get_google_credentials()
    service = build("calendar", "v3", credentials=creds)
    event = {
        "summary": summary,
        "start": {"dateTime": start_time, "timeZone": "Europe/Moscow"},
        "end": {"dateTime": end_time, "timeZone": "Europe/Moscow"},
        "description": description,
    }
    if color_id:
        event["colorId"] = color_id
    created = service.events().insert(calendarId=calendar_id, body=event).execute()
    return created["id"]

def update_calendar_event(calendar_id: str, event_id: str, summary=None, color_id=None, description=None):
    creds = get_google_credentials()
    service = build("calendar", "v3", credentials=creds)
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    if summary:
        event["summary"] = summary
    if color_id:
        event["colorId"] = color_id
    if description:
        event["description"] = description
    service.events().update(calendarId=calendar_id, eventId=event_id, body=event).execute()

def delete_calendar_event(calendar_id: str, event_id: str):
    creds = get_google_credentials()
    service = build("calendar", "v3", credentials=creds)
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    except Exception as e:
        print(f"Ошибка при удалении события: {e}")
