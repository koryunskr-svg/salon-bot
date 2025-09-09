# utils/slots.py
from datetime import datetime, timedelta
import pytz
from config import TIMEZONE
from utils.google import get_sheet_data, get_calendar_events, create_calendar_event

def generate_slots_for_10_days(context=None):
    today = datetime.now(TIMEZONE).date()
    masters_schedule = get_sheet_data("SHEET_ID", "График мастеров!A2:E")
    services = get_sheet_data("SHEET_ID", "Услуги!A2:E")

    time_min = (datetime.now(TIMEZONE) - timedelta(hours=1)).isoformat()
    time_max = (datetime.now(TIMEZONE) + timedelta(days=11)).isoformat()
    existing_events = get_calendar_events("CALENDAR_ID", time_min, time_max)

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
                service_type, subservice, duration, buffer, step = service_row
                step_minutes = int(step)

                current_dt = start_dt
                while current_dt + timedelta(minutes=step_minutes) <= end_dt:
                    date_str = current_dt.strftime("%d.%m.%Y")
                    time_str = current_dt.strftime("%H:%M")
                    if (date_str, time_str, master_name) not in busy_slots:
                        create_calendar_event(
                            calendar_id="CALENDAR_ID",
                            summary="Свободно",
                            start_time=current_dt.isoformat(),
                            end_time=(current_dt + timedelta(minutes=step_minutes)).isoformat(),
                            color_id="11",
                            description=f"Свободный слот для {service_type}"
                        )
                    current_dt += timedelta(minutes=step_minutes)