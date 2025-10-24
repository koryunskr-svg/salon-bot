main.py

# main.py
import logging
import logging.handlers
import os
from datetime import datetime, timedelta
import time
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    PicklePersistence,
)

from config import TELEGRAM_TOKEN, TIMEZONE, RESERVATION_TIMEOUT, WARNING_TIMEOUT, SHEET_ID, CALENDAR_ID
from utils.safe_google import (
    safe_get_sheet_data as get_sheet_data,
    safe_append_to_sheet as append_to_sheet,
    safe_update_sheet_row as update_sheet_row,
    safe_get_calendar_events as get_calendar_events,
    safe_create_calendar_event as create_calendar_event,
    safe_update_calendar_event as update_calendar_event,
    safe_delete_calendar_event as delete_calendar_event,
)
from utils.slots import generate_slots_for_10_days, find_available_slots
from utils.reminders import send_reminders
from utils.admin import load_admins, notify_admins

# =============== ПРОДАКШЕН-ЛОГИРОВАНИЕ ===============
def setup_production_logging():
    """Настройка прод-логирования"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]'
    )
    
    file_handler = logging.handlers.RotatingFileHandler(
        'bot_production.log',
        maxBytes=10*1024*1024,
        backupCount=5
    )
    file_handler.setFormatter(formatter)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

def log_business_event(event_type, **kwargs):
    """Логирование бизнес-событий"""
    logging.info(f"BUSINESS_EVENT: {event_type} - {kwargs}")

# =============== ГЛОБАЛЬНАЯ ОБРАБОТКА ОШИБОК ===============
async def global_error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальная обработка ошибок"""
    logging.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ Произошла техническая ошибка. Мы уже работаем над исправлением. "
                "Пожалуйста, попробуйте позже."
            )
    except Exception:
        pass
    
    await notify_admins(context, f"🚨 Критическая ошибка бота: {context.error}")

# =============== АВТООЧИСТКА СТАРЫХ СЕССИЙ ===============
async def update_last_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обновляет метку времени последней активности пользователя"""
    context.user_data["_last_activity"] = time.time()

async def global_activity_updater(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик для обновления активности на ЛЮБОЕ сообщение"""
    await update_last_activity(update, context)

async def cleanup_old_sessions_job(context: ContextTypes.DEFAULT_TYPE):
    """Удаляет сессии пользователей, неактивных более 30 дней"""
    now = time.time()
    max_age = 30 * 24 * 60 * 60
    to_remove = [
        user_id for user_id, data in context.application.user_data.items()
        if now - data.get("_last_activity", now) > max_age
    ]
    for user_id in to_remove:
        del context.application.user_data[user_id]
    if to_remove:
        logging.info(f"🧹 Очищено {len(to_remove)} старых сессий")

# =============== ПРОВЕРКА КОНФИГУРАЦИИ ===============
def validate_configuration():
    """Проверяет корректность конфигурации при старте"""
    required_config = {
        "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
        "SHEET_ID": SHEET_ID, 
        "CALENDAR_ID": CALENDAR_ID,
        "TIMEZONE": TIMEZONE
    }
    
    missing = [key for key, value in required_config.items() if not value]
    if missing:
        logging.critical(f"❌ Не заданы обязательные параметры в config.py: {', '.join(missing)}")
        return False
    
    if not all([RESERVATION_TIMEOUT, WARNING_TIMEOUT]):
        logging.critical("❌ Не заданы таймауты резервирования")
        return False
        
    logging.info("✅ Конфигурация проверена успешно")
    return True

# =============== ЗАЩИТА ОТ ПОВТОРНОГО ЗАПУСКА ===============
def create_lock_file():
    """Создает lock-файл для защиты от повторного запуска"""
    lock_file = "bot.lock"
    if os.path.exists(lock_file):
        logging.critical("❌ Бот уже запущен! Файл bot.lock существует.")
        return False
    
    try:
        with open(lock_file, 'w') as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        logging.error(f"❌ Не удалось создать lock-файл: {e}")
        return False

def remove_lock_file():
    """Удаляет lock-файл при завершении работы"""
    try:
        if os.path.exists("bot.lock"):
            os.remove("bot.lock")
    except Exception as e:
        logging.error(f"❌ Не удалось удалить lock-файл: {e}")

# Состояния диалога
(
    MENU,
    SELECT_SERVICE_TYPE,
    SELECT_SUBSERVICE,
    SHOW_PRICE_INFO,
    SELECT_PRIORITY,
    SELECT_DATE,
    SELECT_MASTER,
    SELECT_TIME,
    ENTER_NAME,
    ENTER_PHONE,
    CONFIRM_RESERVATION,
    MODIFY_RESERVATION,
    WAITING_LIST_NAME,
    WAITING_LIST_PHONE,
    CALLBACK_NAME,
    CALLBACK_PHONE,
    ADMIN_RECORD_START,
) = range(17)

def format_duration(minutes):
    hours = minutes // 60
    mins = minutes % 60
    if hours == 0:
        return f"{mins} мин"
    elif mins == 0:
        return f"{hours} ч"
    else:
        return f"{hours} ч {mins} мин"

# --- START ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update_last_activity(update, context)
    log_business_event("user_started", user_id=update.effective_user.id)
    
    context.user_data["state"] = MENU

    if context.args and context.args[0].startswith("bind_"):
        record_id = context.args[0].split("bind_")[1]
        records = get_sheet_data(SHEET_ID, "Записи!A2:P")
        for idx, row in enumerate(records, start=2):
            if len(row) > 0 and row[0] == record_id:
                row_to_write = row[:13] + [str(update.effective_chat.id)] + row[14:]
                update_sheet_row(SHEET_ID, "Записи", idx, row_to_write)
                await update.message.reply_text("✅ Вы привязаны к записи. Напоминания будут приходить сюда.")
                return

    # 🔧 ИСПРАВЛЕНО: поиск по имени "Салон"
    all_schedule = get_sheet_data(SHEET_ID, "График мастеров!A2:F")
    schedule_text = "10:00–20:00"
    for row in all_schedule:
        if len(row) >= 4 and row[0] == "Салон":
            try:
                days = row[1]
                start_time = row[2]
                end_time = row[3]
                schedule_text = f"{days}: {start_time}–{end_time}"
                break
            except Exception:
                pass

    keyboard = [
        [InlineKeyboardButton("📅 Записаться на приём", callback_data="book")],
        [InlineKeyboardButton("❌ Отменить или изменить запись", callback_data="modify")],
        [InlineKeyboardButton("💅 Услуги и цены", callback_data="prices")],
        [InlineKeyboardButton("📞 Связаться с админом", callback_data="contact_admin")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"Добро пожаловать в салон красоты!\n\nМы работаем:\n{schedule_text}",
        reply_markup=reply_markup
    )
    return MENU

# --- ОБРАБОТКА КНОПОК ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await update_last_activity(update, context)
    
    data = query.data

# 🔹 Обработка "Назад" с учётом состояния
if data == "back":
    current_state = context.user_data.get("state")
    if current_state == SELECT_SUBSERVICE:
        return await select_service_type(update, context)
    elif current_state == SHOW_PRICE_INFO:
        service_type = context.user_data.get("service_type")
        if service_type:
            context.user_data["state"] = SELECT_SERVICE_TYPE
            subservices = get_sheet_data(SHEET_ID, "Услуги!A2:B")
            options = [row[1] for row in subservices if row and len(row) > 1 and row[0] == service_type]
            keyboard = [[InlineKeyboardButton(opt, callback_data=f"subservice_{opt}")] for opt in options]
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.callback_query.edit_message_text(f"Выберите услугу ({service_type}):", reply_markup=reply_markup)
            return SELECT_SUBSERVICE
    elif current_state in (SELECT_PRIORITY, SELECT_DATE, SELECT_MASTER, SELECT_TIME):
        # Возврат к выбору услуги
        return await show_price_info(update, context)
    # По умолчанию — в главное меню
    await start(update, context)
    return MENU

 # Остальные условия
    if data == "book":
        return await select_service_type(update, context)
    elif data == "modify":
        await query.edit_message_text("Введите ваше имя или телефон:")
        context.user_data["state"] = MODIFY_RESERVATION
        return MODIFY_RESERVATION
    elif data == "prices":
        return await show_prices(update, context)
    elif data == "contact_admin":
        await query.edit_message_text("Напишите ваше сообщение — администратор свяжется с вами.")
        context.user_data["state"] = MENU
        return MENU

    if data.startswith("service_"):
        return await select_subservice(update, context)
    if data.startswith("subservice_"):
        return await show_price_info(update, context)
    if data.startswith("priority_"):
        return await select_priority(update, context)
    if data.startswith("date_"):
        return await select_master(update, context)
    if data.startswith("master_"):
        return await select_time(update, context)
    if data.startswith("slot_"):
        return await reserve_slot(update, context)

    if data.startswith("confirm_reminder_"):
        record_id = data.split("confirm_reminder_")[1]
        await handle_confirm_reminder(record_id, query, context)
        return
    if data.startswith("cancel_reminder_"):
        record_id = data.split("cancel_reminder_")[1]
        await handle_cancel_reminder(record_id, query, context)
        return

    if data == "confirm":
        return await confirm_booking(update, context)
    if data == "cancel_reserve":
        return await cancel_reservation(update, context)
    if data == "waiting_list":
        await query.edit_message_text("Вы добавлены в лист ожидания.")
        return MENU

    await query.edit_message_text("Неизвестная команда.")

# --- ПОКАЗ ЦЕН ---
async def show_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    services = get_sheet_data(SHEET_ID, "Услуги!A2:E")
    prices_text = "💅 МАНИКЮРНЫЕ\n"
    in_manicure = True

    for row in services:
        if len(row) < 5:
            continue

        category, subservice, duration, buffer, price = row[0], row[1], int(row[2]), int(row[3]), row[4]
        duration_min = duration + buffer
        formatted_duration = format_duration(duration_min)
        price_str = f"{int(float(price))} ₽" if price.replace('.', '').isdigit() else "цена не указана"

        if category == "Парикмахерские" and in_manicure:
            prices_text += "\n✂️ ПАРИКМАХЕРСКИЕ\n"
            in_manicure = False

        prices_text += f"• {subservice} — {price_str} ({formatted_duration})\n"

    await query.edit_message_text(prices_text, reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад", callback_data="start")]
    ]))

# --- ВЫБОР ТИПА УСЛУГИ ---
async def select_service_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    services = get_sheet_data(SHEET_ID, "Услуги!A2:A")
    service_types = list(set(row[0] for row in services if row))

    keyboard = [[InlineKeyboardButton(st, callback_data=f"service_{st}")] for st in service_types]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text("Выберите тип услуги:", reply_markup=reply_markup)
    context.user_data["state"] = SELECT_SERVICE_TYPE
    return SELECT_SERVICE_TYPE

# --- ВЫБОР ПОДУСЛУГИ ---
async def select_subservice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    service_type = query.data.split("_", 1)[1]

    subservices = get_sheet_data(SHEET_ID, "Услуги!A2:B")
    options = [row[1] for row in subservices if row and len(row) > 1 and row[0] == service_type]

    keyboard = [[InlineKeyboardButton(opt, callback_data=f"subservice_{opt}")] for opt in options]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(f"Выберите услугу ({service_type}):", reply_markup=reply_markup)
    context.user_data["service_type"] = service_type
    context.user_data["subservice"] = None
    context.user_data["state"] = SELECT_SUBSERVICE
    return SELECT_SUBSERVICE

# --- ПОКАЗ ЦЕНЫ И ДЛИТЕЛЬНОСТИ ---
async def show_price_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    subservice = query.data.split("_", 1)[1]
    context.user_data["subservice"] = subservice

    services = get_sheet_data(SHEET_ID, "Услуги!A2:E")
    duration, buffer, price = 60, 0, "не указана"
    for row in services:
        if len(row) > 1 and row[1] == subservice:
            duration = int(row[2])
            buffer = int(row[3])
            price = row[4]
            break

    total_duration = duration + buffer
    formatted_duration = format_duration(total_duration)
    price_str = f"{int(float(price))} ₽" if price.replace('.', '').isdigit() else "цена не указана"

    text = (
        f"✅ Услуга: {subservice}\n"
        f"💰 Цена: {price_str}\n"
        f"⏳ Длительность: {formatted_duration}\n\n"
        "Что для вас важнее?"
    )

    keyboard = [
        [InlineKeyboardButton("📅 Сначала дата", callback_data="priority_date")],
        [InlineKeyboardButton("👩‍🦰 Сначала мастер", callback_data="priority_master")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, reply_markup=reply_markup)
    context.user_data["state"] = SELECT_PRIORITY
    return SELECT_PRIORITY

# --- ДАТА или МАСТЕР? ---
async def select_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    priority = query.data.split("_")[1]
    context.user_data["priority"] = priority

    available_dates = find_available_slots(
        service_type=context.user_data["service_type"],
        subservice=context.user_data["subservice"],
        priority=priority
    )
    dates = sorted(list(set(d["date"] for d in available_dates)))

    keyboard = [[InlineKeyboardButton(d, callback_data=f"date_{d}")] for d in dates]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text("Выберите дату:", reply_markup=reply_markup)
    context.user_data["state"] = SELECT_DATE
    return SELECT_DATE

# --- ВЫБОР МАСТЕРА ---
async def select_master(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    date_str = query.data.split("_", 1)[1]
    context.user_data["date"] = date_str

    # 🔧 ИСПРАВЛЕНО: читаем A2:F, определяем день недели, проверяем график
    from datetime import datetime as dt_mod
    try:
        target_date = dt_mod.strptime(date_str, "%d.%m.%Y")
        day_name = target_date.strftime("%a")
        short_day = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс"}.get(day_name)
    except Exception:
        await query.edit_message_text("❌ Неверный формат даты.")
        return

    masters = get_sheet_data(SHEET_ID, "График мастеров!A2:F")
    available_masters = []

    for row in masters:
        if len(row) < 2:
            continue
        master_name = row[0]
        if master_name == "Салон":
            continue
        work_days = row[1].split(", ") if len(row) > 1 else []
        if short_day not in work_days:
            continue
        if len(row) > 5 and row[5].strip():
            blacklisted_dates = [d.strip() for d in row[5].split(",")]
            if date_str in blacklisted_dates:
                continue
        available_masters.append(master_name)

    if not available_masters:
        await query.edit_message_text(f"❌ Нет мастеров на {date_str} ({short_day}).\nДоступные дни: {[row[1] for row in masters if len(row)>1 and row[0]!='Салон']}")
        return

    keyboard = [[InlineKeyboardButton(m, callback_data=f"master_{m}")] for m in available_masters]
    keyboard.append([InlineKeyboardButton("👤 Любой мастер", callback_data="master_any")])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text("Выберите мастера:", reply_markup=reply_markup)
    context.user_data["state"] = SELECT_MASTER
    return SELECT_MASTER

# --- ВЫБОР ВРЕМЕНИ ---
async def select_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    master_key = query.data.split("_", 1)[1]

    date_str = context.user_data["date"]
    service_type = context.user_data["service_type"]
    subservice = context.user_data["subservice"]

    slots = find_available_slots(
        service_type=service_type,
        subservice=subservice,
        date=date_str,
        selected_master=master_key if master_key != "any" else None
    )

    if not slots:
        keyboard = [[InlineKeyboardButton("В лист ожидания", callback_data="waiting_list")]]
        await query.edit_message_text("❌ Свободных слотов нет. Хотите встать в лист ожидания?", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    keyboard = []
    for slot in slots:
        time_str = slot["time"]
        master = slot["master"]
        callback_data = f"slot_{master}_{time_str}"
        keyboard.append([InlineKeyboardButton(f"{time_str} — {master}", callback_data=callback_data)])

    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="book")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text("Выберите время:", reply_markup=reply_markup)
    context.user_data["state"] = SELECT_TIME
    return SELECT_TIME

# --- РЕЗЕРВ СЛОТА ---
async def reserve_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data.split("_", 2)
    master = data[1]
    time_str = data[2]

    date_str = context.user_data["date"]
    
    # 🔄 ПОВТОРНАЯ ПРОВЕРКА: не заняли ли слот пока пользователь думал?
    slots = find_available_slots(
        service_type=context.user_data["service_type"],
        subservice=context.user_data["subservice"],
        date=date_str,
        priority=context.user_data.get("priority", "date")
    )
    
    # Проверяем, что выбранный слот еще доступен
    slot_still_available = False
    for slot in slots:
        if (slot["time"] == time_str and 
            (master == "any" or slot["master"] == master)):
            slot_still_available = True
            break
    
    if not slot_still_available:
        await query.edit_message_text(
            "❌ Этот слот только что заняли. Пожалуйста, выберите другое время.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад к выбору времени", 
                                    callback_data=f"date_{date_str}")]
            ])
        )
        return SELECT_DATE

    dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
    start_dt = TIMEZONE.localize(dt)

    subservice = context.user_data["subservice"]
    services = get_sheet_data(SHEET_ID, "Услуги!A2:E")
    duration = 60
    for row in services:
        if len(row) > 1 and row[1] == subservice:
            duration = int(row[2]) + int(row[3])
            break
    end_dt = start_dt + timedelta(minutes=duration)

    event_id = create_calendar_event(
        calendar_id=CALENDAR_ID,
        summary="⏳ Бронь (в процессе)",
        start_time=start_dt.isoformat(),
        end_time=end_dt.isoformat(),
        color_id="5",
        description=f"Бронь: {subservice} к {master}. В процессе оформления..."
    )

    context.user_data["temp_booking"] = {
        "master": master,
        "time": time_str,
        "date": date_str,
        "event_id": event_id,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "duration": duration,
        "subservice": subservice
    }

    context.job_queue.run_once(
        release_reservation,
        RESERVATION_TIMEOUT,
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,

        name=f"reservation_timeout_{update.effective_chat.id}"
    )
    context.job_queue.run_once(
        warn_reservation,
        WARNING_TIMEOUT,
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id,
        name=f"reservation_warn_{update.effective_chat.id}"
    )

    await query.edit_message_text("Слот зарезервирован! Введите ваше имя:")
    context.user_data["state"] = ENTER_NAME
    return ENTER_NAME

async def warn_reservation(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    await context.bot.send_message(chat_id, "⏳ Не забудьте подтвердить запись — осталось немного времени!")

async def release_reservation(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    temp_booking = context.user_data.get("temp_booking")
    if temp_booking and temp_booking.get("event_id"):
        try:
            delete_calendar_event(CALENDAR_ID, temp_booking["event_id"])
        except Exception:
            pass
        await context.bot.send_message(job.chat_id,
  "Слот был освобождён из-за неактивности. Вы можете начать запись заново.")
    context.user_data.clear()

# --- ВВОД ИМЕНИ ---
async def enter_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update_last_activity(update, context)
    
    if context.user_data.get("state") != ENTER_NAME:
        return
    
    name = update.message.text.strip()
    
    # ВАЛИДАЦИЯ ИМЕНИ
    def validate_name(name_str):
        if len(name_str) < 2 or len(name_str) > 50:
            return False
        clean_name = name_str.replace(' ', '').replace('-', '')
        return clean_name.isalpha()
    
    if not validate_name(name):
        await update.message.reply_text(
            "❌ Неверный формат имени. Используйте только буквы, длина 2-50 символов."
        )
        return ENTER_NAME
    
    context.user_data["name"] = name
    await update.message.reply_text("Теперь введите ваш телефон или нажмите кнопку ниже.", reply_markup=ReplyKeyboardRemove())
    context.user_data["state"] = ENTER_PHONE
    return ENTER_PHONE

# --- ВВОД ТЕЛЕФОНА ---
async def enter_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update_last_activity(update, context)
    
    if context.user_data.get("state") != ENTER_PHONE:
        return

    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()

    # ВАЛИДАЦИЯ НОМЕРА ТЕЛЕФОНА
    def validate_phone(phone_str):
        clean_phone = ''.join(filter(str.isdigit, phone_str))
        return 10 <= len(clean_phone) <= 15
    
    if not validate_phone(phone):
        await update.message.reply_text(
            "❌ Неверный формат номера телефона. "
            "Пожалуйста, введите номер в международном формате (+7...) "
            "или используйте кнопку 'Отправить телефон'"
        )
        return ENTER_PHONE

    context.user_data["phone"] = phone

    records = get_sheet_data(SHEET_ID, "Записи!A2:P")
    for row in records:
        if len(row) > 13 and row[13] == str(update.effective_chat.id) and row[8] == "подтверждено":
            try:
                start_time = datetime.strptime(f"{row[6]} {row[7]}", "%d.%m.%Y %H:%M")
                start_time = TIMEZONE.localize(start_time)
                end_time = start_time + timedelta(minutes=60)
                new_start = context.user_data["temp_booking"]["start_dt"]
                if not (new_start >= end_time or new_start + timedelta(minutes=60) <= start_time):
                    await update.message.reply_text(f"❌ У вас уже есть запись на {row[6]} в {row[7]} к {row[5]}. Сначала отмените её.")
                    return
            except Exception:
                continue

    for row in records:
        if len(row) > 4 and row[1] == context.user_data["name"] and row[8] == "подтверждено" and row[4] == context.user_data["service_type"]:
            await update.message.reply_text(
                f"⚠️ У вас уже есть запись на {row[4]} {row[6]} в {row[7]} к {row[5]}.\nВы уверены, что хотите записаться ещё раз?",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Да, хочу", callback_data="confirm_repeat")],
                    [InlineKeyboardButton("❌ Изменить запись", callback_data="cancel_reserve")]
                ])
            )
            return

    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm")],
        [InlineKeyboardButton("❌ Отменить", callback_data="cancel_reserve")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"Подтвердите запись:\n"
        f"Услуга: {context.user_data['subservice']}\n"
        f"Мастер: {context.user_data['temp_booking']['master']}\n"
        f"Дата: {context.user_data['date']}, Время: {context.user_data['temp_booking']['time']}\n"
        f"Имя: {context.user_data['name']}, Телефон: {phone}",
        reply_markup=reply_markup
    )
    context.user_data["state"] = CONFIRM_RESERVATION
    return ENTER_PHONE

# --- ПОДТВЕРЖДЕНИЕ ЗАПИСИ ---
async def confirm_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    temp_booking = context.user_data.get("temp_booking")
    if not temp_booking:
        await query.edit_message_text("Внутренняя ошибка: нет временной записи.")
        return

    update_calendar_event(
        calendar_id=CALENDAR_ID,
        event_id=temp_booking["event_id"],
        summary="Запись подтверждена",
        color_id="7",
        description=f"Клиент: {context.user_data['name']}, тел.: {context.user_data['phone']}"
    )

    records = get_sheet_data(SHEET_ID, "Записи!A:A")
    record_id = f"ЗАП-{len(records):03d}"

    append_to_sheet(SHEET_ID, "Записи", [
        record_id,
        context.user_data["name"],
        context.user_data["phone"],
        context.user_data["service_type"],
        context.user_data["subservice"],
        temp_booking["master"],
        temp_booking["date"],
        temp_booking["time"],
        "подтверждено",
        datetime.now(TIMEZONE).strftime("%d.%m.%Y %H:%M"),
        "", "❌", "❌",
        str(update.effective_chat.id),
        temp_booking["event_id"]
    ])

    link = f"t.me/@salon_bot?start=bind_{record_id}"
    await query.edit_message_text(f"✅ Вы записаны на {context.user_data['subservice']} {temp_booking['date']} в {temp_booking['time']}.\n\nЧтобы получать напоминания, нажмите: {link}")
    
    log_business_event("booking_confirmed", 
                      user_id=update.effective_user.id,
                      service=context.user_data.get("subservice"),
                      master=context.user_data.get("temp_booking", {}).get("master"))
    
    await notify_admins(context, f"📢 Новая запись: {context.user_data['subservice']} к {temp_booking['master']} {temp_booking['date']} в {temp_booking['time']} — {context.user_data['name']}, {context.user_data['phone']}")

    context.user_data.clear()

# --- ОТМЕНА РЕЗЕРВА ---
async def cancel_reservation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    temp_booking = context.user_data.get("temp_booking")
    if temp_booking and temp_booking.get("event_id"):
        try:
            delete_calendar_event(CALENDAR_ID, temp_booking["event_id"])
        except Exception:
            pass
    await query.edit_message_text("Резерв отменён. Слот освобождён.")
    context.user_data.clear()

# --- Обработчики напоминаний ---
async def handle_confirm_reminder(record_id: str, query, context):
    try:
        records = get_sheet_data(SHEET_ID, "Записи!A2:P")
        for idx, row in enumerate(records, start=2):
            if len(row) > 0 and row[0] == record_id:
                if len(row) < 12:
                    row.extend([""] * (12 - len(row)))
                row[11] = "✅"
                update_sheet_row(SHEET_ID, "Записи", idx, row)
                await query.edit_message_text("Спасибо! Ваша запись подтверждена.")
                return
        await query.edit_message_text("Запись не найдена.")
    except Exception as e:
        logging.exception("Ошибка при подтверждении напоминания: %s", e)
        await query.edit_message_text("Ошибка при обработке подтверждения.")

async def handle_cancel_reminder(record_id: str, query, context):
    try:
        records = get_sheet_data(SHEET_ID, "Записи!A2:P")
        for idx, row in enumerate(records, start=2):
            if len(row) > 0 and row[0] == record_id:
                if len(row) < 9:
                    row.extend([""] * (9 - len(row)))
                row[8] = "отменено"
                event_id = row[14] if len(row) > 14 else None
                update_sheet_row(SHEET_ID, "Записи", idx, row)
                if event_id:
                    try:
                        delete_calendar_event(CALENDAR_ID, event_id)
                    except Exception:
                        pass
                await query.edit_message_text("Запись отменена. Спасибо, что сообщили.")
                await notify_admins(context, f"❗ Клиент отменил запись {record_id}.")
                return
        await query.edit_message_text("Запись не найдена.")
    except Exception as e:
        logging.exception("Ошибка при отмене записи из напоминания: %s", e)
        await query.edit_message_text("Ошибка при обработке отмены.")
# ============================================
# 🔧 ИСПРАВЛЕННЫЙ  ПАТЧ  
# ============================================

import time
from datetime import datetime, time as datetime_time

from config import SHEET_ID
from utils.safe_google import safe_append_to_sheet as append_to_sheet

from utils.admin import load_admins, notify_admins
WORK_HOURS = (datetime_time(9, 0), datetime_time(21, 0))
TRIGGER_WORDS = ["админ", "связаться", "помощь", "человек", "менеджер"]

# 🔹 1. Триггерные слова
async def handle_trigger_words(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    if context.user_data.get("state") not in [0, None]:
        return

    text = update.message.text.lower()
    for trigger in TRIGGER_WORDS:
        if trigger in text:
            user = update.effective_user
            now = datetime.now().time()

            try:
                if WORK_HOURS[0] <= now <= WORK_HOURS[1]:
                    await notify_admins(context, f"📞 {user.first_name}: {update.message.text}")
                    await update.message.reply_text("✅ Администратор свяжется с вами")
                else:
                    append_to_sheet(SHEET_ID, "Обратные звонки", [
                        f"CALL-{int(time.time())}", datetime.now().strftime("%d.%m.%Y %H:%M"),
                        user.first_name or "Не указано", "", "Telegram", "", "ожидает"
                    ])
                    await update.message.reply_text("⏰ Мы не работаем. Ваш запрос сохранён.")
            except Exception:
                await update.message.reply_text("✅ Ваше сообщение получено.")
            break  # 🟢 предотвращает повторные срабатывания при нескольких словах подряд

# 🔹 2. Команда /record
async def handle_record_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        admins = load_admins() or []
        user_id = str(update.effective_user.id)
        is_admin = any(admin and admin.get('chat_id') == user_id for admin in admins)

        if not is_admin:
            await update.message.reply_text("❌ Нет прав")
            return

        context.user_data.clear()
        await update.message.reply_text(
            "👨‍💼 Режим записи от имени клиента:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 Записаться на приём", callback_data="book")]
            ])
        )
    except Exception:
        await update.message.reply_text("❌ Ошибка доступа")

# 🔹 3. Лист ожидания
async def handle_waiting_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "waiting_list":
        try:
            user_data = context.user_data
            append_to_sheet(SHEET_ID, "Лист ожидания", [
                "1", f"WAIT-{int(time.time())}", user_data.get("name", ""),
                user_data.get("phone", ""), user_data.get("service_type", ""),
                user_data.get("subservice", ""), "ожидает",
                datetime.now().strftime("%d.%m.%Y %H:%M")
            ])
            await query.edit_message_text("📋 Вы в листе ожидания")
        except Exception:
            await query.edit_message_text("✅ Запрос принят")

# 🔹 4. Регистрация хэндлеров
def register_handlers_directly(application):
    """ВЫЗВАТЬ ЭТУ ФУНКЦИЮ ИЗ main() ПОСЛЕ СОЗДАНИЯ application"""
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_trigger_words), group=99)
    application.add_handler(CommandHandler("record", handle_record_command))
    application.add_handler(CallbackQueryHandler(handle_waiting_list, pattern="^waiting_list$"), group=1)

# --- ЗАПУСК БОТА ---
def main():
    # Защита от повторного запуска
    if not create_lock_file():
        return

    # === Добавлено: перехват системных сигналов (SIGTERM/SIGINT) для корректного удаления lock-файла ===
    import signal
    import sys

    def _handle_exit(signum, frame):
        logging.info(f"Получен системный сигнал {signum}, завершаем работу...")
        try:
            remove_lock_file()
        except Exception:
            pass
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, _handle_exit)
        signal.signal(signal.SIGINT, _handle_exit)
    except Exception as _err:
        logging.debug(f"Не удалось установить signal handlers: {_err}")
    # ==============================================================================================

    # Настройка прод-логирования
    setup_production_logging()
    
    # Проверка конфигурации
    if not validate_configuration():
        remove_lock_file()
        return
    
    try:
        load_admins()
        log_business_event("bot_started")
        logging.info("✅ Модули загружены успешно")
    except Exception as e:
        logging.exception("❌ Не удалось загрузить модули при старте: %s", e)
        remove_lock_file()
        return

    persistence = PicklePersistence(filepath="bot_data.pickle")
    
    try:
        application = Application.builder()\
            .token(TELEGRAM_TOKEN)\
            .persistence(persistence)\
            .build()
    except Exception as e:
        logging.critical(f"❌ Не удалось создать приложение: {e}")
        remove_lock_file()
        return

    # Глобальная обработка ошибок
    application.add_error_handler(global_error_handler)
    
    # Глобальный обработчик активности (на ЛЮБОЕ сообщение) - в отдельную группу
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, global_activity_updater), group=-1)

    # Автоочистка старых сессий каждый день в 03:00
    application.job_queue.run_daily(
        cleanup_old_sessions_job,
        time=datetime.strptime("03:00", "%H:%M").time()
    )
    
    application.job_queue.run_daily(
        generate_slots_for_10_days,
        time=datetime.strptime("00:00", "%H:%M").time(),
        days=(0, 1, 2, 3, 4, 5, 6)
    )
    application.job_queue.run_repeating(send_reminders, interval=60, first=10)

    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name), group=1)
    application.add_handler(MessageHandler(filters.CONTACT, enter_phone), group=1)

 # 🔹 Регистрация дополнительных хэндлеров — ПОСЛЕ основных
    register_handlers_directly(application)

    logging.info("🚀 Бот запущен в продакшен-режиме")
    
    try:
        application.run_polling()
    except Exception as e:
        logging.critical(f"❌ Критическая ошибка при работе бота: {e}")
    finally:
        remove_lock_file()


if __name__ == "__main__":
    main()


