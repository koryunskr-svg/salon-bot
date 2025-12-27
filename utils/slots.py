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
    specialists_schedule = safe_get_sheet_data(SHEET_ID, "График специалистов!A3:I") # Читаем A-I для дней недели
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
    logger.info(f"🔍 ПОИСК СЛОТОВ: Дата={date_str}, Специалист={selected_specialist}, Услуга={subservice}")
    
    # ВРЕМЕННОЕ РЕШЕНИЕ: генерируем тестовые слоты
    if not date_str or not selected_specialist:
        logger.warning(f"⚠️ Пустые параметры: date_str='{date_str}', specialist='{selected_specialist}'")
        return []
    
    # === 1. ПОЛУЧАЕМ ЗАНЯТЫЕ СЛОТЫ ИЗ КАЛЕНДАРЯ ===
    from config import CALENDAR_ID, TIMEZONE
    import datetime
    
    busy_slots = []
    try:
        # Конвертируем дату для поиска в календаре
        search_date = datetime.datetime.strptime(date_str, "%d.%m.%Y")
        search_date = TIMEZONE.localize(search_date)
        
        # Начало и конец дня для поиска
        time_min = search_date.replace(hour=0, minute=0, second=0).isoformat()
        time_max = search_date.replace(hour=23, minute=59, second=59).isoformat()
        
        logger.info(f"📅 Ищу события в календаре: {time_min} - {time_max}")
        
        # Получаем события календаря
        busy_events = safe_get_calendar_events(CALENDAR_ID, time_min, time_max) or []
        logger.info(f"📅 Найдено событий в календаре: {len(busy_events)}")
        
        # Фильтруем события данного специалиста
        specialist_events = 0
        for event in busy_events:
            event_summary = event.get('summary', '')
            event_start = event.get('start', {}).get('dateTime')
            
            # Проверяем, относится ли событие к этому специалисту
            if event_start and selected_specialist in event_summary:
                specialist_events += 1
                try:
                    # Извлекаем время из события
                    event_dt = datetime.datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                    event_dt = event_dt.astimezone(TIMEZONE)
                    busy_time = event_dt.strftime("%H:%M")
                    busy_slots.append(busy_time)
                    logger.info(f"   🕒 Занято: {busy_time} - {event_summary}")
                except Exception as e:
                    logger.error(f"❌ Ошибка парсинга времени события: {e}")
        
        logger.info(f"📅 Для {selected_specialist} на {date_str} занято слотов: {len(busy_slots)} из {specialist_events} событий")
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения данных календаря: {e}")
        busy_slots = []

    # === 2. ГЕНЕРИРУЕМ СВОБОДНЫЕ СЛОТЫ ===
    test_slots = []
    for hour in range(10, 20):  # С 10:00 до 20:00
        for minute in [0, 30]:
            time_str = f"{hour:02d}:{minute:02d}"
            
            # Пропускаем занятые слоты
            if time_str in busy_slots:
                logger.debug(f"   ⏸️ Пропускаем занятый слот: {time_str}")
                continue
                
            test_slots.append({
                "date": date_str,
                "time": time_str,
                "specialist": selected_specialist
            })
    
    logger.info(f"✅ Сгенерировано {len(test_slots)} свободных слотов для {selected_specialist} на {date_str}")
    logger.info(f"   Занятые слоты: {busy_slots}")
    logger.info(f"   Свободные слоты: {[s['time'] for s in test_slots]}")
    
    return test_slots

print("✅ Модуль slots.py загружен.")
