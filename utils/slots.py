# utils/slots.py
import logging
from datetime import datetime, timedelta
import pytz
from config import TIMEZONE, SHEET_ID, CALENDAR_ID
from .safe_google import (
    safe_get_sheet_data,
    safe_get_calendar_events,
    safe_create_calendar_event,
    safe_update_calendar_event,
    safe_delete_calendar_event
)
from .settings import get_setting # Импортируем для получения количества дней генерации

logger = logging.getLogger(__name__)

def generate_slots_for_n_days(days_ahead: int = None):
    """
    Генерирует слоты на N дней вперёд, начиная с *завтра*.
    Использует колонку 'Шаг' из листа 'Услуги' для интервала.
    """
    if days_ahead is None:
        # Загружаем из настроек, если не передано явно
        try:
            days_ahead = int(get_setting("Количество дней генерации слотов", "10"))
        except (ValueError, TypeError):
            logger.warning("⚠️ Не удалось получить 'Количество дней генерации слотов' из настроек, используем 10.")
            days_ahead = 10

    logger.info(f"🔄 Генерация слотов на {days_ahead} дней вперёд...")
    # Начинаем с *завтра*
    start_date = datetime.now(TIMEZONE).date() + timedelta(days=1)
    specialists_schedule = safe_get_sheet_data(SHEET_ID, "График специалистов!A3:I") # Читаем A-H для дней недели
    services = safe_get_sheet_data(SHEET_ID, "Услуги!A2:G") # Читаем A-G для Шага

    # Получаем уже существующие события на период генерации
    time_min = start_date.isoformat() + "T00:00:00"
    time_max = (start_date + timedelta(days=days_ahead + 1)).isoformat() + "T23:59:59"
    existing_events = safe_get_calendar_events(CALENDAR_ID, time_min, time_max)

    busy_slots = set()
    for event in existing_events:
        start = event["start"].get("dateTime")
        if start:
            dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            dt = dt.astimezone(TIMEZONE)
            date_str = dt.strftime("%d.%m.%Y")
            time_str = dt.strftime("%H:%M")
            # Описание может содержать информацию о специалисте
            description = event.get("description", "")
            # Пример: "Клиент: ..., тел.: ..." или "Бронь (в процессе) к Анна..."
            # Пытаемся извлечь имя специалиста из описания или использовать summary
            specialist = event.get("summary", "").split(" к ")[-1] if " к " in event.get("summary", "") else "unknown"
            if " к " in description:
                specialist = description.split(" к ")[-1].split(" ")[0] # Простое извлечение имени специалиста
            busy_slots.add((date_str, time_str, specialist))

    for days_offset in range(0, days_ahead):
        target_date = start_date + timedelta(days=days_offset)
        target_date_str = target_date.strftime("%d.%m.%Y")

        # Получаем день недели (Пн, Вт и т.д.)
        day_name = target_date.strftime("%a")
        short_day_map = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс"}
        target_short_day = short_day_map.get(day_name)

        if not target_short_day:
            logger.warning(f"⚠️ Не удалось определить день недели для {target_date_str}")
            continue

        for row in specialists_schedule:
            if len(row) < 8: # Убедимся, что в строке достаточно данных (A-H)
                continue
            specialist_name = row[0]
            if specialist_name == "Название организации": # Пропускаем строку с расписанием заведения
                continue

            # Получаем рабочее время из колонки для конкретного дня недели
            work_time_str = row[1:].get(target_short_day) # Псевдокод, нужно правильно индексировать
            # Индекс колонки: Пн=1, Вт=2, ..., Вс=7 (относительно A=0)
            day_col_index = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].index(target_short_day) + 1
            if day_col_index >= len(row):
                continue # Специалист не работает в этот день (нет данных в колонке)
            work_time_str = row[day_col_index]

            if work_time_str.lower().strip() == "выходной":
                continue # Специалист не работает в этот день

            # Предполагаем формат HH:MM-HH:MM
            if "-" not in work_time_str:
                logger.warning(f"⚠️ Неверный формат времени в графике специалиста {specialist_name} на {target_date_str}: {work_time_str}")
                continue

            start_time_str, end_time_str = work_time_str.split("-", 1)
            start_time_str = start_time_str.strip()
            end_time_str = end_time_str.strip()

            try:
                start_dt = TIMEZONE.localize(datetime.strptime(f"{target_date_str} {start_time_str}", "%d.%m.%Y %H:%M"))
                end_dt = TIMEZONE.localize(datetime.strptime(f"{target_date_str} {end_time_str}", "%d.%m.%Y %H:%M"))
            except ValueError as e:
                logger.error(f"❌ Ошибка парсинга времени для {specialist_name} на {target_date_str}: {e}")
                continue

            # Перебираем все услуги
            for service_row in services:
                if len(service_row) < 7: # Убедимся, что Шаг (F) доступен
                    continue

                # category, name, duration, buffer, step, price, description
                # Индексы: A=0, B=1, ..., F=5, G=6
                step_minutes = int(service_row[5]) # Колонка 'Шаг (мин)'

                current_dt = start_dt
                while current_dt + timedelta(minutes=step_minutes) <= end_dt:
                    date_str = current_dt.strftime("%d.%m.%Y")
                    time_str = current_dt.strftime("%H:%M")

                    # Проверяем, не занят ли слот
                    if (date_str, time_str, specialist_name) not in busy_slots:
                        event_summary = f"Свободно ({service_row[0]})" # Категория услуги в скобках
                        event_id = safe_create_calendar_event(
                            calendar_id=CALENDAR_ID,
                            summary=event_summary,
                            start_time=current_dt.isoformat(),
                            end_time=(current_dt + timedelta(minutes=step_minutes)).isoformat(),
                            color_id="11", # Серый
                            description=f"Свободный слот для {service_row[1]} у {specialist_name}" # Название услуги
                        )
                        logger.debug(f"📅 Сгенерирован слот: {specialist_name}, {date_str} {time_str}, {service_row[1]} (ID: {event_id})")
                    else:
                        logger.debug(f"⏳ Слот занят, пропускаем: {specialist_name}, {date_str} {time_str}")

                    current_dt += timedelta(minutes=step_minutes)

    logger.info(f"✅ Генерация слотов на {days_ahead} дней завершена.")
def find_available_slots(service_type: str, subservice: str, date_str: str = None, selected_specialist: str = None, priority: str = "date"):
    """
    Находит доступные слоты ДИНАМИЧЕСКИ на основе:
    1. Графика работы специалиста
    2. Уже занятых слотов в календаре (жёлтые/зелёные события)
    3. Длительности услуги (длительность + буфер)
    """
    logger.info(f"🔍 Динамический поиск слотов: {subservice} на {date_str}, специалист={selected_specialist}")
    
    # 1. Получаем длительность услуги
    services = safe_get_sheet_data(SHEET_ID, "Услуги!A3:G") or []
    service_duration = 60  # по умолчанию 60 минут
    
    for service in services:
        if len(service) > 1 and service[1] == subservice:
            try:
                duration = int(service[2]) if service[2] else 0
                buffer = int(service[3]) if service[3] else 0
                service_duration = duration + buffer
                logger.info(f"📏 Длительность услуги '{subservice}': {duration}+{buffer}={service_duration} мин")
                break
            except (ValueError, TypeError):
                continue
    
    if not date_str:
        logger.error("❌ Не указана дата")
        return []
    
    # 2. Преобразуем дату
    try:
        target_date = datetime.strptime(date_str, "%d.%m.%Y").date()
    except ValueError:
        logger.error(f"❌ Неверный формат даты: {date_str}")
        return []
    
    # 3. Получаем день недели
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    target_day_name = day_names[target_date.weekday()]
    
    # 4. Загружаем специалистов
    specialists_data = safe_get_sheet_data(SHEET_ID, "График специалистов!A3:I") or []
    
    # 5. Получаем ВСЕ события на эту дату из календаря
    start_of_day = TIMEZONE.localize(datetime.combine(target_date, datetime.min.time()))
    end_of_day = TIMEZONE.localize(datetime.combine(target_date, datetime.max.time()))
    
    all_events = safe_get_calendar_events(CALENDAR_ID, start_of_day.isoformat(), end_of_day.isoformat()) or []
    logger.info(f"📅 Найдено {len(all_events)} событий в календаре на {date_str}")
    
    # 6. Собираем занятые интервалы
    busy_intervals = []
    for event in all_events:
        try:
            start_time = event.get("start", {}).get("dateTime")
            end_time = event.get("end", {}).get("dateTime")
            
            if start_time and end_time:
                if start_time.endswith('Z'):
                    start_time = start_time[:-1] + "+00:00"
                if end_time.endswith('Z'):
                    end_time = end_time[:-1] + "+00:00"
                
                event_start = datetime.fromisoformat(start_time)
                event_end = datetime.fromisoformat(end_time)
                
                if event_start.tzinfo is None:
                    event_start = TIMEZONE.localize(event_start)
                else:
                    event_start = event_start.astimezone(TIMEZONE)
                
                if event_end.tzinfo is None:
                    event_end = TIMEZONE.localize(event_end)
                else:
                    event_end = event_end.astimezone(TIMEZONE)
                
                busy_intervals.append((event_start, event_end))
                
        except Exception:
            continue
    
    # 7. Ищем доступные слоты
    available_slots = []
    
    for row in specialists_data:
        if len(row) < 9:
            continue
            
        specialist_name = row[0].strip()
        
        # Фильтр по специалисту
        if selected_specialist and selected_specialist != "любой" and specialist_name != selected_specialist:
            continue
        
        # Проверяем категорию
        if len(row) > 1 and row[1]:
            specialist_categories = [cat.strip().lower() for cat in str(row[1]).split(",") if cat.strip()]
            if service_type.lower() not in specialist_categories:
                continue
        
        # Ищем расписание на нужный день
        # В таблице колонки: A=специалист, B=категория, C=Пн, D=Вт, E=Ср, F=Чт, G=Пт, H=Сб, I=Вс
        day_found = False
        schedule = ""
        
        # Маппинг дня недели на индекс колонки
        day_to_index = {
            "Пн": 2,  # колонка C
            "Вт": 3,  # колонка D  
            "Ср": 4,  # колонка E
            "Чт": 5,  # колонка F
            "Пт": 6,  # колонка G
            "Сб": 7,  # колонка H
            "Вс": 8   # колонка I
        }
        
        col_idx = day_to_index.get(target_day_name)
        
        if col_idx is not None and col_idx < len(row):
            schedule = str(row[col_idx]).strip()
            if schedule and schedule.lower() != "выходной":
                day_found = True
                logger.info(f"📅 Нашли расписание: {specialist_name} работает в {target_day_name}: {schedule}")
            else:
                logger.info(f"📅 {specialist_name} выходной в {target_day_name}")
        
        if not day_found or not schedule:
            continue
        
        # Парсим время работы
        try:
            start_work_str, end_work_str = schedule.split("-")
            start_work_str = start_work_str.strip()
            end_work_str = end_work_str.strip()
            
            start_work = TIMEZONE.localize(datetime.combine(
                target_date, 
                datetime.strptime(start_work_str, "%H:%M").time()
            ))
            end_work = TIMEZONE.localize(datetime.combine(
                target_date,
                datetime.strptime(end_work_str, "%H:%M").time()
            ))
            
            # Генерируем возможные времена начала с шагом 15 минут
            current_time = start_work
            
            while current_time + timedelta(minutes=service_duration) <= end_work:
                slot_start = current_time
                slot_end = current_time + timedelta(minutes=service_duration)
                
                # Проверяем пересечение с занятыми интервалами
                is_available = True
                
                for busy_start, busy_end in busy_intervals:
                    if not (slot_end <= busy_start or slot_start >= busy_end):
                        is_available = False
                        break
                
                if is_available:
                    available_slots.append({
                        "time": current_time.strftime("%H:%M"),
                        "specialist": specialist_name,
                        "date": date_str
                    })
                
                current_time += timedelta(minutes=15)
                
        except ValueError:
            continue
    
    # 8. Сортируем результат
    if priority == "date":
        available_slots.sort(key=lambda x: (x["time"], x["specialist"]))
    else:
        available_slots.sort(key=lambda x: (x["specialist"], x["time"]))
    
    logger.info(f"✅ Найдено {len(available_slots)} доступных слотов")
    return available_slots

print("✅ Модуль slots.py загружен.")

print("✅ Модуль slots.py загружен.")
