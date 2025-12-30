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
    Возвращает список словарей с ключами: time, specialist.
    """
    logger.info(f"🔍 ПОИСК СЛОТОВ: Дата={date_str}, Специалист={selected_specialist}, Услуга={subservice}")
    
    if not date_str or not selected_specialist:
        logger.warning(f"⚠️ Пустые параметры: date_str='{date_str}', specialist='{selected_specialist}'")
        return []
    
    # === 1. ПОЛУЧАЕМ ГРАФИК РАБОТЫ СПЕЦИАЛИСТА ===
    from config import CALENDAR_ID, TIMEZONE, SHEET_ID
    import datetime
    
    # Определяем день недели
    try:
        search_date = datetime.datetime.strptime(date_str, "%d.%m.%Y")
        day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        day_of_week = day_names[search_date.weekday()]
        logger.info(f"📅 День недели для {date_str}: {day_of_week}")
    except Exception as e:
        logger.error(f"❌ Ошибка определения дня недели: {e}")
        return []
    
    # Получаем график специалиста
    schedule_data = safe_get_sheet_data(SHEET_ID, "График специалистов!A3:I") or []
    work_start = 10  # по умолчанию
    work_end = 20    # по умолчанию
    
    for row in schedule_data:
        if len(row) > 0 and row[0] == selected_specialist:
            day_index = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].index(day_of_week) + 2
            
            if day_index < len(row):
                schedule = row[day_index].strip()
                if schedule.lower() == "выходной":
                    logger.info(f"📅 {selected_specialist} не работает в {day_of_week}")
                    return []
                elif "-" in schedule:
                    try:
                        start_str, end_str = schedule.split("-")
                        work_start = int(start_str.split(":")[0])
                        work_end = int(end_str.split(":")[0])
                        logger.info(f"📅 График {selected_specialist}: {schedule} ({work_start}:00-{work_end}:00)")
                    except Exception as e:
                        logger.error(f"❌ Ошибка парсинга графика: {e}")
                break
    
    # === 2. ПОЛУЧАЕМ ДЛИТЕЛЬНОСТЬ УСЛУГИ ===
    service_duration = 60  # по умолчанию
    service_buffer = 0     # буфер
    services_data = safe_get_sheet_data(SHEET_ID, "Услуги!A3:G") or []
    for row in services_data:
        if len(row) > 1 and row[1] == subservice:
            try:
                service_duration = int(row[2]) if row[2] else 60  # колонка C - Длительность
                service_buffer = int(row[3]) if len(row) > 3 and row[3] else 0  # колонка D - Буфер
                logger.info(f"⏱️ Услуга '{subservice}': {service_duration} мин + буфер {service_buffer} мин")
            except Exception as e:
                logger.error(f"❌ Ошибка парсинга длительности услуги: {e}")
            break
    
    total_duration = service_duration + service_buffer
    
    # Функция округления до 15 минут
    def round_to_15(minutes):
        return ((minutes + 7) // 15) * 15
    
    # Округляем общую длительность до 15 минут
    total_duration = round_to_15(total_duration)
    logger.info(f"⏱️ Общая длительность с округлением: {total_duration} мин")

    # === 3. ПОЛУЧАЕМ ЗАНЯТЫЕ ИНТЕРВАЛЫ ===
    # Получаем из таблицы "Записи" (более надежно чем из календаря)
    busy_intervals = []  # список кортежей (начало, конец) в минутах от 00:00
    
    records = safe_get_sheet_data(SHEET_ID, "Записи!A3:O") or []
    for r in records:
        if len(r) > 7:
            record_date = str(r[6]).strip()
            record_specialist = str(r[5]).strip() if len(r) > 5 else ""
            record_status = str(r[8]).strip() if len(r) > 8 else ""
            record_time = str(r[7]).strip()
            
            if (record_date == date_str and 
                record_status == "подтверждено" and
                record_specialist == selected_specialist):
                
                try:
                    # Получаем время начала
                    start_dt = TIMEZONE.localize(
                        datetime.datetime.strptime(f"{record_date} {record_time}", "%d.%m.%Y %H:%M")
                    )
                    
                    # Получаем длительность этой записи
                    record_service = str(r[4]).strip() if len(r) > 4 else ""
                    record_duration = calculate_service_step(record_service)
                    
                    # Конец записи
                    end_dt = start_dt + datetime.timedelta(minutes=record_duration)
                    
                    # Конвертируем в минуты от начала дня
                    start_minutes = start_dt.hour * 60 + start_dt.minute
                    end_minutes = end_dt.hour * 60 + end_dt.minute
                    
                    busy_intervals.append((start_minutes, end_minutes))
                    logger.info(f"   🕒 Занято в таблице: {record_time}-{end_dt.strftime('%H:%M')} ({record_service}, {record_duration} мин)")
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка парсинга времени записи: {e}")
    
    logger.info(f"📅 Найдено занятых интервалов: {len(busy_intervals)}")
    
    # === 4. ГЕНЕРИРУЕМ СВОБОДНЫЕ СЛОТЫ ===
    available_slots = []
    slot_interval = 15  # минут между проверяемыми слотами
    
    # Перебираем все 15-минутные интервалы в рабочее время
    for hour in range(work_start, work_end):
        for minute in [0, 15, 30, 45]:  # ← ИЗМЕНИТЬ: каждые 15 минут!
            # Время начала слота в минутах
            slot_start_minutes = hour * 60 + minute
            slot_end_minutes = slot_start_minutes + total_duration
            
            # Проверяем, что слот не выходит за время работы
            slot_end_hour = slot_end_minutes // 60
            slot_end_minute = slot_end_minutes % 60
            
            # Если слот выходит за время работы - пропускаем
            if slot_end_hour > work_end or (slot_end_hour == work_end and slot_end_minute > 0):
                continue
            
            # Проверяем перекрытие с занятыми интервалами
            slot_overlaps = False
            for busy_start, busy_end in busy_intervals:
                # Если интервалы перекрываются
                if max(slot_start_minutes, busy_start) < min(slot_end_minutes, busy_end):
                    slot_overlaps = True
                    logger.debug(f"   ⚠️ Слот {hour:02d}:{minute:02d} перекрывается с занятым интервалом")
                    break
            
            if not slot_overlaps:
                time_str = f"{hour:02d}:{minute:02d}"
                available_slots.append({
                    "time": time_str,
                    "specialist": selected_specialist
                })
    
    logger.info(f"✅ Сгенерировано {len(available_slots)} свободных слотов для {selected_specialist} на {date_str}")
    
    # Детальное логирование слотов
    if available_slots:
        logger.info(f"   📋 ДОСТУПНЫЕ СЛОТЫ:")
        for slot in available_slots:
            time_str = slot['time']
            hour = int(time_str.split(':')[0])
            minute = int(time_str.split(':')[1])
            start_minutes = hour * 60 + minute
            end_minutes = start_minutes + total_duration
            
            end_hour = end_minutes // 60
            end_minute = end_minutes % 60
            
            logger.info(f"      🕒 {time_str}-{end_hour:02d}:{end_minute:02d} "
                       f"({total_duration} мин)")
    
    # Ограничиваем количество слотов (чтобы не перегружать интерфейс)
    if len(available_slots) > 10:
        available_slots = available_slots[:10]
    
    return available_slots


print("✅ Модуль slots.py загружен.")
