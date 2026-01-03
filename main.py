1. Исправление в find_available_slots
Но это не решает проблему полностью. Нужно учитывать, что работают до 20:00 ВКЛЮЧИТЕЛЬНО. - а в субботу до 18=9:00. Внисить это исправление? Оно решает проблеиу?
2. Добавим в utils/slots.py новую функцию: def get_specialist_schedule(specialist_name: str, date_str: str): - поскольку мы внесли большое исправление, даю тебе отредактированный utils/slots.py. Изучи его и потом дай дополнение
3.

 



 на телефоне расписание в меню отражается некрасиво:
Мы работаем: Пн-Пт
10:00-14:00, 15:00-20:00, Сб
10:00-14:00, 15:00-19:00, Вс
Выходной
Лучше бы
Мы работаем: 
Пн-Пт 10:00-14:00, 15:00-20:00, 
Сб 10:00-14:00, 15:00-19:00, 
Вс Выходной
А еще лучше
Мы работаем: 
Пн-Пт     10:00-20:00,  
Сб           10:00-19:00, 
Перерыв 14:00-15:00, 
Вс                Выходной





1. root@5861467-mu663385:~/salon-bot# python main.py
Traceback (most recent call last):
  File "/root/salon-bot/main.py", line 5, in <module>
    from dotenv import load_dotenv
ModuleNotFoundError: No module named 'dotenv'
root@5861467-mu663385:~/salon-bot#
2. async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await update_last_activity(update, context)
    data = query.data

    print("🚨🚨🚨 button_handler ВЫЗВАН! 🚨🚨🚨")
    print(f"🚨 data = '{data}'")
    print(f"🚨 User = {update.effective_user.id}")

    # ← НОВЫЙ ПРИНТ ДЛЯ ДИАГНОСТИКИ
    print("=" * 70)
    print(f"🎯 НАЖАТА КНОПКА: '{data}'")
    print(f"🎯 User ID: {update.effective_user.id}")
    print(f"🎯 Username: {update.effective_user.username}")
    print(f"🎯 Время: {datetime.now().strftime('%H:%M:%S.%f')}")
    
    # ← СПЕЦИАЛЬНО ДЛЯ call_admin_
    if data.startswith("call_admin_"):
        print(f"🔥 ОБНАРУЖЕН call_admin_! Номер: {data.split('call_admin_', 1)[1]}")
    
    print("=" * 70)
    # ← КОНЕЦ ДОБАВЛЕНИЯ
    # === НАЧАЛО ОТЛАДКИ ===
    logger.info(f"🔄 DEBUG button_handler: Нажата кнопка с data='{data}'")
    logger.info(
        f"🔄 DEBUG: Текущий state={context.user_data.get('state')}, priority={context.user_data.get('priority')}"
    )
    # === КОНЕЦ ОТЛАДКИ ===
