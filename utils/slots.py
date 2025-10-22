# utils/slots.py
from datetime import datetime, timedelta
import pytz
from config import TIMEZONE, SHEET_ID, CALENDAR_ID
from utils.google import get_sheet_data, get_calendar_events

def generate_slots_for_10_days(context=None):
    today = datetime.now(TIMEZONE).date()
    masters_schedule = get_sheet_data(SHEET_ID, "График мастеров!A2:E")
    services = get_sheet_data(SHEET_ID, "Услуги!A2:E")
    time_min = (datetime.now(TIMEZONE) - timedelta(hours=1)).isoformat()
    time_max = (datetime.now(TIMEZONE) + timedelta(days=11)).isoformat()
    existing_events = get_calendar_events(CALENDAR_ID, time_min, time_max)
    busy_slots = set()
    for event in existing_events:
        start = event["start"].get("dateTime")
        if start:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            dt = dt.astimezone(TIMEZONE)
            date_str = dt.strftime("%d.%m.%Y")
            time_str = dt.strftime("%H:%M")
            master = event.get("description", "").split("к ")[-1].strip()
            busy_slots.add((date_str, time_str, master))
    for days_ahead in range(1, 11):
        target_date = today + timedelta(days=days_ahead)
        target_date_str = target_date.strftime("%d.%m.%Y")
        for row in masters_schedule:
            if len(row) < 5:
                continue
            master_name = row[0]
            work_days = row[1].split(", ")
            start_time_str = row[2]
            end_time_str = row[3]
            day_name = target_date.strftime("%a")
            short_day = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс"}.get(day_name)
            if short_day not in work_days:
                continue
            start_dt = TIMEZONE.localize(datetime.strptime(f"{target_date_str} {start_time_str}", "%d.%m.%Y %H:%M"))
            end_dt = TIMEZONE.localize(datetime.strptime(f"{target_date_str} {end_time_str}", "%d.%m.%Y %H:%M"))
            for service_row in services:
                if len(service_row) < 5:
                    continue
                service_type, subservice, duration, buffer, step = service_row[0], service_row[1], int(service_row[2]), int(service_row[3]), int(service_row[4])
                step_minutes = step
                current_dt = start_dt
                while current_dt + timedelta(minutes=step_minutes) <= end_dt:
                    date_str = current_dt.strftime("%d.%m.%Y")
                    time_str = current_dt.strftime("%H:%M")
                    if (date_str, time_str, master_name) not in busy_slots:
                        from utils.google import create_calendar_event
                        create_calendar_event(
                            calendar_id=CALENDAR_ID,
                            summary="Свободно",
                            start_time=current_dt.isoformat(),
                            end_time=(current_dt + timedelta(minutes=step_minutes)).isoformat(),
                            color_id="11",
                            description=f"Свободный слот для {service_type}"
                        )
                    current_dt += timedelta(minutes=step_minutes)

def find_available_slots(service_type=None, subservice=None, date=None, selected_master=None, priority=None):
    """
    Возвращает список доступных слотов в формате:
    [{"date": "01.01.2025", "time": "10:00", "master": "Анна"}, ...]
    """
    time_min = datetime.now(TIMEZONE).isoformat()
    time_max = (datetime.now(TIMEZONE) + timedelta(days=11)).isoformat()
    events = get_calendar_events(CALENDAR_ID, time_min, time_max)
    available = []

    for event in events:
        if event.get("summary") != "Свободно":
            continue
        start = event.get("start", {}).get("dateTime")
        if not start:
            continue
        dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(TIMEZONE)
        date_str = dt.strftime("%d.%m.%Y")
        time_str = dt.strftime("%H:%M")
        desc = event.get("description", "")
        # Извлекаем тип услуги из описания
        if f"для {service_type}" not in desc and service_type:
            continue
        # Извлекаем мастера из описания (если нужно)
        master = "Любой"
        if "к " in desc:
            master = desc.split("к ")[-1].strip()

        if selected_master and selected_master != "any" and master != selected_master:
            continue
        if date and date_str != date:
            continue

        available.append({
            "date": date_str,
            "time": time_str,
            "master": master
        })

    # Сортировка
    available.sort(key=lambda x: (x["date"], x["time"]))
    return available
