import logging
import os
import time  # <-- ДОБАВЛЕНО: для защиты от спама
from datetime import datetime, timedelta
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config import TELEGRAM_TOKEN, TIMEZONE, RESERVATION_TIMEOUT, WARNING_TIMEOUT
from utils.google import (
    get_sheet_data,
    append_to_sheet,
    update_sheet_row,
    get_calendar_events,
    create_calendar_event,
    update_calendar_event,
    delete_calendar_event,
)
from utils.slots import generate_slots_for_10_days, find_available_slots
from utils.reminders import send_reminders
from utils.admin import load_admins, notify_admins

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

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

# Глобальные данные сессии
user_data = {}
user_last_msg = {}  # <-- ДОБАВЛЕНО: для защиты от спама

def format_duration(minutes):
    """Форматирует минуты в формат: 90 → '1 ч 30 мин'"""
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
    chat_id = update.effective_chat.id

    # === ЗАЩИТА ОТ СПАМА ===
    now = time.time()
    if now - user_last_msg.get(chat_id, 0) < 1.5:
        return
    user_last_msg[chat_id] = now
    # =======================

    user_data[chat_id] = {"state": MENU}

    # Привязка chat_id через start=bind_...
    if context.args and context.args[0].startswith("bind_"):
        record_id = context.args[0].split("bind_")[1]
        records = get_sheet_data(SHEET_ID, "Записи!A2:P")
        for row in records:
            if len(row) > 13 and row[0] == record_id:
                row_index = int(record_id.split("-")[1]) + 1
                update_sheet_row(SHEET_ID, "Записи", row_index, row[:13] + [str(chat_id)] + row[14:])
                await update.message.reply_text("✅ Вы привязаны к записи. Напоминания будут приходить сюда.")
                return

    # График салона из таблицы
    salon_schedule = get_sheet_data(SHEET_ID, "График мастеров!A2:E2")
    schedule_text = "10:00–20:00"
    if salon_schedule and len(salon_schedule) > 0:
        try:
            days = salon_schedule[0][1]
            start_time = salon_schedule[0][2]
            end_time = salon_schedule[0][3]
            schedule_text = f"{days}: {start_time}–{end_time}"
        except IndexError:
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
    chat_id = query.from_user.id

    # === ЗАЩИТА ОТ СПАМА ===
    now = time.time()
    if now - user_last_msg.get(chat_id, 0) < 1.5:
        return
    user_last_msg[chat_id] = now
    # =======================

    if query.data == "book":
        return await select_service_type(update, context)
    elif query.data == "modify":
        await query.edit_message_text("Введите ваше имя или телефон:")
        user_data[chat_id]["state"] = MODIFY_RESERVATION
        return MODIFY_RESERVATION
    elif query.data == "prices":
        return await show_prices(update, context)
    elif query.data == "contact_admin":
        await query.edit_message_text("Напишите ваше сообщение — администратор свяжется с вами.")
        user_data[chat_id]["state"] = MENU
        return MENU

# --- ПОКАЗ ЦЕН ---
async def show_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    # === ЗАЩИТА ОТ СПАМА ===
    chat_id = query.from_user.id
    now = time.time()
    if now - user_last_msg.get(chat_id, 0) < 1.5:
        return
    user_last_msg[chat_id] = now
    # =======================

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
    chat_id = update.effective_chat.id

    # === ЗАЩИТА ОТ СПАМА ===
    now = time.time()
    if now - user_last_msg.get(chat_id, 0) < 1.5:
        return
    user_last_msg[chat_id] = now
    # =======================

    services = get_sheet_data(SHEET_ID, "Услуги!A2:A")
    service_types = list(set(row[0] for row in services if row))

    keyboard = [[InlineKeyboardButton(st, callback_data=f"service_{st}")] for st in service_types]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text("Выберите тип услуги:", reply_markup=reply_markup)
    user_data[chat_id]["state"] = SELECT_SERVICE_TYPE
    return SELECT_SERVICE_TYPE

# --- ВЫБОР ПОДУСЛУГИ ---
async def select_subservice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    service_type = query.data.split("_", 1)[1]
    chat_id = query.from_user.id

    # === ЗАЩИТА ОТ СПАМА ===
    now = time.time()
    if now - user_last_msg.get(chat_id, 0) < 1.5:
        return
    user_last_msg[chat_id] = now
    # =======================

    subservices = get_sheet_data(SHEET_ID, "Услуги!A2:B")
    options = [row[1] for row in subservices if row and len(row) > 1 and row[0] == service_type]

    keyboard = [[InlineKeyboardButton(opt, callback_data=f"subservice_{opt}")] for opt in options]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="book")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(f"Выберите услугу ({service_type}):", reply_markup=reply_markup)
    user_data[chat_id]["service_type"] = service_type
    user_data[chat_id]["subservice"] = None
    user_data[chat_id]["state"] = SELECT_SUBSERVICE
    return SELECT_SUBSERVICE

# --- ПОКАЗ ЦЕНЫ И ДЛИТЕЛЬНОСТИ ---
async def show_price_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.from_user.id

    # === ЗАЩИТА ОТ СПАМА ===
    now = time.time()
    if now - user_last_msg.get(chat_id, 0) < 1.5:
        return
    user_last_msg[chat_id] = now
    # =======================

    subservice = query.data.split("_", 1)[1]
    user_data[chat_id]["subservice"] = subservice

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
        [InlineKeyboardButton("⬅️ Назад", callback_data="book")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, reply_markup=reply_markup)
    user_data[chat_id]["state"] = SELECT_PRIORITY
    return SELECT_PRIORITY

# --- ДАТА или МАСТЕР? ---
async def select_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    priority = query.data.split("_")[1]
    chat_id = query.from_user.id

    # === ЗАЩИТА ОТ СПАМА ===
    now = time.time()
    if now - user_last_msg.get(chat_id, 0) < 1.5:
        return
    user_last_msg[chat_id] = now
    # =======================

    user_data[chat_id]["priority"] = priority

    available_dates = find_available_slots(
        service_type=user_data[chat_id]["service_type"],
        subservice=user_data[chat_id]["subservice"],
        priority=priority
    )
    dates = sorted(list(set(d["date"] for d in available_dates)))

    keyboard = [[InlineKeyboardButton(d, callback_data=f"date_{d}")] for d in dates]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="book")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text("Выберите дату:", reply_markup=reply_markup)
    user_data[chat_id]["state"] = SELECT_DATE
    return SELECT_DATE

# --- ВЫБОР МАСТЕРА ---
async def select_master(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    date_str = query.data.split("_", 1)[1]
    chat_id = query.from_user.id

    # === ЗАЩИТА ОТ СПАМА ===
    now = time.time()
    if now - user_last_msg.get(chat_id, 0) < 1.5:
        return
    user_last_msg[chat_id] = now
    # =======================

    user_data[chat_id]["date"] = date_str

    masters = get_sheet_data(SHEET_ID, "График мастеров!A2:E")
    available_masters = []

    for row in masters:
        if len(row) >= 6 and date_str[:2] in row[5]:
            available_masters.append(row[0])

    keyboard = [[InlineKeyboardButton(m, callback_data=f"master_{m}")] for m in available_masters]
    keyboard.append([InlineKeyboardButton("👤 Любой мастер", callback_data="master_any")])
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="book")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text("Выберите мастера:", reply_markup=reply_markup)
    user_data[chat_id]["state"] = SELECT_MASTER
    return SELECT_MASTER

# --- ВЫБОР ВРЕМЕНИ ---
async def select_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    master_key = query.data.split("_", 1)[1]
    chat_id = query.from_user.id

    # === ЗАЩИТА ОТ СПАМА ===
    now = time.time()
    if now - user_last_msg.get(chat_id, 0) < 1.5:
        return
    user_last_msg[chat_id] = now
    # =======================

    date_str = user_data[chat_id]["date"]
    service_type = user_data[chat_id]["service_type"]
    subservice = user_data[chat_id]["subservice"]

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
    user_data[chat_id]["state"] = SELECT_TIME
    return SELECT_TIME

# --- РЕЗЕРВ СЛОТА ---
async def reserve_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data.split("_", 2)
    master = data[1]
    time_str = data[2]
    chat_id = query.from_user.id

    # === ЗАЩИТА ОТ СПАМА ===
    now = time.time()
    if now - user_last_msg.get(chat_id, 0) < 1.5:
        return
    user_last_msg[chat_id] = now
    # =======================

    date_str = user_data[chat_id]["date"]
    dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
    start_dt = TIMEZONE.localize(dt)

    subservice = user_data[chat_id]["subservice"]
    services = get_sheet_data(SHEET_ID, "Услуги!A2:E")
    duration = 60
    for row in services:
        if len(row) > 1 and row[1] == subservice:
            duration = int(row[2]) + int(row[3])
            break
    end_dt = start_dt + timedelta(minutes=duration)

    event_id = create_calendar_event(
        calendar_id=CALENDAR_ID,
        summary="Резерв",
        start_time=start_dt.isoformat(),
        end_time=end_dt.isoformat(),
        color_id="5",
        description=f"Резерв: {subservice} к {master}"
    )

    user_data[chat_id]["temp_booking"] = {
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
        chat_id=chat_id,
        user_id=chat_id,
        name=f"reservation_timeout_{chat_id}"
    )
    context.job_queue.run_once(
        warn_reservation,
        WARNING_TIMEOUT,
        chat_id=chat_id,
        user_id=chat_id,
        name=f"reservation_warn_{chat_id}"
    )

    await query.edit_message_text("Слот зарезервирован! Введите ваше имя:")
    user_data[chat_id]["state"] = ENTER_NAME
    return ENTER_NAME

async def warn_reservation(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    await context.bot.send_message(chat_id, "⏳ Не забудьте подтвердить запись — осталось немного времени!")

async def release_reservation(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.chat_id
    temp_booking = user_data.get(chat_id, {}).get("temp_booking")
    if temp_booking and temp_booking.get("event_id"):
        delete_calendar_event(CALENDAR_ID, temp_booking["event_id"])
        await context.bot.send_message(chat_id, "Слот был освобождён из-за неактивности. Вы можете начать запись заново.")
    user_data.pop(chat_id, None)

# --- ВВОД ИМЕНИ ---
async def enter_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # === ЗАЩИТА ОТ СПАМА ===
    now = time.time()
    if now - user_last_msg.get(chat_id, 0) < 1.5:
        return
    user_last_msg[chat_id] = now
    # =======================

    name = update.message.text.strip()
    user_data[chat_id]["name"] = name
    await update.message.reply_text("Теперь введите ваш телефон или нажмите кнопку ниже.", reply_markup=ReplyKeyboardRemove())
    user_data[chat_id]["state"] = ENTER_PHONE
    return ENTER_PHONE

# --- ВВОД ТЕЛЕФОНА ---
async def enter_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # === ЗАЩИТА ОТ СПАМА ===
    now = time.time()
    if now - user_last_msg.get(chat_id, 0) < 1.5:
        return
    user_last_msg[chat_id] = now
    # =======================

    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()

    # === ВАЛИДАЦИЯ ТЕЛЕФОНА ===
    clean_phone = ''.join(filter(str.isdigit, phone))
    if len(clean_phone) < 10 or len(clean_phone) > 15:
        await update.message.reply_text("❌ Неверный формат телефона. Пожалуйста, введите от 10 до 15 цифр.")
        return  # возвращаемся к вводу
    phone = clean_phone
    # =========================

    user_data[chat_id]["phone"] = phone

    # Проверка пересечения времени
    records = get_sheet_data(SHEET_ID, "Записи!A2:P")
    for row in records:
        if len(row) > 13 and row[13] == str(chat_id) and row[8] == "подтверждено":
            start_time = datetime.strptime(f"{row[6]} {row[7]}", "%d.%m.%Y %H:%M")
            start_time = TIMEZONE.localize(start_time)
            end_time = start_time + timedelta(minutes=60)
            new_start = user_data[chat_id]["temp_booking"]["start_dt"]
            if not (new_start >= end_time or new_start + timedelta(minutes=60) <= start_time):
                await update.message.reply_text(f"❌ У вас уже есть запись на {row[6]} в {row[7]} к {row[5]}. Сначала отмените её.")
                return

    # Мягкое напоминание о повторной записи
    for row in records:
        if len(row) > 4 and row[1] == user_data[chat_id]["name"] and row[8] == "подтверждено" and row[4] == user_data[chat_id]["service_type"]:
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
        f"Услуга: {user_data[chat_id]['subservice']}\n"
        f"Мастер: {user_data[chat_id]['temp_booking']['master']}\n"
        f"Дата: {user_data[chat_id]['date']}, Время: {user_data[chat_id]['temp_booking']['time']}\n"
        f"Имя: {user_data[chat_id]['name']}, Телефон: {phone}",
        reply_markup=reply_markup
    )
    user_data[chat_id]["state"] = CONFIRM_RESERVATION
    return ENTER_PHONE

# --- ПОДТВЕРЖДЕНИЕ ЗАПИСИ ---
async def confirm_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.from_user.id

    # === ЗАЩИТА ОТ СПАМА ===
    now = time.time()
    if now - user_last_msg.get(chat_id, 0) < 1.5:
        return
    user_last_msg[chat_id] = now
    # =======================

    temp_booking = user_data[chat_id]["temp_booking"]

    update_calendar_event(
        calendar_id=CALENDAR_ID,
        event_id=temp_booking["event_id"],
        summary="Запись подтверждена",
        color_id="7",
        description=f"Клиент: {user_data[chat_id]['name']}, тел.: {user_data[chat_id]['phone']}"
    )

    records = get_sheet_data(SHEET_ID, "Записи!A:A")
    record_id = f"ЗАП-{len(records):03d}"

    append_to_sheet(SHEET_ID, "Записи", [
        record_id,
        user_data[chat_id]["name"],
        user_data[chat_id]["phone"],
        user_data[chat_id]["service_type"],
        user_data[chat_id]["subservice"],
        temp_booking["master"],
        temp_booking["date"],
        temp_booking["time"],
        "подтверждено",
        datetime.now(TIMEZONE).strftime("%d.%m.%Y %H:%M"),
        "",
        "❌", "❌",
        str(chat_id),
        temp_booking["event_id"]
    ])

    link = f"t.me/@salon_bot?start=bind_{record_id}"
    await query.edit_message_text(f"✅ Вы записаны на {user_data[chat_id]['subservice']} {temp_booking['date']} в {temp_booking['time']}.\n\nЧтобы получать напоминания, нажмите: {link}")
    await notify_admins(context, f"📢 Новая запись: {user_data[chat_id]['subservice']} к {temp_booking['master']} {temp_booking['date']} в {temp_booking['time']} — {user_data[chat_id]['name']}, {user_data[chat_id]['phone']}")

    user_data.pop(chat_id, None)

# --- ОТМЕНА РЕЗЕРВА ---
async def cancel_reservation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.from_user.id

    # === ЗАЩИТА ОТ СПАМА ===
    now = time.time()
    if now - user_last_msg.get(chat_id, 0) < 1.5:
        return
    user_last_msg[chat_id] = now
    # =======================

    temp_booking = user_data[chat_id].get("temp_booking")
    if temp_booking and temp_booking.get("event_id"):
        delete_calendar_event(CALENDAR_ID, temp_booking["event_id"])
    await query.edit_message_text("Резерв отменён. Слот освобождён.")
    user_data.pop(chat_id, None)

# --- ЗАПУСК БОТА ---
def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.job_queue.run_daily(
        generate_slots_for_10_days,
        time=datetime.strptime("00:00", "%H:%M").time(),
        days=(0, 1, 2, 3, 4, 5, 6)
    )

    application.job_queue.run_repeating(send_reminders, interval=60, first=10)

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, enter_name), group=1)
    application.add_handler(MessageHandler(filters.CONTACT, enter_phone), group=1)

    logger.info("Бот запущен.")
    application.run_polling()

if __name__ == "__main__":
    main()
