# utils/slots.py
from datetime import datetime, timedelta
import logging
from config import TIMEZONE, SHEET_ID, CALENDAR_ID
from .safe_google import safe_get_sheet_data, safe_get_calendar_events
from .settings import get_setting

logger = logging.getLogger(__name__)


def find_available_slots(service_type: str, subservice: str, date_str: str, selected_master: str = None, priority: str = "date"):
    """
    Находит доступные слоты на заданную дату для услуги/мастера.
    Работает динамически: не создаёт событий, не использует серый цвет.
    """
    try:
        step_minutes = calculate_service_step(subservice)
        if step_minutes <= 0:
            logger.warning(f"⚠️ Шаг услуги '{subservice}' <= 0: {step_minutes}")
            return []

        target_date = datetime.strptime(date_str, "%d.%m.%Y").date()
        dt_start_of_day = TIMEZONE.localize(datetime.combine(target_date, datetime.min.time()))
        dt_end_of_day = TIMEZONE.localize(datetime.combine(target_date, datetime.max.time()))

        masters_data = safe_get_sheet_data(SHEET_ID, "График мастеров!A3:H") or []
        org_name = get_setting("Название заведения", "").strip()
        available_masters = []
        weekday_index = target_date.weekday()
        day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        target_day = day_names[weekday_index]
        col_index = weekday_index + 1  # B=1 (Пн), ..., H=7 (Вс)

        for row in masters_data:
            if len(row) < 8:
                continue
            master_name = str(row[0]).strip()
            if master_name == org_name:
                continue  # пропускаем строку "Салон"
            if selected_master and master_name != selected_master:
                continue
            master_categories = str(row[1]).strip() if len(row) > 1 else ""
            if master_categories and service_type:
                cats = [cat.strip() for cat in master_categories.split(",")]
                if service_type not in cats:
                    continue

            if col_index < len(row):
                work_cell = str(row[col_index]).strip()
                if work_cell.lower() == "выходной":
                    continue
                if "-" not in work_cell:
                    continue

                try:
                    start_str, end_str = work_cell.split("-", 1)
                    start_time = datetime.strptime(start_str.strip(), "%H:%M").time()
                    end_time = datetime.strptime(end_str.strip(), "%H:%M").time()
                    dt_work_start = TIMEZONE.localize(datetime.combine(target_date, start_time))
                    dt_work_end = TIMEZONE.localize(datetime.combine(target_date, end_time))
                    dt_work_start = max(dt_work_start, dt_start_of_day)
                    dt_work_end = min(dt_work_end, dt_end_of_day)

                    if dt_work_start >= dt_work_end:
                        continue

                    available_masters.append({
                        "name": master_name,
                        "start": dt_work_start,
                        "end": dt_work_end
                    })
                except Exception as e:
                    logger.error(f"❌ Ошибка парсинга графика у {master_name}: {work_cell} → {e}")

        if not available_masters:
            logger.debug(f"🔍 Нет работающих мастеров на {date_str} для категории '{service_type}'.")
            return []

        # Получаем занятые интервалы (только жёлтые и зелёные)
        busy_intervals = []
        try:
            events = safe_get_calendar_events(
                calendar_id=CALENDAR_ID,
                time_min=dt_start_of_day.isoformat(),
                time_max=dt_end_of_day.isoformat()
            )
            for ev in events:
                if "start" in ev and "end" in ev:
                    start_dt = datetime.fromisoformat(ev["start"]["dateTime"].replace("Z", "+00:00")).astimezone(TIMEZONE)
                    end_dt = datetime.fromisoformat(ev["end"]["dateTime"].replace("Z", "+00:00")).astimezone(TIMEZONE)
                    color_id = ev.get("colorId")
                    if color_id in ("7", "10"):  # жёлтый, зелёный — занято
                        busy_intervals.append((start_dt, end_dt))
        except Exception as e:
            logger.error(f"❌ Ошибка получения событий календаря на {date_str}: {e}")

        # Строим слоты
        slots = []
        for master in available_masters:
            current = master["start"]
            while current + timedelta(minutes=step_minutes) <= master["end"]:
                slot_start = current
                slot_end = current + timedelta(minutes=step_minutes)

                is_busy = any(
                    slot_start < b_end and slot_end > b_start
                    for b_start, b_end in busy_intervals
                )

                if not is_busy:
                    slots.append({
                        "time": current.strftime("%H:%M"),
                        "master": master["name"]
                    })

                current += timedelta(minutes=15)

        slots.sort(key=lambda x: x["time"])
        logger.debug(f"✅ Найдено {len(slots)} свободных слотов на {date_str} для '{subservice}'")
        return slots

    except Exception as e:
        logger.error(f"❌ Ошибка в find_available_slots: {e}", exc_info=True)
        return []


def calculate_service_step(subservice: str) -> int:
    services = safe_get_sheet_data(SHEET_ID, "Услуги!A3:G") or []
    for row in services:
        if len(row) > 1 and row[1] == subservice:
            try:
                duration = int(row[2]) if row[2] else 0
                buffer = int(row[3]) if row[3] else 0
                return duration + buffer
            except (ValueError, TypeError):
                pass
    return int(get_setting("Дефолтный шаг услуги", "60"))


print("✅ Модуль slots.py загружен (без генерации слотов).")
