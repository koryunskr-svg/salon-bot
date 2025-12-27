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
