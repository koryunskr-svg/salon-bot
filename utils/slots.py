# utils/slots.py
import logging
from datetime import datetime, timedelta
from config import TIMEZONE, SHEET_ID, CALENDAR_ID
from .safe_google import (
    safe_get_sheet_data,
    safe_get_calendar_events
)
from .settings import get_setting

logger = logging.getLogger(__name__)

def find_available_slots(service_type: str, subservice: str, date_str: str, selected_master: str = None):
    """
    Находит доступные слоты на основе типа услуги, подуслуги, даты и (опционально) мастера.
    Возвращает список словарей с ключами: date, time, master.
    """
    logger.debug(f"🔍 Поиск доступных слотов: Тип={service_type}, Услуга={subservice}, Дата={date_str}, Мастер={selected_master}")
    
    available_slots = []
    try:
        target_date_obj = datetime.strptime(date_str, "%d.%m.%Y")
        target_date_iso = target_date_obj.date().isoformat()
        next_day_iso = (target_date_obj.date() + timedelta(days=1)).isoformat()
    except ValueError:
        logger.error(f"❌ Неверный формат даты для поиска слотов: {date_str}")
        return available_slots

    # 1. Получить длительность и буфер услуги → рассчитать шаг
    step_minutes = None
    services_data = safe_get_sheet_data(SHEET_ID, "Услуги!A2:G")
    for row in services_data:
        if len(row) >= 7 and row[0].strip() == service_type and row[1].strip() == subservice:
            try:
                duration = int(row[2]) if row[2] else 0  # [2] = Длительность
                buffer = int(row[3]) if row[3] else 0   # [3] = Буфер
                step_minutes = duration + buffer
                logger.debug(f"📏 Рассчитан шаг для {service_type}/{subservice}: {step_minutes} мин (длит. {duration} + буфер {buffer})")
                break
            except (ValueError, TypeError) as e:
                logger.error(f"❌ Ошибка парсинга Длительности/Буфера для {service_type}/{subservice}: {e}")
                continue
    if step_minutes is None:
        logger.error(f"❌ Не найдена услуга '{service_type}' - '{subservice}' в таблице 'Услуги'.")
        return available_slots

    # 2. Получить события из календаря на date_str
    time_min = f"{target_date_iso}T00:00:00"
    time_max = f"{next_day_iso}T00:00:00"
    try:
        existing_events = safe_get_calendar_events(CALENDAR_ID, time_min, time_max)
        logger.debug(f"📅 Получено {len(existing_events)} событий из календаря на {date_str}")
    except Exception as e:
        logger.error(f"❌ Ошибка при получении событий календаря для {date_str}: {e}")
        return available_slots

    # 3. Найти занятые слоты
    busy_slots = set()
    for event in existing_events:
        start = event["start"].get("dateTime")
        if start:
            try:
                dt = datetime.fromisoformat(start)
                if dt.tzinfo is None:
                    dt = TIMEZONE.localize(dt)
                else:
                    dt = dt.astimezone(TIMEZONE)
                summary = event.get("summary", "")
                description = event.get("description", "")
                master = "unknown"
                if " к " in summary:
                    parts = summary.split(" к ")
                    if len(parts) > 1:
                        master = parts[1].split()[0]
                elif " к " in description:
                    parts = description.split(" к ")
                    if len(parts) > 1:
                        master = parts[1].split()[0]
                busy_slots.add((dt, master))
                logger.debug(f"🔒 Занятый слот: {dt.strftime('%d.%m.%Y %H:%M')} у {master}")
            except (ValueError, Exception) as e:
                logger.warning(f"⚠️ Не удалось обработать событие календаря {event.get('id')}: {e}")

    # 4. Получить график мастеров на date_str
    masters_schedule_data = safe_get_sheet_data(SHEET_ID, "График мастеров!A2:H")
    day_name = target_date_obj.strftime("%a")
    short_day_map = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс"}
    target_short_day = short_day_map.get(day_name)
    if not target_short_day:
        logger.error(f"❌ Не удалось определить день недели для {date_str}")
        return available_slots

    masters_dict = {}
    org_name = get_setting("Название заведения", "").strip() or "Название организации"
    for row in masters_schedule_data:
        if len(row) >= 1:
            master_name = row[0].strip()
            if master_name and master_name != org_name:
                schedule = {}
                day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
                for i, day in enumerate(day_names):
                    col_index = i + 1
                    if col_index < len(row):
                        schedule[day] = row[col_index].strip()
                    else:
                        schedule[day] = "выходной"
                masters_dict[master_name] = schedule

    # 5. Найти доступные слоты
    for master_name, master_schedule in masters_dict.items():
        if selected_master and master_name != selected_master:
            continue
        work_time_str = master_schedule.get(target_short_day, "выходной")
        if work_time_str.lower().strip() == "выходной":
            logger.debug(f"🏖️ Мастер {master_name} не работает {date_str} ({target_short_day})")
            continue
        if "-" not in work_time_str:
            logger.warning(f"⚠️ Неверный формат времени у {master_name} на {date_str}: {work_time_str}")
            continue
        try:
            start_time_str, end_time_str = work_time_str.split("-", 1)
            work_start_dt = TIMEZONE.localize(datetime.strptime(f"{date_str} {start_time_str.strip()}", "%d.%m.%Y %H:%M"))
            work_end_dt = TIMEZONE.localize(datetime.strptime(f"{date_str} {end_time_str.strip()}", "%d.%m.%Y %H:%M"))
        except ValueError as e:
            logger.error(f"❌ Ошибка парсинга времени работы для {master_name} на {date_str}: {e}")
            continue

        current_dt = work_start_dt
        while current_dt + timedelta(minutes=step_minutes) <= work_end_dt:
            slot_end_dt = current_dt + timedelta(minutes=step_minutes)
            is_busy = False
            for busy_start_dt, busy_master in busy_slots:
                busy_end_dt = busy_start_dt + timedelta(minutes=step_minutes)
                latest_start = max(current_dt, busy_start_dt)
                earliest_end = min(slot_end_dt, busy_end_dt)
                if latest_start < earliest_end and (busy_master == master_name or busy_master == "unknown"):
                    is_busy = True
                    break
            if not is_busy:
                available_slots.append({
                    "date": current_dt.strftime("%d.%m.%Y"),
                    "time": current_dt.strftime("%H:%M"),
                    "master": master_name
                })
                logger.debug(f"✅ Найден доступный слот: {master_name}, {current_dt.strftime('%d.%m.%Y %H:%M')}")
            current_dt += timedelta(minutes=step_minutes)

    logger.info(f"✅ Поиск слотов завершён. Найдено {len(available_slots)} доступных слотов.")
    return available_slots

logger.info("✅ Модуль slots.py загружен.")
