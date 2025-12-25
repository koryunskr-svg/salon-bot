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
    Находит доступные слоты на основе типа услуги, подуслуги, даты, специалиста и приоритета.
    Возвращает список словарей с ключами: date, time, specialist.
    """
   
    logger.info(f"🔍 Поиск слотов: Тип={service_type}, Услуга={subservice}, Дата={date_str}, специалист={selected_specialist}")

    # 1. Получаем шаг (длительность + буфер) для услуги
    services = safe_get_sheet_data(SHEET_ID, "Услуги!A3:G") or []
    step_minutes = 60  # значение по умолчанию
    
    for service in services:
        if len(service) > 1 and service[1] == subservice:
            try:
                duration = int(service[2]) if service[2] else 0
                buffer = int(service[3]) if service[3] else 0
                step_minutes = duration + buffer
                break
            except (ValueError, TypeError):
                continue
    
    logger.info(f"📏 Шаг для услуги '{subservice}': {step_minutes} мин")
    
    if not date_str:
        logger.error("❌ Не указана дата для поиска слотов")
        return []
    
    # 2. Преобразуем дату
    try:
        target_date = datetime.strptime(date_str, "%d.%m.%Y").date()
    except ValueError:
        logger.error(f"❌ Неверный формат даты: {date_str}")
        return []
    
    # 3. Получаем день недели
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    day_index = target_date.weekday()
    target_day_name = day_names[day_index]
    
    # 4. Загружаем график специалистов
    specialists_data = safe_get_sheet_data(SHEET_ID, "График специалистов!A3:I") or []
    
    # 5. Получаем занятые слоты из календаря
    time_min = TIMEZONE.localize(datetime.combine(target_date, datetime.min.time())).isoformat()
    time_max = TIMEZONE.localize(datetime.combine(target_date, datetime.max.time())).isoformat()
    
    events = safe_get_calendar_events(CALENDAR_ID, time_min, time_max) or []
    busy_slots = {}
    
    for event in events:
        start = event.get("start", {}).get("dateTime")
        if start:
            try:
                # Убираем 'Z' и парсим время
                if start.endswith('Z'):
                    start = start[:-1] + "+00:00"
                
                event_dt = datetime.fromisoformat(start)
                if event_dt.tzinfo is None:
                    event_dt = TIMEZONE.localize(event_dt)
                else:
                    event_dt = event_dt.astimezone(TIMEZONE)
                
                # Извлекаем специалиста из summary или description
                summary = event.get("summary", "")
                specialist = "неизвестно"
                
                # Пытаемся найти специалиста в summary
                if "к " in summary:
                    specialist = summary.split("к ")[-1].strip()
                else:
                    # Пробуем в описании
                    description = event.get("description", "")
                    if "к " in description:
                        specialist = description.split("к ")[-1].split()[0].strip()
                
                # Добавляем в занятые слоты
                time_key = event_dt.strftime("%H:%M")
                if specialist not in busy_slots:
                    busy_slots[specialist] = set()
                busy_slots[specialist].add(time_key)
                
            except Exception as e:
                logger.warning(f"⚠️ Ошибка парсинга события: {e}")
    
    # 6. Находим доступные слоты для каждого специалиста
    available_slots = []
    
    for row in specialists_data:
        if len(row) < 9:  # Нужны колонки A-I
            continue
            
        specialist_name = row[0].strip()
        
        # Фильтруем по выбранному специалисту
        if selected_specialist and selected_specialist != "любой" and specialist_name != selected_specialist:
            continue
        
        # Проверяем категорию специалиста (колонка B)
        if len(row) > 1:
            specialist_categories = [cat.strip().lower() for cat in str(row[1]).split(",") if cat.strip()]
            if service_type.lower() not in specialist_categories:
                continue
        
        # Получаем расписание на нужный день
        day_col_index = 2 + day_index  # A=0, B=1, C=2 (Пн), D=3 (Вт)...
        if day_col_index >= len(row):
            continue
            
        schedule = str(row[day_col_index]).strip()
        
        if schedule.lower() == "выходной" or not schedule:
            continue
        
        # Парсим время работы
        if "-" not in schedule:
            logger.warning(f"⚠️ Неверный формат расписания у {specialist_name}: {schedule}")
            continue
            
        start_work_str, end_work_str = schedule.split("-")
        start_work_str = start_work_str.strip()
        end_work_str = end_work_str.strip()
        
        try:
            start_dt = TIMEZONE.localize(datetime.combine(target_date, datetime.strptime(start_work_str, "%H:%M").time()))
            end_dt = TIMEZONE.localize(datetime.combine(target_date, datetime.strptime(end_work_str, "%H:%M").time()))
        except ValueError as e:
            logger.error(f"❌ Ошибка парсинга времени у {specialist_name}: {e}")
            continue
        
        # Генерируем слоты
        current_dt = start_dt
        
        while current_dt + timedelta(minutes=step_minutes) <= end_dt:
            slot_time_str = current_dt.strftime("%H:%M")
            
            # Проверяем, свободен ли слот
            is_busy = False
            if specialist_name in busy_slots and slot_time_str in busy_slots[specialist_name]:
                is_busy = True
            
            # Также проверяем общие события (без указания специалиста)
            if "неизвестно" in busy_slots and slot_time_str in busy_slots["неизвестно"]:
                is_busy = True
            
            if not is_busy:
                available_slots.append({
                    "time": slot_time_str,
                    "specialist": specialist_name,
                    "date": date_str
                })
                logger.debug(f"✅ Найден слот: {specialist_name} в {slot_time_str}")
            
            current_dt += timedelta(minutes=step_minutes)
    
    # 7. Сортируем слоты
    if priority == "date":
        # Сначала по времени, потом по специалисту
        available_slots.sort(key=lambda x: (x["time"], x["specialist"]))
    else:  # priority == "specialist"
        # Сначала по специалисту, потом по времени
        available_slots.sort(key=lambda x: (x["specialist"], x["time"]))
    
    logger.info(f"✅ Найдено {len(available_slots)} доступных слотов")
    return available_slots

print("✅ Модуль slots.py загружен.")
