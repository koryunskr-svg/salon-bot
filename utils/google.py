Time' in event['start']:
        event['start']['timeZone'] = str(TIMEZONE)
    if 'end' in event and 'dateTime' in event['end']:
        event['end']['timeZone'] = str(TIMEZONE)
    service.events().update(calendarId=calendar_id, eventId=event_id, body=event).execute()

def delete_calendar_event(calendar_id: str, event_id: str):
    """Удаляет событие из Google Календаря."""
    creds = get_google_credentials()
    service = build("calendar", "v3", credentials=creds)
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        logger.info(f"✅ Событие {event_id} удалено из календаря {calendar_id}.")
    except Exception as e:
        logger.error(f"❌ Ошибка при удалении события {event_id}: {e}")
        # Не пробрасываем исключение — не критично для основного потока

logger.info("✅ Модуль google.py загружен.")
