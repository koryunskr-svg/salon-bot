# utils/slots.py
import logging
logger = logging.getLogger(__name__)
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
    Возвращает список словарей с ключами: time, specialist, available_specialists (для "Любой").
    """
    logger.info(f"🎯 ПОИСК СЛОТОВ: Дата={date_str}, Специалист={selected_specialist}, Услуга={subservice} ({service_type})")
    
    # === МЕГА-ОТЛАДКА: проверяем входные параметры ===
    logger.info(f"=== ВХОДНЫЕ ПАРАМЕТРЫ find_available_slots ===")
    logger.info(f"  1. service_type: '{service_type}'")
    logger.info(f"  2. subservice: '{subservice}'")
    logger.info(f"  3. date_str: '{date_str}'")
    logger.info(f"  4. selected_specialist: '{selected_specialist}'")
    logger.info(f"  5. priority: '{priority}'")
    logger.info(f"  6. TIMEZONE: {TIMEZONE} (type: {type(TIMEZONE)})")
    
    if not date_str:
        logger.error("❌ date_str пустая!")
        return []
    
    if not selected_specialist:
        logger.warning("⚠️ selected_specialist пустой, но продолжаем...")
    
    # === 1. ПОЛУЧАЕМ ГРАФИК РАБОТЫ СПЕЦИАЛИСТА ===
    # УБЕРИТЕ: from config import CALENDAR_ID, TIMEZONE, SHEET_ID (уже импортировано)
    import datetime as dt_module  # для избежания конфликта имен
    
    # Определяем день недели
    try:
        search_date = dt_module.datetime.strptime(date_str, "%d.%m.%Y")
        day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        day_of_week = day_names[search_date.weekday()]
        logger.info(f"День недели для {date_str}: {day_of_week}")
    except Exception as e:
        logger.error(f"Ошибка определения дня недели: {e}")
        return []
    
    # === ОСОБЫЙ СЛУЧАЙ: "ЛЮБОЙ" СПЕЦИАЛИСТ ===
    is_any_mode = False
    all_specialists_in_category = []
    
    if selected_specialist and selected_specialist.lower() in ["любой", "любой специалист"]:
        is_any_mode = True
        logger.info(f"🔍 РЕЖИМ 'ЛЮБОЙ': ищем всех специалистов категории '{service_type}'")
        
        # 1. Находим всех специалистов этой категории
        schedule_data = safe_get_sheet_data(SHEET_ID, "График специалистов!A3:I") or []
        for row in schedule_data:
            if len(row) > 1 and row[0] and row[0].strip():
                spec_name = row[0].strip()
                spec_categories = row[1].strip().lower() if len(row) > 1 else ""
                
                if service_type.lower() in spec_categories:
                    # Проверяем, работает ли в этот день
                    day_index = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].index(day_of_week) + 2
                    if day_index < len(row) and row[day_index].strip().lower() != "выходной":
                        all_specialists_in_category.append(spec_name)
                        logger.info(f"  ✓ {spec_name} работает в {day_of_week}")
        
        if not all_specialists_in_category:
            logger.error(f"❌ Нет работающих специалистов категории '{service_type}' на {date_str}")
            return []
        
        logger.info(f"📋 Все специалисты категории: {all_specialists_in_category}")
    
    # Получаем график специалиста (если не "Любой")
    schedule_data = safe_get_sheet_data(SHEET_ID, "График специалистов!A3:I") or []
    work_intervals = []  # список интервалов в минутах [(start_minutes, end_minutes), ...]
    
    if not is_any_mode:
        # Существующая логика для конкретного специалиста
        for row in schedule_data:
            if len(row) > 0 and row[0] == selected_specialist:
                day_index = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].index(day_of_week) + 2
                
                if day_index < len(row):
                    schedule = row[day_index].strip()
                    if schedule.lower() == "выходной":
                        logger.info(f"{selected_specialist} не работает в {day_of_week}")
                        return []
                    elif "-" in schedule:
                        try:
                            if "," in schedule:
                                interval_strings = schedule.split(",")
                                for interval_str in interval_strings:
                                    interval_str = interval_str.strip()
                                    if "-" in interval_str:
                                        start_str, end_str = interval_str.split("-")
                                        start_hour = int(start_str.split(":")[0])
                                        start_minute = int(start_str.split(":")[1]) if ":" in start_str and len(start_str.split(":")) > 1 else 0
                                        end_hour = int(end_str.split(":")[0])
                                        end_minute = int(end_str.split(":")[1]) if ":" in end_str and len(end_str.split(":")) > 1 else 0
                                        
                                        start_in_minutes = start_hour * 60 + start_minute
                                        end_in_minutes = end_hour * 60 + end_minute
                                        
                                        work_intervals.append((start_in_minutes, end_in_minutes))
                                        logger.info(f"  Интервал в минутах: {start_str}-{end_str} ({start_in_minutes}-{end_in_minutes} мин)")
                            else:
                                start_str, end_str = schedule.split("-")
                                start_hour = int(start_str.split(":")[0])
                                start_minute = int(start_str.split(":")[1]) if ":" in start_str and len(start_str.split(":")) > 1 else 0
                                end_hour = int(end_str.split(":")[0])
                                end_minute = int(end_str.split(":")[1]) if ":" in end_str and len(end_str.split(":")) > 1 else 0
                                
                                start_in_minutes = start_hour * 60 + start_minute
                                end_in_minutes = end_hour * 60 + end_minute
                                
                                work_intervals.append((start_in_minutes, end_in_minutes))
                                logger.info(f"  Интервал в минутах: {start_str}-{end_str} ({start_in_minutes}-{end_in_minutes} мин)")
                            
                            logger.info(f"График {selected_specialist}: {schedule} (интервалы в минутах: {work_intervals})")
                        except Exception as e:
                            logger.error(f"Ошибка парсинга графика: {e}")
                            work_intervals = [(10*60, 20*60)]
                break
    
    # Для "Любой" будем обрабатывать каждого специалиста отдельно
    if is_any_mode:
        # Рабочие интервалы для "Любой" будут обрабатываться позже
        pass
    elif not work_intervals:
        work_intervals = [(10*60, 20*60)]
    
    # Получаем текущее время
    now = dt_module.datetime.now(TIMEZONE)
    
    # Проверяем, не прошла ли выбранная дата
    try:
        selected_date = dt_module.datetime.strptime(date_str, "%d.%m.%Y").date()
        if selected_date < now.date():
            logger.info(f"⏰ Выбрана прошедшая дата: {date_str}, сегодня: {now.date()}")
            return []
    except Exception as e:
        logger.error(f"Ошибка проверки даты: {e}")
    
    # === 2. ПОЛУЧАЕМ ДЛИТЕЛЬНОСТЬ УСЛУГИ ===
    service_duration = 60
    service_buffer = 0
    services_data = safe_get_sheet_data(SHEET_ID, "Услуги!A3:G") or []
    for row in services_data:
        if len(row) > 1 and row[1] == subservice:
            try:
                service_duration = int(row[2]) if row[2] else 60
                service_buffer = int(row[3]) if len(row) > 3 and row[3] else 0
                logger.info(f"Услуга '{subservice}': {service_duration} мин + буфер {service_buffer} мин")
            except Exception as e:
                logger.error(f"Ошибка парсинга длительности услуги: {e}")
            break
    
    total_duration = service_duration + service_buffer
    
    def round_to_15(minutes):
        return ((minutes + 7) // 15) * 15
    
    total_duration = round_to_15(total_duration)
    logger.info(f"Общая длительность с округлением: {total_duration} мин")
    
    if total_duration > 240:
        logger.info(f"⚠️ Очень длинная услуга ({total_duration} мин) - требуется согласование")
        return [{
            "time": "Требуется согласование",
            "specialist": f"{selected_specialist} (длинная услуга)",
            "long_service": True
        }]
    
    # === 3. ПОЛУЧАЕМ ЗАНЯТЫЕ ИНТЕРВАЛЫ ===
    busy_intervals_by_specialist = {}
    
    records = safe_get_sheet_data(SHEET_ID, "Записи!A3:O") or []
    
    if is_any_mode:
        logger.info(f"=== DEBUG SLOTS: Ищу занятые слоты для ВСЕХ специалистов на {date_str} ===")
        target_specialists = all_specialists_in_category
    else:
        logger.info(f"=== DEBUG SLOTS: Ищу занятые слоты для {selected_specialist} на {date_str} ===")
        target_specialists = [selected_specialist]
    
    for idx, r in enumerate(records, start=3):
        if len(r) > 7:
            record_date = str(r[6]).strip()
            record_specialist = str(r[5]).strip() if len(r) > 5 else ""
            record_status = str(r[8]).strip() if len(r) > 8 else ""
            record_time = str(r[7]).strip()
            
            if (record_date == date_str and 
                record_status == "подтверждено" and
                record_specialist in target_specialists):
             
                logger.info(f"Запись {idx}: дата='{record_date}', спец='{record_specialist}', время='{record_time}' ✓ ПОДХОДИТ!")
                
                try:
                    if "-" in record_time:
                        start_time_str = record_time.split("-")[0].strip()
                    else:
                        start_time_str = record_time

                    logger.info(f"  ОТЛАДКА: Парсим '{record_date} {start_time_str}'")
                    
                    naive_datetime = dt_module.datetime.strptime(f"{record_date} {start_time_str}", "%d.%m.%Y %H:%M")
                    
                    if naive_datetime.tzinfo is None:
                        start_dt = TIMEZONE.localize(naive_datetime)
                    else:
                        start_dt = naive_datetime
                    
                    record_service = str(r[4]).strip() if len(r) > 4 else ""
                    record_service_duration = 60
                    
                    for svc_row in services_data:
                        if len(svc_row) > 1 and svc_row[1] == record_service:
                            try:
                                base_duration = int(svc_row[2]) if svc_row[2] else 60
                                buffer_duration = int(svc_row[3]) if len(svc_row) > 3 and svc_row[3] else 0
                                record_service_duration = base_duration + buffer_duration
                                break
                            except (ValueError, TypeError):
                                pass
                    
                    end_dt = start_dt + dt_module.timedelta(minutes=record_service_duration)
                    
                    start_minutes = start_dt.hour * 60 + start_dt.minute
                    end_minutes = end_dt.hour * 60 + end_dt.minute
                    
                    if record_specialist not in busy_intervals_by_specialist:
                        busy_intervals_by_specialist[record_specialist] = []
                    busy_intervals_by_specialist[record_specialist].append((start_minutes, end_minutes))
                    
                    logger.info(f"   Занято: {start_time_str}-{end_dt.strftime('%H:%M')} ({record_service}, {record_service_duration} мин)")
                    
                except Exception as e:
                    logger.error(f"Ошибка обработки записи {idx}: {e}")
    
    logger.info(f"=== DEBUG SLOTS: Найдено занятых интервалов ===")
    
    # === 4. ГЕНЕРИРУЕМ СВОБОДНЫЕ СЛОТЫ ===
    available_slots = []
    
    if is_any_mode:
        # === РЕЖИМ "ЛЮБОЙ": собираем слоты по времени ===
        time_to_specialists = {}
        
        # Вспомогательная функция для получения интервалов работы
        def get_spec_intervals(spec_name):
            for row in schedule_data:
                if len(row) > 0 and row[0] == spec_name:
                    day_index = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].index(day_of_week) + 2
                    if day_index < len(row):
                        schedule = row[day_index].strip()
                        if schedule.lower() == "выходной":
                            return []
                        elif "-" in schedule:
                            intervals = []
                            if "," in schedule:
                                parts = schedule.split(",")
                                for part in parts:
                                    part = part.strip()
                                    if "-" in part:
                                        start_str, end_str = part.split("-")
                                        start_hour = int(start_str.split(":")[0])
                                        start_minute = int(start_str.split(":")[1]) if ":" in start_str and len(start_str.split(":")) > 1 else 0
                                        end_hour = int(end_str.split(":")[0])
                                        end_minute = int(end_str.split(":")[1]) if ":" in end_str and len(end_str.split(":")) > 1 else 0
                                        
                                        start_in_minutes = start_hour * 60 + start_minute
                                        end_in_minutes = end_hour * 60 + end_minute
                                        intervals.append((start_in_minutes, end_in_minutes))
                            else:
                                start_str, end_str = schedule.split("-")
                                start_hour = int(start_str.split(":")[0])
                                start_minute = int(start_str.split(":")[1]) if ":" in start_str and len(start_str.split(":")) > 1 else 0
                                end_hour = int(end_str.split(":")[0])
                                end_minute = int(end_str.split(":")[1]) if ":" in end_str and len(end_str.split(":")) > 1 else 0
                                
                                start_in_minutes = start_hour * 60 + start_minute
                                end_in_minutes = end_hour * 60 + end_minute
                                intervals.append((start_in_minutes, end_in_minutes))
                            return intervals
            return [(10*60, 20*60)]
        
        # Для каждого специалиста
        for spec in all_specialists_in_category:
            spec_intervals = get_spec_intervals(spec)
            spec_busy = busy_intervals_by_specialist.get(spec, [])
            
            for interval_start, interval_end in spec_intervals:
                current_minutes = interval_start
                while current_minutes + total_duration <= interval_end:
                    
                    # Проверяем, не прошло ли время
                    hour = current_minutes // 60
                    minute = current_minutes % 60
                    time_str = f"{hour:02d}:{minute:02d}"
                    
                    try:
                        slot_dt = TIMEZONE.localize(
                            dt_module.datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
                        )
                        if slot_dt < now:
                            current_minutes += 15
                            continue
                    except Exception:
                        pass
                    
                    slot_start = current_minutes
                    slot_end = current_minutes + total_duration
                    
                    # Проверяем занятость
                    slot_free = True
                    for busy_start, busy_end in spec_busy:
                        if not (slot_end <= busy_start or slot_start >= busy_end):
                            slot_free = False
                            break
                    
                    if slot_free:
                        if time_str not in time_to_specialists:
                            time_to_specialists[time_str] = []
                        time_to_specialists[time_str].append(spec)
                    
                    current_minutes += 15
        
        # Преобразуем в формат для бота
        for time_str, specialists in sorted(time_to_specialists.items()):
            # Фильтруем "Любой" из списка специалистов
            real_specialists = [spec for spec in specialists if spec.lower() not in ["любой", "любой специалист"]]
            real_count = len(real_specialists)
            available_slots.append({
                "time": time_str,
                "specialist": "любой",
                "available_specialists": real_specialists,
                "available_count": real_count,
                "is_any_mode": True
            })
        
        logger.info(f"🕒 Для 'Любой' найдено {len(available_slots)} временных слотов")
        
    else:
        # === ОБЫЧНЫЙ РЕЖИМ ===
        logger.info(f"Генерация слотов по интервалам: {work_intervals}")
        
        for interval_start, interval_end in work_intervals:
            current_minutes = interval_start
            while current_minutes + total_duration <= interval_end:
                
                hour = current_minutes // 60
                minute = current_minutes % 60
                time_str = f"{hour:02d}:{minute:02d}"
                
                try:
                    slot_dt = TIMEZONE.localize(
                        dt_module.datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
                    )
                    if slot_dt < now:
                        current_minutes += 15
                        continue
                except Exception as e:
                    logger.error(f"Ошибка проверки времени слота: {e}")
                
                slot_start = current_minutes
                slot_end = current_minutes + total_duration
                
                slot_overlaps = False
                spec_busy = busy_intervals_by_specialist.get(selected_specialist, [])
                for busy_start, busy_end in spec_busy:
                    if not (slot_end <= busy_start or slot_start >= busy_end):
                        slot_overlaps = True
                        break
                
                if not slot_overlaps:
                    available_slots.append({
                        "time": time_str,
                        "specialist": selected_specialist,
                        "available_specialists": [selected_specialist],
                        "available_count": 1,
                        "is_any_mode": False
                    })
                
                current_minutes += 15
    
    logger.info(f"Сгенерировано {len(available_slots)} свободных слотов")
    
    if available_slots:
        logger.info(f"   ДОСТУПНЫЕ СЛОТЫ (первые 5):")
        for slot in available_slots[:5]:
            time_str = slot['time']
            hour = int(time_str.split(':')[0])
            minute = int(time_str.split(':')[1])
            start_minutes = hour * 60 + minute
            end_minutes = start_minutes + total_duration
            
            end_hour = end_minutes // 60
            end_minute = end_minutes % 60
            
            if slot.get('is_any_mode', False):
                specs = slot.get('available_specialists', [])
                logger.info(f"      {time_str}-{end_hour:02d}:{end_minute:02d} - свободны: {len(specs)} специалистов")
            else:
                logger.info(f"      {time_str}-{end_hour:02d}:{end_minute:02d} - {slot['specialist']}")
    
    if len(available_slots) > 40:
        available_slots = available_slots[:40]
    
    return available_slots

print("✅ Модуль slots.py загружен.")
