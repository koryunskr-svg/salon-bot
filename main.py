# main.py - Q-1977-11.11.25
import logging
import logging.handlers
import os
import time
from datetime import datetime, timedelta
from datetime import time as datetime_time
import pytz
import signal
import sys
import threading
from typing import Dict, Any

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    PicklePersistence,
    ApplicationBuilder,
)

# --- ИМПОРТЫ ИЗ КОНФИГА И УТИЛИТ ---
from config import TELEGRAM_BOT_TOKEN, TIMEZONE, RESERVATION_TIMEOUT, WARNING_TIMEOUT, SHEET_ID, CALENDAR_ID
from utils.safe_google import (
    safe_get_sheet_data,
    safe_append_to_sheet,
    safe_update_sheet_row,
    safe_get_calendar_events,
    safe_create_calendar_event,
    safe_update_calendar_event,
    safe_delete_calendar_event,
)
from utils.slots import find_available_slots
from utils.reminders import send_reminders, handle_confirm_reminder, handle_cancel_reminder
from utils.admin import load_admins, notify_admins
from utils.validation import validate_name, validate_phone
from utils.settings import load_settings_from_table

def safe_parse_price(p) -> str:
    """
    Безопасно парсит цену из строки: убирает всё, кроме цифр и точки,
    конвертирует в int, возвращает строку вида '1500 ₽' или 'цена не указана'.
    """
    if not p:
        return "цена не указана"
    try:
        import re
        clean = re.sub(r'[^\d.]', '', str(p).strip())
        if not clean:
            return "цена не указана"
        val = int(float(clean))
        return f"{val} ₽"
    except (ValueError, TypeError, OverflowError):
        return "цена не указана"

# --- GLOBALS ---
TRIGGER_WORDS = []
logger = logging.getLogger(__name__)

# --- RATE LIMITING ---
class RateLimiter:
    def __init__(self, max_requests: int = 15, window: int = 60):
        self.max_requests = max_requests
        self.window = window
        self.requests = {}

    def is_limited(self, user_id: int) -> bool:
        now = time.time()
        if user_id not in self.requests:
            self.requests[user_id] = []
        self.requests[user_id] = [req_time for req_time in self.requests[user_id] if now - req_time < self.window]
        if len(self.requests[user_id]) >= self.max_requests:
            return True
        self.requests[user_id].append(now)
        return False

rate_limiter = RateLimiter(max_requests=15, window=60)

# --- КЭШИРОВАНИЕ НАСТРОЕК С TTL ---
_settings_cache: Dict[str, Any] = {}
_settings_cache_timestamp: float = 0
_cache_lock = threading.Lock()
CACHE_TTL: int = 300  # 5 минут

def get_cached_settings() -> Dict[str, Any]:
    global _settings_cache, _settings_cache_timestamp
    now = time.time()
    if _settings_cache and (now - _settings_cache_timestamp) <= CACHE_TTL:
        return _settings_cache
    with _cache_lock:
        now = time.time()
        if not _settings_cache or (now - _settings_cache_timestamp) > CACHE_TTL:
            try:
                raw = safe_get_sheet_data(SHEET_ID, "Настройки!A3:B") or []
                _settings_cache = {str(row[0]).strip(): str(row[1]).strip() for row in raw if len(row) >= 2 and row[0] and row[1]}
                _settings_cache_timestamp = now
                missing = [k for k in ["Время начала работы", "Время окончания работы"] if k not in _settings_cache]
                if missing:
                    logger.warning(f"⚠️ Отсутствуют настройки: {missing}")
            except Exception as e:
                logger.error(f"❌ Ошибка загрузки настроек: {e}")
                if not _settings_cache:
                    _settings_cache = {}
                    _settings_cache_timestamp = now
        return _settings_cache

def get_setting(key: str, default: str = "") -> str:
    return get_cached_settings().get(key, default)

def invalidate_settings_cache():
    global _settings_cache, _settings_cache_timestamp
    with _cache_lock:
        _settings_cache = {}
        _settings_cache_timestamp = 0
        logger.info("🧹 Кэш настроек сброшен")

# --- КЭШИРОВАНИЕ УСЛУГ ---
_services_cache = None
_services_cache_timestamp = 0
SERVICES_CACHE_TTL = 300

def get_cached_services():
    global _services_cache, _services_cache_timestamp
    now = time.time()
    if _services_cache is None or (now - _services_cache_timestamp) > SERVICES_CACHE_TTL:
        try:
            _services_cache = safe_get_sheet_data(SHEET_ID, "Услуги!A3:G") or []
            _services_cache_timestamp = now
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки услуг: {e}")
            _services_cache = []
    return _services_cache

def calculate_service_step(subservice: str) -> int:
    services = get_cached_services()
    for row in services:
        if len(row) > 1 and row[1] == subservice:
            try:
                duration = int(row[2]) if row[2] else 0
                buffer = int(row[3]) if row[3] else 0
                return duration + buffer
            except (ValueError, TypeError):
                break
    return int(get_setting("Дефолтный шаг услуги", "60"))

def invalidate_services_cache():
    global _services_cache, _services_cache_timestamp
    _services_cache = None
    _services_cache_timestamp = 0

# --- LOGGING SETUP ---
def setup_production_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]')
    os.makedirs("logs", exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler('logs/bot.log', maxBytes=10*1024*1024, backupCount=5)
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    if not root.handlers:
        root.addHandler(file_handler)
        root.addHandler(console_handler)

# --- GLOBAL ERROR HANDLER ---
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)
    if update and hasattr(update, 'effective_message') and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "❌ Произошла техническая ошибка. Мы уже работаем над исправлением. Пожалуйста, попробуйте позже."
            )
        except Exception:
            pass
    try:
        await notify_admins(context, f"🚨 Критическая ошибка бота: {context.error}")
    except Exception:
        logger.exception("Не удалось уведомить админов")

# --- ACTIVITY / SESSIONS ---
async def update_last_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["_last_activity"] = time.time()

async def global_activity_updater(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_message and getattr(update.effective_message, "text", "") and not update.effective_message.text.startswith('/'):
        await update_last_activity(update, context)

async def cleanup_old_sessions_job(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    max_age = 30 * 24 * 60 * 60
    to_remove = [
        user_id for user_id, data in context.application.user_data.items()
        if now - data.get("_last_activity", now) > max_age
    ]
    for user_id in to_remove:
        try:
            del context.application.user_data[user_id]
        except KeyError:
            pass
    if to_remove:
        logger.info(f"🧹 Очищено {len(to_remove)} старых сессий")

# --- CLEANUP STUCK RESERVATIONS WITH WAITING LIST CHECK ---
async def cleanup_stuck_reservations_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        now = datetime.now(TIMEZONE)
        stuck_count = 0
        processed_slots = 0
        MAX_SLOTS = 50
        for user_id, user_data in list(context.application.user_data.items()):
            if processed_slots >= MAX_SLOTS:
                break
            if not isinstance(user_data, dict):
                continue
            temp_booking = user_data.get("temp_booking")
            if temp_booking and isinstance(temp_booking, dict):
                booking_time = temp_booking.get("created_at")
                if booking_time:
                    try:
                        booking_dt = datetime.fromisoformat(booking_time)
                        if (now - booking_dt).total_seconds() > 1800:
                            event_id = temp_booking.get("event_id")
                            if event_id:
                                safe_delete_calendar_event(CALENDAR_ID, event_id)
                            slot_date = temp_booking.get("date")
                            slot_time = temp_booking.get("time")
                            slot_specialist = temp_booking.get("specialist")
                            if slot_date and slot_time and slot_specialist:
                                await check_waiting_list(slot_date, slot_time, slot_specialist, context)
                                processed_slots += 1
                            if user_id in context.application.user_data:
                                del context.application.user_data[user_id]
                            stuck_count += 1
                    except (ValueError, TypeError):
                        pass
        if stuck_count:
            logger.info(f"🧹 Очищено {stuck_count} зависших бронирований, проверено {processed_slots} слотов")
    except Exception as e:
        logger.error(f"❌ Ошибка при очистке зависших бронирований: {e}")

# --- HEALTH CHECK ---
async def health_check_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        test_data = safe_get_sheet_data(SHEET_ID, "Настройки!A1:B1") or []
        test_events = safe_get_calendar_events(
            CALENDAR_ID,
            datetime.now(TIMEZONE).isoformat(),
            (datetime.now(TIMEZONE) + timedelta(hours=1)).isoformat()
        ) or []
        active_users = len(context.application.user_data)
        active_jobs = len(context.job_queue.jobs())
        logger.info(f"🏥 Health Check: Sheets={bool(test_data)}, Calendar={bool(test_events)}, Users={active_users}, Jobs={active_jobs}")
        log_business_event("health_check", sheets_connected=bool(test_data), calendar_connected=bool(test_events), active_users=active_users, active_jobs=active_jobs)
    except Exception as e:
        logger.error(f"❌ Health Check failed: {e}")
        await notify_admins(context, f"🚨 Health Check failed: {e}")

# --- LOCK FILE ---
def create_lock_file():
    lock_file = "bot.lock"
    if os.path.exists(lock_file):
        logger.critical("❌ Бот уже запущен! Файл bot.lock существует.")
        return False
    try:
        with open(lock_file, 'w') as f:
            f.write(str(os.getpid()))
        return True
    except Exception as e:
        logger.error(f"❌ Не удалось создать lock-файл: {e}")
        return False

def remove_lock_file():
    try:
        if os.path.exists("bot.lock"):
            os.remove("bot.lock")
            logger.info("🗑️ Lock-файл удалён.")
    except Exception as e:
        logger.error(f"❌ Не удалось удалить lock-файл: {e}")

# --- STATES ---
(
    MENU,
    SELECT_SERVICE_TYPE,
    SELECT_SUBSERVICE,
    SHOW_PRICE_INFO,
    SELECT_PRIORITY,
    SELECT_DATE,
    SELECT_SPECIALIST,
    SELECT_TIME,
    ENTER_NAME,
    ENTER_PHONE,
    CONFIRM_RESERVATION,
    MODIFY_RESERVATION,
    AWAITING_ADMIN_MESSAGE,
    AWAITING_REPEAT_CONFIRMATION,
    AWAITING_WAITING_LIST_DETAILS,
    AWAITING_ADMIN_SEARCH,
    AWAITING_MY_RECORDS_NAME,
    AWAITING_MY_RECORDS_PHONE,
    AWAITING_WL_CATEGORY,
    AWAITING_WL_SPECIALIST,
    AWAITING_WL_DATE,
    AWAITING_WL_TIME,
    AWAITING_WL_PRIORITY,
    AWAITING_CONFIRMATION,
    AWAITING_ADMIN_NEW_DATE,
    AWAITING_ADMIN_NEW_SPECIALIST,
    AWAITING_ADMIN_NEW_TIME,
    AWAITING_PHONE_FOR_CALLBACK,
) = range(28)

ACTIVE_STATUSES = {"подтверждено", "ожидает оплаты", "забронировано"}
CANCELLABLE_STATUSES = {"подтверждено", "ожидает оплаты", "забронировано"}

# --- HELPERS ---
def format_duration(minutes: int) -> str:
    if not isinstance(minutes, int) or minutes < 0:
        return "N/A"
    hours = minutes // 60
    mins = minutes % 60
    if hours == 0:
        return f"{mins} мин"
    elif mins == 0:
        return f"{hours} ч"
    else:
        return f"{hours} ч {mins} мин"

def validate_configuration():
    required = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "SHEET_ID": SHEET_ID,
        "CALENDAR_ID": CALENDAR_ID,
        "TIMEZONE": TIMEZONE
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        logger.critical(f"❌ Не заданы обязательные параметры: {', '.join(missing)}")
        return False
    if not all([RESERVATION_TIMEOUT, WARNING_TIMEOUT]):
        logger.critical("❌ Не заданы таймауты резервирования")
        return False
    logger.info("✅ Конфигурация проверена успешно")
    return True

def log_business_event(event_type, **kwargs):
    logger.info(f"BUSINESS_EVENT: {event_type} - {kwargs}")

def validate_time_format(time_str: str) -> bool:
    try:
        if not isinstance(time_str, str):
            return False
        datetime.strptime(time_str, "%H:%M")
        return True
    except ValueError:
        return False

def validate_work_schedule(work_time_str: str) -> bool:
    if not isinstance(work_time_str, str):
        return False
    if work_time_str.lower().strip() == "выходной":
        return True
    if "-" not in work_time_str:
        return False
    times = work_time_str.split("-")
    if len(times) != 2:
        return False
    return validate_time_format(times[0].strip()) and validate_time_format(times[1].strip())

def validate_date_format(date_str: str) -> bool:
    try:
        if not isinstance(date_str, str):
            return False
        if not date_str.count('.') == 2:
            return False
        datetime.strptime(date_str, "%d.%m.%Y")
        return True
    except ValueError:
        return False

# --- CHECK WAITING LIST (С ПОДДЕРЖКОЙ ПРИОРИТЕТА И БЛИЗКИХ СЛОТОВ) ---
async def check_waiting_list(slot_date: str, slot_time: str, specialist: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        MAX_DIFF = int(get_setting("Максимальное отклонение времени для листа ожидания", "30"))
        MAX_NOTIFY = int(get_setting("Максимальное количество уведомлений из листа ожидания", "1"))
        waiting_list = safe_get_sheet_data(SHEET_ID, "Лист ожидания!A3:L") or []
        candidates = []
        for idx, row in enumerate(waiting_list, start=2):
            if len(row) < 12:
                continue
            wait_date = str(row[7]).strip() if len(row) > 7 and row[7] else ""
            wait_time = str(row[8]).strip() if len(row) > 8 and row[8] else ""
            wait_specialist = str(row[6]).strip() if len(row) > 6 and row[6] else ""
            status = str(row[10]).strip() if len(row) > 10 and row[10] else ""
            chat_id = str(row[11]).strip() if len(row) > 11 and row[11] else ""
            priority = int(row[9]) if len(row) > 9 and row[9] and str(row[9]).isdigit() else 1
            if status == "ожидает" and chat_id.isdigit() and wait_date == slot_date and (wait_specialist == specialist or wait_specialist == "любой"):
                try:
                    slot_min = int(slot_time[:2]) * 60 + int(slot_time[3:5])
                    wait_min = int(wait_time[:2]) * 60 + int(wait_time[3:5])
                    diff = abs(slot_min - wait_min)
                    if diff <= MAX_DIFF:
                        candidates.append({
                            'priority': priority,
                            'diff': diff,
                            'idx': idx,
                            'row': row,
                            'chat_id': int(chat_id),
                            'req_time': wait_time
                        })
                except (ValueError, IndexError):
                    continue
        candidates.sort(key=lambda x: (-x['priority'], x['diff']))
        notified = 0
        for cand in candidates[:MAX_NOTIFY]:
            try:
                await context.bot.send_message(
                    chat_id=cand['chat_id'],
                    text=f"🎉 Появилось свободное время!\n📅 Дата: {slot_date}\n⏰ Время: {slot_time} (запрашивали {cand['req_time']})\n👩‍💼 Специалист: {specialist}\nНажмите /start для записи."
                )
                updated = list(cand['row'])
                updated[10] = "уведомлен"
                safe_update_sheet_row(SHEET_ID, "Лист ожидания", cand['idx'], updated)
                notified += 1
                logger.info(f"✅ Уведомлён клиент: {cand['chat_id']}, приоритет {cand['priority']}")
            except Exception as e:
                logger.error(f"❌ Ошибка уведомления: {e}")
        if notified:
            logger.info(f"📢 Уведомлено {notified} клиентов из листа ожидания")
    except Exception as e:
        logger.error(f"❌ Ошибка в check_waiting_list: {e}", exc_info=True)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ЗАПИСЕЙ ---
async def _display_records(update: Update, context: ContextTypes.DEFAULT_TYPE, records, title="Ваши активные записи:"):
    query = update.callback_query
    msg = f"📋 <b>{title}</b>\n\n"
    kb = []
    for r in records:
        rid = str(r[0]).strip() if len(r) > 0 else "N/A"
        svc = str(r[4]).strip() if len(r) > 4 else "N/A"
        cat = str(r[3]).strip() if len(r) > 3 else "N/A"
        mst = str(r[5]).strip() if len(r) > 5 else "N/A"
        dt = str(r[6]).strip() if len(r) > 6 else "N/A"
        tm = str(r[7]).strip() if len(r) > 7 else "N/A"
        st = str(r[8]).strip() if len(r) > 8 else "N/A"
        msg += f"<b>ID:</b> {rid}\n<b>Услуга:</b> {svc} ({cat})\n<b>Специалист:</b> {mst}\n<b>Дата:</b> {dt}\n<b>Время:</b> {tm}\n<b>Статус:</b> {st}\n"
        if st in CANCELLABLE_STATUSES:
            kb.append([InlineKeyboardButton(f"❌ Отменить {dt} {tm}", callback_data=f"cancel_record_{rid}")])
        else:
            msg += "<b>Действие:</b> Отмена невозможна\n"
        msg += "\n"
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="start")])
    rm = InlineKeyboardMarkup(kb)
    if query:
        await query.edit_message_text(msg, reply_markup=rm, parse_mode='HTML')
    else:
        await update.message.reply_text(msg, reply_markup=rm, parse_mode='HTML')

async def _validate_booking_checks(context: ContextTypes.DEFAULT_TYPE, name: str, phone: str, date_str: str, time_str: str, service_type: str):
    records = safe_get_sheet_data(SHEET_ID, "Записи!A3:O") or []
    try:
        new_start = TIMEZONE.localize(datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M"))
        new_end = new_start + timedelta(minutes=calculate_service_step(context.user_data.get("subservice", "default")))
    except ValueError:
        return False, "❌ Неверный формат даты/времени"
    for r in records:
        if len(r) > 7 and str(r[1]).strip() == name and str(r[2]).strip() == phone and str(r[8]).strip() == "подтверждено":
            rec_date = str(r[6]).strip()
            rec_time = str(r[7]).strip()
            try:
                rec_start = TIMEZONE.localize(datetime.strptime(f"{rec_date} {rec_time}", "%d.%m.%Y %H:%M"))
                rec_end = rec_start + timedelta(minutes=calculate_service_step(str(r[4]).strip()))
                if max(new_start, rec_start) < min(new_end, rec_end):
                    return False, f"❌ У вас уже есть запись на {rec_date} в {rec_time} к {str(r[5]).strip()} (услуга: {str(r[4]).strip()})."
            except ValueError:
                continue
    for r in records:
        if len(r) > 4 and str(r[1]).strip() == name and str(r[2]).strip() == phone and str(r[3]).strip() == service_type and str(r[8]).strip() == "подтверждено":
            context.user_data["repeat_booking_conflict"] = {
                "category": str(r[3]).strip(),
                "date": str(r[6]).strip(),
                "time": str(r[7]).strip(),
                "specialist": str(r[5]).strip()
            }
            return "CONFIRM_REPEAT", None
    return True, None

# --- HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update_last_activity(update, context)
    log_business_event("user_started", user_id=update.effective_user.id)

    greeting = get_setting("Текст приветствия", "Добро пожаловать!")
    schedule_text = "График работы не указан"
    org_name = get_setting("Название заведения", "").strip()
    if not org_name:
        schedule_text = "⚠️ Название заведения не задано в настройках"
    else:
        data = safe_get_sheet_data(SHEET_ID, "График специалистов!A3:I") or []
        found = False
        for row in data:
            if len(row) > 0 and str(row[0]).strip() == org_name:
                if len(row) > 3:
                    days = row[1] or "Пн-Вс"
                    start = row[2] or "09:00"
                    end = row[3] or "18:00"
                    schedule_text = f"{days} {start}–{end}"
                    found = True
                break
        if not found:
            schedule_text = f"❌ Расписание для '{org_name}' не найдено"
    kb = [
        [InlineKeyboardButton("📅 Записаться на приём", callback_data="book")],
        [InlineKeyboardButton("❌ Отменить или изменить запись", callback_data="modify")],
        [InlineKeyboardButton("📋 Мои записи", callback_data="my_records")],
        [InlineKeyboardButton("💅 Услуги и цены", callback_data="prices")],
        [InlineKeyboardButton("📞 Связаться с админом", callback_data="contact_admin")],
    ]
    rm = InlineKeyboardMarkup(kb)
    text = f"{greeting}\n\nМы работаем: {schedule_text}"
    if update.message:
        await update.message.reply_text(text, reply_markup=rm)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=rm)
    context.user_data["state"] = MENU
    return MENU

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await update_last_activity(update, context)
    data = query.data
    if data == "back":
        state = context.user_data.get("state")
        back_map = {
            SELECT_SUBSERVICE: select_service_type,
            SHOW_PRICE_INFO: select_subservice,
            SELECT_DATE: show_price_info,
            SELECT_SPECIALIST: show_price_info,
            SELECT_TIME: lambda u,c: select_date(u,c) if context.user_data.get("priority")=="date" else select_specialist(u,c),
            ENTER_NAME: select_time,
            ENTER_PHONE: enter_name,
        }
        if state in back_map:
            return await back_map[state](update, context)
        elif state in (CONFIRM_RESERVATION, AWAITING_REPEAT_CONFIRMATION):
            await query.edit_message_text("❌ Возврат невозможен. Подтвердите или отмените запись.")
            return
        elif state == AWAITING_WAITING_LIST_DETAILS:
            await start(update, context)
            return MENU
        elif state == AWAITING_ADMIN_SEARCH:
            return await handle_record_command(update, context)
        else:
            await start(update, context)
            return MENU
    if data == "start":
        await start(update, context)
        return MENU
    if data == "book":
        return await select_service_type(update, context)
    if data == "modify":
        await query.edit_message_text("❌ Возможность отменить/изменить запись пока недоступна через бота. Обратитесь к администратору.")
        return MENU
    if data == "my_records":
        return await show_my_records(update, context)
    if data == "prices":
        return await show_prices(update, context)
    if data == "contact_admin":
        await query.edit_message_text("Напишите ваше сообщение — администратор свяжется с вами.")
        context.user_data["state"] = AWAITING_ADMIN_MESSAGE
        return
    # АДМИНСКИЕ ФУНКЦИИ
    admin_handlers = {
        "admin_book_for_client": admin_book_for_client,
        "admin_manage_record": admin_manage_record,
        "admin_back": handle_record_command,
    }
    if data in admin_handlers:
        return await admin_handlers[data](update, context)
    if data.startswith("admin_cancel_"):
        return await admin_cancel_record(update, context, data.split("admin_cancel_", 1)[1])
    if data.startswith("admin_reschedule_"):
        return await admin_reschedule_record(update, context, data.split("admin_reschedule_", 1)[1])
    if data.startswith("admin_manage_"):
        return await admin_show_record_details(update, context, data.split("admin_manage_", 1)[1])
    if data.startswith("admin_new_date_"):
        return await admin_process_new_date(update, context, data.split("admin_new_date_", 1)[1])
    if data.startswith("admin_new_specialist_"):
        return await admin_process_new_specialist(update, context, data.split("admin_new_specialist_", 1)[1])
    if data.startswith("admin_new_slot_"):
        parts = data.split("admin_new_slot_", 1)[1].split("_", 1)
        if len(parts) == 2:
            return await admin_process_new_slot(update, context, parts[0], parts[1])
    if data in ["admin_change_date", "admin_change_specialist", "admin_change_time", "admin_change_all", "admin_skip_specialist"]:
        handler_map = {
            "admin_change_date": admin_change_date,
            "admin_change_specialist": admin_change_specialist,
            "admin_change_time": admin_change_time,
            "admin_change_all": admin_change_all,
            "admin_skip_specialist": admin_skip_specialist,
        }
        return await handler_map[data](update, context)
    if data.startswith("admin_confirm_reschedule_"):
        return await admin_confirm_reschedule(update, context, data.split("admin_confirm_reschedule_", 1)[1])
    if data.startswith("admin_force_reschedule_"):
        return await admin_force_reschedule(update, context, data.split("admin_force_reschedule_", 1)[1])
    if data.startswith("service_"):
        context.user_data["service_type"] = data.split("service_", 1)[1]
        return await select_subservice(update, context)
    if data.startswith("subservice_"):
        context.user_data["subservice"] = data.split("subservice_", 1)[1]
        return await show_price_info(update, context)
    if data.startswith("priority_"):
        context.user_data["priority"] = data.split("priority_", 1)[1]
        return await select_date(update, context)
    if data.startswith("date_"):
        context.user_data["date"] = data.split("date_", 1)[1]
        if context.user_data.get("priority") == "date":
            return await select_specialist(update, context)
        else:
            return await select_time(update, context)
    if data.startswith("specialist_"):
        context.user_data["selected_specialist"] = data.split("specialist_", 1)[1]
        if context.user_data.get("priority") == "specialist":
            return await select_date(update, context)
        else:
            return await select_time(update, context)
    if data.startswith("slot_"):
        parts = data.split("_", 2)
        if len(parts) == 3:
            return await reserve_slot(update, context, parts[1], parts[2])
        else:
            await query.edit_message_text("❌ Неверный формат слота.")
            return
    if data.startswith("confirm_reminder_"):
        await handle_confirm_reminder(data.split("confirm_reminder_", 1)[1], query, context)
        return
    if data.startswith("cancel_reminder_"):
        await handle_cancel_reminder(data.split("cancel_reminder_", 1)[1], query, context)
        return
    if data.startswith("cancel_record_"):
        return await cancel_record_from_list(update, context, data.split("cancel_record_", 1)[1])
    if data == "confirm":
        return await confirm_booking(update, context)
    if data == "cancel_reserve":
        return await cancel_reservation(update, context)
    if data == "confirm_repeat":
        return await finalize_booking(update, context)
    if data == "waiting_list":
        await query.edit_message_text(
            "📋 Чтобы встать в лист ожидания, уточните:\n"
            "1. Категорию и название услуги\n"
            "2. Имя cпециалиста (или 'любой')\n"
            "3. Желаемые дату и время"
        )
        context.user_data["state"] = AWAITING_WAITING_LIST_DETAILS
        return AWAITING_WAITING_LIST_DETAILS
    if data == "confirm_booking":
        return await finalize_booking(update, context)
    if data == "cancel_booking":
        await query.edit_message_text("❌ Запись отменена.")
        context.user_data.clear()
        return MENU
    await query.edit_message_text("❌ Неизвестная команда.")

# --- PRICES ---
async def show_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    services = safe_get_sheet_data(SHEET_ID, "Услуги!A3:G") or []
    text = "💅 УСЛУГИ И ЦЕНЫ\n\n"
    current_cat = None
    for row in services:
        if len(row) < 7:
            continue
        cat, name, dur_str, buf_str, _, price, desc = row[0], row[1], row[2], row[3], row[4], row[5], row[6]
        try:
            dur = int(dur_str)
            buf = int(buf_str)
        except Exception:
            logger.warning(f"⚠️ Неверный формат длительности/буфера в услуге {name}: {dur_str}, {buf_str}")
            continue
        if cat != current_cat:
            if current_cat is not None:
                text += "\n"
            text += f"\n<b>{cat.upper()}</b>:\n"
            current_cat = cat
        fmt_dur = format_duration(dur + buf)
        price_str = safe_parse_price(price)
        text += f"• <b>{name}</b> — {price_str} (длит.: {fmt_dur})\n"
        if desc:
            text += f" <i>{desc}</i>\n"
    await query.edit_message_text(text or "❌ Услуги не найдены.", parse_mode='HTML')
    try:
        await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="start")]]))
    except Exception:
        pass

# --- SELECT SERVICE TYPE ---
async def select_service_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    services = safe_get_sheet_data(SHEET_ID, "Услуги!A3:A") or []
    types = list({row[0] for row in services if row and len(row) > 0})
    kb = [[InlineKeyboardButton(t, callback_data=f"service_{t}")] for t in types]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
    await update.callback_query.edit_message_text("Выберите категорию услуги:", reply_markup=InlineKeyboardMarkup(kb))
    context.user_data["state"] = SELECT_SERVICE_TYPE
    return SELECT_SERVICE_TYPE

# --- SELECT SUBSERVICE ---
async def select_subservice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    st = context.user_data.get("service_type")
    if not st:
        await query.edit_message_text("❌ Ошибка: тип услуги не выбран.")
        return
    all_services = safe_get_sheet_data(SHEET_ID, "Услуги!A3:G") or []
    subs = [row[1] for row in all_services if len(row) > 1 and row[0] == st]
    kb = [[InlineKeyboardButton(s, callback_data=f"subservice_{s}")] for s in subs]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
    await query.edit_message_text(f"Выберите услугу ({st}):", reply_markup=InlineKeyboardMarkup(kb))
    context.user_data["state"] = SELECT_SUBSERVICE
    return SELECT_SUBSERVICE

# --- SHOW PRICE INFO ---
async def show_price_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    ss = context.user_data.get("subservice")
    if not ss:
        await query.edit_message_text("❌ Ошибка: услуга не выбрана.")
        return
    all_services = safe_get_sheet_data(SHEET_ID, "Услуги!A3:G") or []
    dur, buf, price = 60, 0, "не указана"
    for row in all_services:
        if len(row) > 1 and row[1] == ss:
            try:
                dur = int(row[2])
                buf = int(row[3])
            except Exception:
                pass
            price = row[5] if len(row) > 5 else "не указана"
            break
    fmt_dur = format_duration(dur + buf)
    price_str = safe_parse_price(price)
    text = f"✅ Услуга: {ss}\n💰 Цена: {price_str}\n⏳ Длительность: {fmt_dur}\n\nЧто для вас важнее?"
    kb = [
        [InlineKeyboardButton("📅 Сначала дата", callback_data="priority_date")],
        [InlineKeyboardButton("👩‍🦰 Сначала cпециалист", callback_data="priority_specialist")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    context.user_data["state"] = SHOW_PRICE_INFO
    return SHOW_PRICE_INFO

# --- SELECT DATE ---
async def select_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TIMEZONE).date()
    priority = context.user_data.get("priority", "date")
    st = context.user_data.get("service_type")
    ss = context.user_data.get("subservice")
    specialist = context.user_data.get("selected_specialist")
    dates = set()
    for i in range(1, 11):
        d = (today + timedelta(days=i)).strftime("%d.%m.%Y")
        slots = find_available_slots(st, ss, d, specialist, priority)
        if slots:
            dates.add(d)
    kb = [[InlineKeyboardButton(d, callback_data=f"date_{d}")] for d in sorted(dates)]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
    await update.callback_query.edit_message_text("Выберите дату:", reply_markup=InlineKeyboardMarkup(kb))
    context.user_data["state"] = SELECT_DATE
    return SELECT_DATE

# --- SELECT SPECIALIST ---
async def select_specialist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    date_str = context.user_data.get("date")
    if not date_str:
        await query.edit_message_text("❌ Ошибка: дата не выбрана.")
        return
    specialists_data = safe_get_sheet_data(SHEET_ID, "График специалистов!A3:I") or []
    available = []
    try:
        target = datetime.strptime(date_str, "%d.%m.%Y")
        day_name = target.strftime("%a")
        short_map = {"Mon": "Пн", "Tue": "Вт", "Wed": "Ср", "Thu": "Чт", "Fri": "Пт", "Sat": "Сб", "Sun": "Вс"}
        target_day = short_map.get(day_name)
        if not target_day:
            await query.edit_message_text("❌ Ошибка: невозможно определить день недели.")
            return
    except Exception:
        await query.edit_message_text("❌ Ошибка: неверный формат даты.")
        return

    selected_service_type = context.user_data.get("service_type")
    if not selected_service_type:
        await query.edit_message_text("❌ Ошибка: категория услуги не выбрана.")
        return

    for row in specialists_data:
        if len(row) > 0 and row[0] != get_setting("Название заведения", "Название организации"):
            name = row[0]
            # --- НОВАЯ ПРОВЕРКА КАТЕГОРИИ СПЕЦИАЛИСТА ---
            specialist_categories = str(row[1]).strip() if len(row) > 1 else ""
            if specialist_categories and selected_service_type:
                if selected_service_type not in [cat.strip() for cat in specialist_categories.split(",")]:
                    continue  # cпециалист не подходит под выбранную категорию
            # ---------------------------------------
            try:
                col_idx = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].index(target_day) + 1
            except ValueError:
                continue
            if col_idx >= len(row):
                continue
            work_time = row[col_idx]
            if validate_work_schedule(work_time):
                available.append(name)
    kb = [[InlineKeyboardButton(m, callback_data=f"specialist_{m}")] for m in available]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
    await query.edit_message_text("Выберите специалиста:", reply_markup=InlineKeyboardMarkup(kb))
    context.user_data["state"] = SELECT_SPECIALIST
    return SELECT_SPECIALIST

# --- SELECT TIME ---
async def select_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    date_str = context.user_data.get("date")
    specialist = context.user_data.get("selected_specialist")
    st = context.user_data.get("service_type")
    ss = context.user_data.get("subservice")
    if not all([date_str, st, ss]):
        await query.edit_message_text("❌ Ошибка: не все данные для выбора времени выбраны.")
        return
    slots = find_available_slots(st, ss, date_str, specialist, context.user_data.get("priority", "date"))
    if not slots:
        await query.edit_message_text("❌ Свободных слотов нет.")
        kb = [
            [InlineKeyboardButton("📋 В лист ожидания", callback_data="waiting_list")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back")]
        ]
        try:
            await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            pass
        return
    kb = []
    for s in slots:
        t = s.get("time", "N/A")
        m = s.get("specialist", "N/A")
        kb.append([InlineKeyboardButton(f"{t} — {m}", callback_data=f"slot_{m}_{t}")])
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
    await query.edit_message_text("Выберите время:", reply_markup=InlineKeyboardMarkup(kb))
    context.user_data["state"] = SELECT_TIME
    return SELECT_TIME

# --- RESERVE SLOT ---
async def reserve_slot(update: Update, context: ContextTypes.DEFAULT_TYPE, specialist: str, time_str: str):
    query = update.callback_query
    date_str = context.user_data.get("date")
    ss = context.user_data.get("subservice")
    step = calculate_service_step(ss)
    dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
    start_dt = TIMEZONE.localize(dt)
    end_dt = start_dt + timedelta(minutes=step)
    event_id = safe_create_calendar_event(
        CALENDAR_ID, "⏳ Бронь (в процессе)", start_dt.isoformat(), end_dt.isoformat(), "7",
        f"Бронь: {ss} к {specialist}. В процессе оформления..."
    )
    context.user_data["temp_booking"] = {
        "specialist": specialist,
        "time": time_str,
        "date": date_str,
        "event_id": event_id,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "subservice": ss,
        "created_at": datetime.now(TIMEZONE).isoformat()
    }
    context.job_queue.run_once(
        release_reservation, RESERVATION_TIMEOUT,
        chat_id=update.effective_chat.id,
        name=f"reservation_timeout_{update.effective_chat.id}",
        data={"user_id": update.effective_user.id}
    )
    context.job_queue.run_once(
        warn_reservation, WARNING_TIMEOUT,
        chat_id=update.effective_chat.id,
        name=f"reservation_warn_{update.effective_chat.id}",
        data={"user_id": update.effective_user.id}
    )
    await query.edit_message_text("⏳ Слот зарезервирован! Введите ваше имя:")
    context.user_data["state"] = ENTER_NAME
    return ENTER_NAME

# --- WARN / RELEASE RESERVATION ---
async def warn_reservation(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    uid = job.data.get("user_id") if job.data else None
    if not uid:
        logger.error("❌ warn_reservation: Не удалось получить user_id")
        return
    try:
        await context.bot.send_message(job.chat_id, "⏳ Не забудьте подтвердить запись — осталось немного времени!")
        logger.info(f"📤 Предупреждение отправлено (chat_id: {job.chat_id})")
    except Exception as e:
        logger.error(f"❌ Ошибка предупреждения: {e}")

async def release_reservation(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    uid = job.data.get("user_id") if job.data else None
    if not uid:
        logger.error("❌ release_reservation: Не удалось получить user_id")
        return
    user_data = context.application.user_data.get(uid, {})
    temp = user_data.get("temp_booking") if isinstance(user_data, dict) else None
    if temp and temp.get("event_id"):
        try:
            safe_delete_calendar_event(CALENDAR_ID, temp["event_id"])
            logger.info(f"Резерв слота {temp['date']} {temp['time']} освобождён по таймауту для пользователя {uid}.")
            await check_waiting_list(temp['date'], temp['time'], temp['specialist'], context)
        except Exception as e:
            logger.error(f"❌ Ошибка освобождения резерва: {e}")
        try:
            await context.bot.send_message(job.chat_id, "❌ Слот был освобождён из-за неактивности. Вы можете начать запись заново.")
        except Exception:
            pass
    if uid in context.application.user_data:
        context.application.user_data[uid].clear()

# --- ENTER NAME / PHONE ---
async def enter_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != ENTER_NAME:
        return
    name = (update.message.text or "").strip()
    if not validate_name(name):
        await update.message.reply_text("❌ Неверный формат имени. Используйте только буквы, длиной 2-30 символов, максимум один дефис.")
        return ENTER_NAME
    context.user_data["name"] = name
    await update.message.reply_text("📞 Теперь введите ваш телефон:", reply_markup=ReplyKeyboardRemove())
    context.user_data["state"] = ENTER_PHONE
    return ENTER_PHONE

async def enter_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("state") != ENTER_PHONE:
        return
    phone = (update.message.text or "").strip()
    if not validate_phone(phone):
        await update.message.reply_text("❌ Неверный формат номера телефона. Введите номер длиной 10-15 цифр.")
        return ENTER_PHONE
    context.user_data["phone"] = phone
    await update.message.reply_text(
        "📋 Пожалуйста, подтвердите запись:\n\n"
        f"Услуга: {context.user_data.get('subservice', 'N/A')} ({context.user_data.get('service_type', 'N/A')})\n"
        f"Специалист: {context.user_data.get('selected_specialist', 'N/A')}\n"
        f"Дата: {context.user_data.get('date', 'N/A')}\n"
        f"Время: {context.user_data.get('time', 'N/A')}\n"
        f"Имя: {context.user_data.get('name', 'N/A')}\n"
        f"Телефон: {context.user_data.get('phone', 'N/A')}\n\n"
        "Всё верно?"
    )
    kb = [
        [InlineKeyboardButton("✅ Подтвердить запись", callback_data="confirm_booking")],
        [InlineKeyboardButton("❌ Отменить", callback_data="cancel_booking")]
    ]
    await update.message.reply_text("Выберите действие:", reply_markup=InlineKeyboardMarkup(kb))
    context.user_data["state"] = AWAITING_CONFIRMATION
    return AWAITING_CONFIRMATION

# --- FINALIZE BOOKING ---
async def finalize_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    st = context.user_data.get("service_type")
    ss = context.user_data.get("subservice")
    specialist = context.user_data.get("selected_specialist")
    date_str = context.user_data.get("date")
    time_str = context.user_data.get("time")
    name = context.user_data.get("name")
    phone = context.user_data.get("phone")
    if not all([st, ss, specialist, date_str, time_str, name, phone]):
        await query.edit_message_text("❌ Не все данные для записи заполнены. Пожалуйста, начните сначала.")
        context.user_data.clear()
        return MENU
    check_result, error_msg = await _validate_booking_checks(context, name, phone, date_str, time_str, st)
    if check_result is False:
        temp = context.user_data.get("temp_booking")
        if temp and temp.get("event_id"):
            try:
                safe_delete_calendar_event(CALENDAR_ID, temp["event_id"])
            except Exception as e:
                logger.error(f"❌ Ошибка удаления события при отмене: {e}")
        await query.edit_message_text(error_msg)
        context.user_data.clear()
        return MENU
    elif check_result == "CONFIRM_REPEAT":
        conflict = context.user_data.get("repeat_booking_conflict", {})
        kb = [
            [InlineKeyboardButton("✅ Да, хочу", callback_data="confirm_repeat")],
            [InlineKeyboardButton("❌ Отменить", callback_data="start")]
        ]
        msg = (
            f"⚠️ У вас уже есть запись на <b>{conflict.get('category', 'N/A')}</b>\n"
            f"{conflict.get('date', 'N/A')} в {conflict.get('time', 'N/A')} к {conflict.get('specialist', 'N/A')}.\n\n"
            "Вы уверены, что хотите записаться ещё раз?"
        )
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        context.user_data["state"] = AWAITING_REPEAT_CONFIRMATION
        return AWAITING_REPEAT_CONFIRMATION
    temp = context.user_data.get("temp_booking")
    event_id = temp.get("event_id") if temp else None
    if not event_id:
        step = calculate_service_step(ss)
        dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
        start_dt = TIMEZONE.localize(dt)
        end_dt = start_dt + timedelta(minutes=step)
        event_id = safe_create_calendar_event(
            CALENDAR_ID, f"{name} - {ss}", start_dt.isoformat(), end_dt.isoformat(), "10",
            f"Клиент: {name}, тел.: {phone}"
        )
    else:
        safe_update_calendar_event(CALENDAR_ID, event_id, f"{name} - {ss}", "10", f"Клиент: {name}, тел.: {phone}")
    record_id = f"ЗАП-{len(safe_get_sheet_data(SHEET_ID, 'Записи!A:A') or []) + 1:03d}"
    new_record = [
        record_id,
        name,
        phone,
        st,
        ss,
        specialist,
        date_str,
        time_str,
        "подтверждено",
        datetime.now(TIMEZONE).strftime("%d.%m.%Y %H:%M"),
        "",
        "❌",
        "❌",
        str(update.effective_chat.id),
        event_id
    ]
    safe_append_to_sheet(SHEET_ID, "Записи", [new_record])
    context.user_data.clear()
    success = (
        f"✅ Вы записаны!\nУслуга: {ss}\nСпециалист: {specialist}\nДата: {date_str}\nВремя: {time_str}\n"
        f"Стоимость: {get_setting('Стоимость', 'уточняйте')}"
    )
    await query.edit_message_text(success)
    admin_msg = f"📢 Новая запись: <b>{ss}</b> к <b>{specialist}</b> {date_str} в {time_str} — <b>{name}</b>"
    await notify_admins(context, admin_msg)
    logger.info(f"✅ Новая запись: {name} ({phone}) -> {ss} ({date_str} {time_str})")
    return MENU

# --- CONFIRM / CANCEL BOOKING ---
async def confirm_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await finalize_booking(update, context)

async def cancel_reservation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    temp = context.user_data.get("temp_booking")
    if temp and temp.get("event_id"):
        try:
            safe_delete_calendar_event(CALENDAR_ID, temp["event_id"])
            logger.info(f"Резерв слота {temp['date']} {temp['time']} отменён вручную.")
            await check_waiting_list(temp['date'], temp['time'], temp['specialist'], context)
        except Exception as e:
            logger.error(f"❌ Ошибка при отмене резерва: {e}")
    await query.edit_message_text("❌ Резерв отменён. Слот освобождён.")
    context.user_data.clear()

# --- SHOW MY RECORDS ---
async def show_my_records(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    user_id = update.effective_user.id
    name = context.user_data.get("name")
    phone = context.user_data.get("phone")
    records = safe_get_sheet_data(SHEET_ID, "Записи!A3:O") or []
    found = []
    for r in records:
        if len(r) > 13 and str(r[13]).strip() == str(user_id) and str(r[8]).strip() in ACTIVE_STATUSES:
            found.append(r)
    if not found and name and phone:
        for r in records:
            if len(r) > 2 and str(r[1]).strip() == name and str(r[2]).strip() == phone and str(r[8]).strip() in ACTIVE_STATUSES:
                found.append(r)
    if not found:
        if not name or not phone:
            await update.message.reply_text("🔍 Я не нашёл ваши записи. Пожалуйста, введите ваше имя:")
            context.user_data["state"] = AWAITING_MY_RECORDS_NAME
            return AWAITING_MY_RECORDS_NAME
        else:
            await (query.edit_message_text if query else update.message.reply_text)("📋 У вас нет активных записей.")
            return MENU
    await _display_records(update, context, found, "Ваши активные записи:")
    return MENU

# --- CANCEL RECORD FROM LIST ---
async def cancel_record_from_list(update: Update, context: ContextTypes.DEFAULT_TYPE, record_id: str):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)
    records = safe_get_sheet_data(SHEET_ID, "Записи!A3:O") or []
    for idx, r in enumerate(records, start=2):
        if len(r) > 0 and r[0] == record_id:
            if len(r) > 13 and str(r[13]).strip() != chat_id:
                await query.edit_message_text("❌ Вы не можете отменить эту запись.")
                return
            event_id = r[14] if len(r) > 14 else None
            if event_id:
                safe_delete_calendar_event(CALENDAR_ID, event_id)
            updated = list(r)
            updated[8] = "отменено клиентом"
            safe_update_sheet_row(SHEET_ID, "Записи", idx, updated)
            await query.edit_message_text(f"✅ Запись {record_id} отменена.")
            if len(r) > 6 and len(r) > 7 and len(r) > 5:
                await check_waiting_list(str(r[6]).strip(), str(r[7]).strip(), str(r[5]).strip(), context)
            logger.info(f"✅ Клиент {chat_id} отменил запись {record_id}")
            return
    await query.edit_message_text("❌ Запись не найдена.")

# --- HANDLE MY RECORDS INPUT ---
async def handle_my_records_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")
    if state == AWAITING_MY_RECORDS_NAME:
        name = update.message.text.strip()
        if not name:
            await update.message.reply_text("❌ Имя не может быть пустым.")
            return AWAITING_MY_RECORDS_NAME
        context.user_data["temp_my_records_name"] = name
        await update.message.reply_text("📞 Теперь введите ваш номер телефона (только цифры, не менее 10).")
        context.user_data["state"] = AWAITING_MY_RECORDS_PHONE
        return AWAITING_MY_RECORDS_PHONE
    elif state == AWAITING_MY_RECORDS_PHONE:
        phone = update.message.text.strip()
        if not validate_phone(phone):
            await update.message.reply_text("❌ Неверный формат телефона. Введите не менее 10 цифр.")
            return AWAITING_MY_RECORDS_PHONE
        name = context.user_data.get("temp_my_records_name")
        records = safe_get_sheet_data(SHEET_ID, "Записи!A3:O") or []
        found = []
        for r in records:
            if len(r) > 2 and str(r[1]).strip() == name and str(r[2]).strip() == phone and str(r[8]).strip() in ACTIVE_STATUSES:
                found.append(r)
        if found:
            await _display_records(update, context, found, "Ваши активные записи (по введённым данным):")
        else:
            await update.message.reply_text("❌ Записей с такими данными не найдено.")
        context.user_data.pop("temp_my_records_name", None)
        context.user_data.pop("state", None)
        return MENU
    return MENU

# --- WAITING LIST INPUT ---
async def handle_waiting_list_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_input = (msg.text or "").strip()
    state = context.user_data.get("state")
    if state == AWAITING_WAITING_LIST_DETAILS:
        required = ["service_type", "subservice"]
        missing = [f for f in required if not context.user_data.get(f)]
        if missing:
            await msg.reply_text("📋 Вы в листе ожидания.\nПожалуйста, укажите категорию услуги.")
            context.user_data["state"] = AWAITING_WL_CATEGORY
            return AWAITING_WL_CATEGORY
        else:
            service_type = context.user_data.get("service_type", "")
            subservice = context.user_data.get("subservice", "")
            specialist = context.user_data.get("selected_specialist", "любой")
            date = context.user_data.get("date", "")
            time = context.user_data.get("time", "")
            entry = [
                f"WAIT-{int(time.time())}",
                datetime.now(TIMEZONE).strftime("%d.%m.%Y %H:%M"),
                update.effective_user.full_name or "Не указано",
                context.user_data.get("phone", ""),
                service_type,
                subservice,
                specialist,
                date,
                time,
                "1",
                "ожидает",
                str(update.effective_chat.id)
            ]
            try:
                safe_append_to_sheet(SHEET_ID, "Лист ожидания!A3:L", [entry])
                confirmation = (
                    "📋 Спасибо! Ваши данные сохранены в листе ожидания.\n\n"
                    f"<b>Основные данные:</b>\n• Услуга: {subservice} ({service_type})\n• Специалист: {specialist}\n"
                )
                if date and time:
                    confirmation += f"• Предпочтительное время: {date} в {time}\n"
                else:
                    confirmation += "• Предпочтительное время: не указано\n"
                confirmation += "\nМы уведомим вас, когда появится подходящее время."
                await msg.reply_text(confirmation, parse_mode='HTML')
                logger.info(f"✅ Добавлена запись в лист ожидания для chat_id {update.effective_chat.id}")
            except Exception as e:
                logger.error(f"❌ Ошибка добавления в лист ожидания: {e}")
                await msg.reply_text("❌ Произошла ошибка при сохранении в лист ожидания. Попробуйте позже.")
            context.user_data.clear()
            context.user_data["state"] = MENU
            return MENU
    elif state == AWAITING_WL_CATEGORY:
        if not user_input:
            await msg.reply_text("❌ Категория не может быть пустой.")
            return AWAITING_WL_CATEGORY
        context.user_data["wl_category"] = user_input
        await msg.reply_text(f"👤 Вы выбрали категорию: <b>{user_input}</b>.\nТеперь укажите имя специалиста (или 'любой').", parse_mode='HTML')
        context.user_data["state"] = AWAITING_WL_SPECIALIST
        return AWAITING_WL_SPECIALIST
    elif state == AWAITING_WL_SPECIALIST:
        if not user_input:
            await msg.reply_text("❌ Имя специалиста не может быть пустым.")
            return AWAITING_WL_SPECIALIST
        context.user_data["wl_specialist"] = user_input
        await msg.reply_text(f"👤 Специалист: <b>{user_input}</b>.\nТеперь укажите желаемую дату (ДД.ММ.ГГГГ).", parse_mode='HTML')
        context.user_data["state"] = AWAITING_WL_DATE
        return AWAITING_WL_DATE
    elif state == AWAITING_WL_DATE:
        if not validate_date_format(user_input):
            await msg.reply_text("❌ Неверный формат даты. Введите ДД.ММ.ГГГГ.")
            return AWAITING_WL_DATE
        context.user_data["wl_date"] = user_input
        await msg.reply_text(f"📅 Дата: <b>{user_input}</b>.\nТеперь укажите желаемое время (ЧЧ:ММ).", parse_mode='HTML')
        context.user_data["state"] = AWAITING_WL_TIME
        return AWAITING_WL_TIME
    elif state == AWAITING_WL_TIME:
        if not validate_time_format(user_input):
            await msg.reply_text("❌ Неверный формат времени. Введите ЧЧ:ММ.")
            return AWAITING_WL_TIME
        context.user_data["wl_time"] = user_input
        await msg.reply_text(f"⏰ Время: <b>{user_input}</b>.\nТеперь укажите приоритет (например, 'раньше', 'позже', 'около').", parse_mode='HTML')
        context.user_data["state"] = AWAITING_WL_PRIORITY
        return AWAITING_WL_PRIORITY
    elif state == AWAITING_WL_PRIORITY:
        if not user_input:
            await msg.reply_text("❌ Приоритет не может быть пустым.")
            return AWAITING_WL_PRIORITY
        context.user_data["wl_priority"] = user_input
        sheet_data = [
            f"WAIT-{int(time.time())}",
            datetime.now(TIMEZONE).strftime("%d.%m.%Y %H:%M"),
            update.effective_user.full_name or "Не указано",
            context.user_data.get("phone", "Неизвестен"),
            context.user_data["wl_category"],
            context.user_data.get("wl_service", "Любая в категории"),
            context.user_data["wl_specialist"],
            context.user_data["wl_date"],
            context.user_data["wl_time"],
            context.user_data["wl_priority"],
            "ожидает",
            str(update.effective_user.id)
        ]
        try:
            safe_append_to_sheet(SHEET_ID, "Лист ожидания!A3:L", [sheet_data])
            await msg.reply_text(
                f"✅ Вы добавлены в лист ожидания!\nКатегория: {context.user_data['wl_category']}\n"
                f"Специалист: {context.user_data['wl_specialist']}\nДата: {context.user_data['wl_date']}\n"
                f"Время: {context.user_data['wl_time']}\nПриоритет: {context.user_data['wl_priority']}\nСтатус: ожидает"
            )
            logger.info(f"✅ Клиент {update.effective_user.id} добавлен в лист ожидания: {sheet_data}")
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения в лист ожидания: {e}")
            await msg.reply_text("❌ Ошибка при сохранении в лист ожидания. Повторите попытку позже.")
        for key in ["wl_category", "wl_service", "wl_specialist", "wl_date", "wl_time", "wl_priority"]:
            context.user_data.pop(key, None)
        context.user_data.pop("state", None)
        return MENU
    else:
        await msg.reply_text("❌ Произошла ошибка. Пожалуйста, начните снова.")
        context.user_data.pop("state", None)
        return MENU

# --- АДМИНИСТРАТИВНЫЕ ФУНКЦИИ ---
async def handle_record_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    admins = load_admins() or []
    if not any(str(a) == user_id for a in admins):
        msg = "❌ У вас нет прав администратора."
        if update.message:
            await update.message.reply_text(msg)
        elif update.callback_query:
            await update.callback_query.edit_message_text(msg)
        return
    context.user_data.clear()
    context.user_data["admin_mode"] = True
    kb = [
        [InlineKeyboardButton("📅 Записать клиента", callback_data="admin_book_for_client")],
        [InlineKeyboardButton("🔍 Найти/управлять записью", callback_data="admin_manage_record")],
        [InlineKeyboardButton("➡️ В главное меню", callback_data="start")]
    ]
    text = "👨‍💼 Режим администратора. Выберите действие:"
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def admin_book_for_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data["admin_mode"] = True
    await query.edit_message_text("👨‍💼 Режим записи за клиента. Начните процесс записи:")
    return await select_service_type(update, context)

async def admin_manage_record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text("Введите имя или телефон клиента для поиска записей:")
    context.user_data["state"] = AWAITING_ADMIN_SEARCH
    return AWAITING_ADMIN_SEARCH

async def handle_admin_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    term = update.message.text.strip()
    records = safe_get_sheet_data(SHEET_ID, "Записи!A3:O") or []
    found = []
    for r in records:
        if len(r) >= 3:
            name = r[1]
            phone = r[2]
            if term.lower() in name.lower() or term in phone.replace(" ", "").replace("-", ""):
                found.append(r)
    if not found:
        await update.message.reply_text("❌ Записи не найдены.")
        return AWAITING_ADMIN_SEARCH
    kb = []
    for r in found[:10]:
        rid = r[0]
        svc = r[4] if len(r) > 4 else "N/A"
        dt = r[6] if len(r) > 6 else "N/A"
        tm = r[7] if len(r) > 7 else "N/A"
        st = r[8] if len(r) > 8 else "N/A"
        kb.append([InlineKeyboardButton(f"{rid} | {dt} {tm} | {svc} | {st}", callback_data=f"admin_manage_{rid}")])
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_back")])
    await update.message.reply_text(f"📋 Найдено записей: {len(found)}\nВыберите запись для управления:", reply_markup=InlineKeyboardMarkup(kb))

async def admin_show_record_details(update: Update, context: ContextTypes.DEFAULT_TYPE, record_id: str):
    query = update.callback_query
    records = safe_get_sheet_data(SHEET_ID, "Записи!A3:O") or []
    for r in records:
        if len(r) > 0 and r[0] == record_id:
            info = (
                f"📋 Детали записи {record_id}:\n"
                f"👤 Клиент: {r[1] if len(r) > 1 else 'N/A'}\n"
                f"📞 Телефон: {r[2] if len(r) > 2 else 'N/A'}\n"
                f"📁 Категория: {r[3] if len(r) > 3 else 'N/A'}\n"
                f"💅 Услуга: {r[4] if len(r) > 4 else 'N/A'}\n"
                f"👩‍🦰 Специалист: {r[5] if len(r) > 5 else 'N/A'}\n"
                f"📅 Дата: {r[6] if len(r) > 6 else 'N/A'}\n"
                f"⏰ Время: {r[7] if len(r) > 7 else 'N/A'}\n"
                f"📊 Статус: {r[8] if len(r) > 8 else 'N/A'}\n"
                f"🆔 Chat ID: {r[13] if len(r) > 13 else 'N/A'}"
            )
            kb = [
                [InlineKeyboardButton("❌ Отменить запись", callback_data=f"admin_cancel_{record_id}")],
                [InlineKeyboardButton("🔄 Перенести запись", callback_data=f"admin_reschedule_{record_id}")],
                [InlineKeyboardButton("⬅️ Назад к поиску", callback_data="admin_manage_record")]
            ]
            await query.edit_message_text(info, reply_markup=InlineKeyboardMarkup(kb))
            return
    await query.edit_message_text("❌ Запись не найдена.")

async def admin_cancel_record(update: Update, context: ContextTypes.DEFAULT_TYPE, record_id: str):
    query = update.callback_query
    records = safe_get_sheet_data(SHEET_ID, "Записи!A3:O") or []
    for idx, r in enumerate(records, start=2):
        if len(r) > 0 and r[0] == record_id:
            event_id = r[14] if len(r) > 14 else None
            if event_id:
                safe_delete_calendar_event(CALENDAR_ID, event_id)
            updated = list(r)
            updated[8] = "отменено админом"
            safe_update_sheet_row(SHEET_ID, "Записи", idx, updated)
            await query.edit_message_text(f"✅ Запись {record_id} отменена администратором.")
            chat_id = r[13] if len(r) > 13 else None
            if chat_id:
                try:
                    await context.bot.send_message(chat_id, f"❌ Ваша запись {record_id} была отменена администратором.")
                except Exception:
                    pass
            if len(r) > 6 and len(r) > 7 and len(r) > 5:
                await check_waiting_list(str(r[6]).strip(), str(r[7]).strip(), str(r[5]).strip(), context)
            return
    await query.edit_message_text("❌ Запись не найдена.")

# --- ADMIN RESCHEDULE RECORD (ПОЛНАЯ РЕАЛИЗАЦИЯ) ---
async def admin_reschedule_record(update: Update, context: ContextTypes.DEFAULT_TYPE, record_id: str):
    query = update.callback_query
    await query.answer()
    context.user_data["admin_reschedule_record_id"] = record_id
    context.user_data["admin_mode"] = True
    records = safe_get_sheet_data(SHEET_ID, "Записи!A3:O") or []
    current = None
    for r in records:
        if len(r) > 0 and r[0] == record_id:
            current = r
            break
    if not current:
        await query.edit_message_text("❌ Запись не найдена.")
        return
    for i, key in enumerate(["service_type", "subservice", "current_specialist", "current_date", "current_time"]):
        if len(current) > i + 3:
            context.user_data[key] = str(current[i + 3]).strip()
    msg = (
        f"🔄 Перенос записи {record_id}\n\n<b>Текущие данные:</b>\n"
        f"• Услуга: {current[4] if len(current) > 4 else 'N/A'}\n"
        f"• Специалист: {current[5] if len(current) > 5 else 'N/A'}\n"
        f"• Дата: {current[6] if len(current) > 6 else 'N/A'}\n"
        f"• Время: {current[7] if len(current) > 7 else 'N/A'}\n"
        f"• Клиент: {current[1] if len(current) > 1 else 'N/A'}\n\n"
        "Выберите что изменить:"
    )
    kb = [
        [InlineKeyboardButton("📅 Изменить дату", callback_data="admin_change_date")],
        [InlineKeyboardButton("👩‍💼 Изменить специалиста", callback_data="admin_change_specialist")],
        [InlineKeyboardButton("⏰ Изменить время", callback_data="admin_change_time")],
        [InlineKeyboardButton("✅ Перенести всё сразу", callback_data="admin_change_all")],
        [InlineKeyboardButton("⬅️ Назад к записи", callback_data=f"admin_manage_{record_id}")]
    ]
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def admin_change_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    today = datetime.now(TIMEZONE).date()
    dates = [(today + timedelta(days=i)).strftime("%d.%m.%Y") for i in range(1, 11)]
    kb = [[InlineKeyboardButton(d, callback_data=f"admin_new_date_{d}")] for d in dates]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"admin_manage_{context.user_data.get('admin_reschedule_record_id', '')}")])
    await query.edit_message_text("📅 Выберите новую дату для записи:", reply_markup=InlineKeyboardMarkup(kb))

async def admin_change_specialist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    specialists_data = safe_get_sheet_data(SHEET_ID, "График специалистов!A3:I") or []
    specialists = [row[0] for row in specialists_data if len(row) > 0 and row[0] != get_setting("Название заведения", "Название организации")]
    kb = [[InlineKeyboardButton(m, callback_data=f"admin_new_specialist_{m}")] for m in specialists]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"admin_manage_{context.user_data.get('admin_reschedule_record_id', '')}")])
    await query.edit_message_text("👩‍💼 Выберите нового cпециалиста:", reply_markup=InlineKeyboardMarkup(kb))

async def admin_change_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    st = context.user_data.get("service_type")
    ss = context.user_data.get("subservice")
    date_str = context.user_data.get("current_date")
    specialist = context.user_data.get("current_specialist")
    if not all([st, ss, date_str]):
        await query.edit_message_text("❌ Недостаточно данных для поиска времени.")
        return
    slots, err = await _get_available_slots_for_admin(st, ss, date_str, specialist)
    if err:
        await query.edit_message_text(err)
        return
    if not slots:
        await query.edit_message_text(f"❌ Нет доступных слотов для {specialist} на {date_str}.")
        return
    kb = [[InlineKeyboardButton(f"⏰ {s}", callback_data=f"admin_new_slot_{specialist}_{s}")] for s in slots]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data=f"admin_manage_{context.user_data.get('admin_reschedule_record_id', '')}")])
    await query.edit_message_text(
        f"📅 Дата: <b>{date_str}</b>\n👩‍💼 Специалист: <b>{specialist}</b>\nТеперь выберите <b>новое время</b>.",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML'
    )

async def admin_change_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await admin_change_date(update, context)

async def admin_process_new_date(update: Update, context: ContextTypes.DEFAULT_TYPE, date_str: str):
    query = update.callback_query
    await query.answer()
    context.user_data["new_date"] = date_str
    kb = [
        [InlineKeyboardButton("👩‍💼 Выбрать cпециалиста", callback_data="admin_change_specialist")],
        [InlineKeyboardButton("⏰ Пропустить (оставить текущего)", callback_data="admin_skip_specialist")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="admin_change_date")]
    ]
    await query.edit_message_text(
        f"📅 Новая дата: <b>{date_str}</b>\n\nТеперь выберите специалиста или пропустите этот шаг:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML'
    )

async def admin_process_new_specialist(update: Update, context: ContextTypes.DEFAULT_TYPE, specialist: str):
    query = update.callback_query
    await query.answer()
    context.user_data["new_specialist"] = specialist
    st = context.user_data.get("service_type")
    ss = context.user_data.get("subservice")
    date_str = context.user_data.get("new_date") or context.user_data.get("current_date")
    if not all([st, ss, date_str]):
        await query.edit_message_text("❌ Недостаточно данных для поиска времени.")
        return
    slots, err = await _get_available_slots_for_admin(st, ss, date_str, specialist)
    if err:
        await query.edit_message_text(err)
        return
    if not slots:
        await query.edit_message_text(f"❌ У cпециалиста {specialist} нет свободных слотов на {date_str}.")
        return
    kb = [[InlineKeyboardButton(s, callback_data=f"admin_new_slot_{specialist}_{s}")] for s in slots]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin_change_specialist")])
    await query.edit_message_text(
        f"👩‍💼 Специалист: <b>{specialist}</b>\n📅 Дата: <b>{date_str}</b>\n\nВыберите время:",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML'
    )

async def admin_skip_specialist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current = context.user_data.get("current_specialist")
    if not current:
        await query.edit_message_text("❌ Текущий cпециалист не указан.")
        return
    context.user_data["new_specialist"] = current
    return await admin_change_time(update, context)

async def admin_process_new_slot(update: Update, context: ContextTypes.DEFAULT_TYPE, specialist: str, time_str: str):
    query = update.callback_query
    await query.answer()
    record_id = context.user_data.get("admin_reschedule_record_id")
    new_date = context.user_data.get("new_date") or context.user_data.get("current_date")
    new_specialist = specialist or context.user_data.get("current_specialist")
    records = safe_get_sheet_data(SHEET_ID, "Записи!A3:O") or []
    orig = None
    for r in records:
        if len(r) > 0 and r[0] == record_id:
            orig = r
            break
    if not orig:
        await query.edit_message_text("❌ Оригинальная запись не найдена.")
        return
    name = orig[1] if len(orig) > 1 else ""
    phone = orig[2] if len(orig) > 2 else ""
    st = orig[3] if len(orig) > 3 else ""
    check_result, error_msg = await _validate_booking_checks(context, name, phone, new_date, time_str, st)
    if check_result is False:
        await query.edit_message_text(f"❌ Нельзя перенести запись:\n{error_msg}\n\nВыберите другое время.")
        return
    elif check_result == "CONFIRM_REPEAT":
        conflict = context.user_data.get("repeat_booking_conflict", {})
        kb = [
            [InlineKeyboardButton("✅ Да, перенести", callback_data=f"admin_force_reschedule_{record_id}")],
            [InlineKeyboardButton("❌ Выбрать другое время", callback_data=f"admin_manage_{record_id}")]
        ]
        await query.edit_message_text(
            f"⚠️ У клиента уже есть запись в этой категории:\n• {conflict.get('category', 'N/A')} {conflict.get('date', 'N/A')} в {conflict.get('time', 'N/A')}\n\nВсё равно перенести?",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return
    msg = (
        f"🔄 Подтвердите перенос записи {record_id}\n\n<b>БЫЛО:</b>\n"
        f"• Дата: {orig[6] if len(orig) > 6 else 'N/A'}\n"
        f"• Время: {orig[7] if len(orig) > 7 else 'N/A'}\n"
        f"• Специалист: {orig[5] if len(orig) > 5 else 'N/A'}\n\n"
        f"<b>СТАНЕТ:</b>\n• Дата: {new_date}\n• Время: {time_str}\n• Специалист: {new_specialist}\n\n"
        f"Клиент: {orig[1] if len(orig) > 1 else 'N/A'}"
    )
    kb = [
        [InlineKeyboardButton("✅ Подтвердить перенос", callback_data=f"admin_confirm_reschedule_{record_id}")],
        [InlineKeyboardButton("❌ Отменить", callback_data=f"admin_manage_{record_id}")]
    ]
    context.user_data.update({"new_date": new_date, "new_time": time_str, "new_specialist": new_specialist})
    await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

async def _admin_save_reschedule(update: Update, context: ContextTypes.DEFAULT_TYPE, record_id: str, force: bool = False):
    query = update.callback_query
    await query.answer()
    new_date = context.user_data.get("new_date")
    new_time = context.user_data.get("new_time")
    new_specialist = context.user_data.get("new_specialist")
    if not all([new_date, new_time, new_specialist]):
        await query.edit_message_text("❌ Не все данные для переноса заполнены.")
        return
    records = safe_get_sheet_data(SHEET_ID, "Записи!A3:O") or []
    for idx, r in enumerate(records, start=2):
        if len(r) > 0 and r[0] == record_id:
            old_date = str(r[6]).strip() if len(r) > 6 else ""
            old_time = str(r[7]).strip() if len(r) > 7 else ""
            old_specialist = str(r[5]).strip() if len(r) > 5 else ""
            updated = list(r)
            updated[5] = new_specialist
            updated[6] = new_date
            updated[7] = new_time
            updated[9] = datetime.now(TIMEZONE).strftime("%d.%m.%Y %H:%M")
            note = f"Перенесено админом {datetime.now(TIMEZONE).strftime('%d.%m.%Y %H:%M')}"
            if force:
                note += " (принудительно, несмотря на повтор)"
            updated[10] = note
            safe_update_sheet_row(SHEET_ID, "Записи", idx, updated)
            event_id = r[14] if len(r) > 14 else None
            if event_id:
                ss = r[4] if len(r) > 4 else ""
                name = r[1] if len(r) > 1 else ""
                phone = r[2] if len(r) > 2 else ""
                step = calculate_service_step(ss)
                dt = datetime.strptime(f"{new_date} {new_time}", "%d.%m.%Y %H:%M")
                start_dt = TIMEZONE.localize(dt)
                end_dt = start_dt + timedelta(minutes=step)
                safe_update_calendar_event(
                    CALENDAR_ID, event_id, f"{name} - {ss}", start_dt.isoformat(), end_dt.isoformat(), "10",
                    f"Клиент: {name}, тел.: {phone}\nПеренесено: {datetime.now(TIMEZONE).strftime('%d.%m.%Y %H:%M')}"
                )
            if old_date and old_time and old_specialist:
                await check_waiting_list(old_date, old_time, old_specialist, context)
                logger.info(f"🔄 Проверен лист ожидания для освободившегося слота {old_date} {old_time} у {old_specialist} при переносе записи {record_id}.")
            client_chat_id = r[13] if len(r) > 13 else None
            if client_chat_id and client_chat_id.isdigit():
                try:
                    await context.bot.send_message(
                        chat_id=int(client_chat_id),
                        text=f"🔄 Ваша запись {record_id} была перенесена администратором.\n\nНовые данные:\n• Дата: {new_date}\n• Время: {new_time}\n• Специалист: {new_specialist}\n\nЕсли новое время не подходит, свяжитесь с нами."
                    )
                except Exception as e:
                    logger.error(f"❌ Не удалось уведомить клиента о переносе: {e}")
            success = f"✅ Запись {record_id} успешно перенесена!\n\nНовые данные:\n• Дата: {new_date}\n• Время: {new_time}\n• Специалист: {new_specialist}"
            if force:
                success += "\n\n⚠️ Перенос выполнен принудительно (клиент имеет повторную запись в категории)"
            await query.edit_message_text(success)
            for key in ["admin_reschedule_record_id", "new_date", "new_time", "new_specialist", "admin_mode", "repeat_booking_conflict"]:
                context.user_data.pop(key, None)
            logger.info(f"✅ Админ {'принудительно ' if force else ''}перенес запись {record_id} на {new_date} {new_time} к {new_specialist}")
            return
    await query.edit_message_text("❌ Запись не найдена.")

async def admin_confirm_reschedule(update: Update, context: ContextTypes.DEFAULT_TYPE, record_id: str):
    return await _admin_save_reschedule(update, context, record_id, force=False)

async def admin_force_reschedule(update: Update, context: ContextTypes.DEFAULT_TYPE, record_id: str):
    return await _admin_save_reschedule(update, context, record_id, force=True)

async def _get_available_slots_for_admin(service_type: str, subservice: str, date_str: str, specialist: str):
    try:
        day_headers = safe_get_sheet_data(SHEET_ID, "График специалистов!C2:I2") or []
        if not day_headers or len(day_headers[0]) < 7:
            return None, "❌ Не удалось загрузить расписание дней недели из таблицы."
        day_titles = [str(h).strip().lower() for h in day_headers[0]]
        target_date = datetime.strptime(date_str, "%d.%m.%Y")
        day_number = target_date.weekday()
        if day_number >= len(day_titles):
            return None, f"❌ Не удалось определить график для {date_str}."
        specialist_rows = safe_get_sheet_data(SHEET_ID, "График специалистов!A:A") or []
        specialist_row_idx = -1
        for i, row in enumerate(specialist_rows):
            if len(row) > 0 and str(row[0]).strip() == specialist:
                specialist_row_idx = i + 2
                break
        if specialist_row_idx == -1:
            return None, f"❌ Специалист {specialist} не найден в графике."
        day_col_letter = chr(67 + day_number)
        schedule_cell = f"{day_col_letter}{specialist_row_idx}"
        schedule_data = safe_get_sheet_data(SHEET_ID, f"График специалистов!{schedule_cell}:{schedule_cell}") or []
        if not schedule_data or not schedule_data[0]:
            return None, f"❌ Нет графика для {specialist} на {date_str}."
        schedule_range = schedule_data[0][0]
        if schedule_range.lower() == "выходной":
            return None, f"❌ {specialist} не работает {date_str}."
        start_time_str, end_time_str = schedule_range.split("-")
        start_time = datetime.strptime(start_time_str.strip(), "%H:%M").time()
        end_time = datetime.strptime(end_time_str.strip(), "%H:%M").time()
        step_minutes = calculate_service_step(subservice)
        all_records = safe_get_sheet_data(SHEET_ID, "Записи!A3:O") or []
        booked = []
        for r in all_records:
            if len(r) > 7 and str(r[5]).strip() == specialist and str(r[6]).strip() == date_str and str(r[8]).strip() in ["подтверждено", "в резерве", "ожидает оплаты"]:
                booked.append(str(r[7]).strip())
        available = []
        current = datetime.combine(target_date.date(), start_time)
        end_dt = datetime.combine(target_date.date(), end_time)
        while current + timedelta(minutes=step_minutes) <= end_dt:
            slot_time = current.strftime("%H:%M")
            if slot_time not in booked:
                available.append(slot_time)
            current += timedelta(minutes=step_minutes)
        return available, None
    except Exception as e:
        logger.error(f"Ошибка при поиске доступных слотов: {e}")
        return None, "❌ Ошибка при поиске доступных слотов."

# --- TRIGGER WORDS ---
async def handle_trigger_words(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    state = context.user_data.get("state")
    ignore_states = [
        ENTER_NAME, ENTER_PHONE, CONFIRM_RESERVATION, AWAITING_REPEAT_CONFIRMATION,
        AWAITING_ADMIN_MESSAGE, AWAITING_WAITING_LIST_DETAILS,
        AWAITING_MY_RECORDS_NAME, AWAITING_MY_RECORDS_PHONE,
        AWAITING_WL_CATEGORY, AWAITING_WL_SPECIALIST, AWAITING_WL_DATE, AWAITING_WL_TIME, AWAITING_WL_PRIORITY,
        AWAITING_ADMIN_SEARCH, AWAITING_ADMIN_NEW_DATE, AWAITING_ADMIN_NEW_SPECIALIST, AWAITING_ADMIN_NEW_TIME,
        AWAITING_PHONE_FOR_CALLBACK,
    ]
    if state in ignore_states:
        return
    text = update.message.text.lower()
    for trigger in TRIGGER_WORDS:
        if trigger and trigger in text:
            user = update.effective_user
            now = datetime.now(TIMEZONE).time()
            try:
                start_str = get_setting("Время начала работы", "10:00")
                end_str = get_setting("Время окончания работы", "20:00")
                start_time = datetime.strptime(start_str, "%H:%M").time()
                end_time = datetime.strptime(end_str, "%H:%M").time()
                is_working = start_time <= now <= end_time
            except Exception:
                logger.error("❌ Ошибка в формате времени работы. Используем 10:00–20:00.")
                start_time = datetime_time(10, 0)
                end_time = datetime_time(20, 0)
                is_working = start_time <= now <= end_time
            if is_working:
                await notify_admins(context, f"📞 Пользователь (ID скрыт): {update.message.text}")
                await update.message.reply_text("✅ Администратор свяжется с вами.")
            else:
                context.user_data["reverse_call_msg"] = update.message.text
                context.user_data["state"] = AWAITING_PHONE_FOR_CALLBACK
                await update.message.reply_text("⏰ Мы не работаем. Пожалуйста, укажите ваш номер телефона для обратной связи:")
                return
            break

# --- NOTIFY ADMINS OF NEW CALLS — ОБНОВЛЕНО ПО ТЗ 9.5: ПОСЛЕ ОКОНЧАНИЯ ПРЕДЫДУЩЕГО РАБОЧЕГО ДНЯ ---
async def notify_admins_of_new_calls_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        now = datetime.now(TIMEZONE)

        # === ШАГ 1: Найти ВРЕМЯ ОКОНЧАНИЯ ПОСЛЕДНЕГО РАБОЧЕГО ДНЯ ===
        schedule_data = safe_get_sheet_data(SHEET_ID, "График специалистов!A3:I") or []
        org_name = get_setting("Название заведения", "").strip()
        if not org_name:
            logger.error("❌ Не задано 'Название заведения' в настройках.")
            return

        org_row = None
        for row in schedule_data:
            if len(row) > 0 and str(row[0]).strip() == org_name:
                org_row = row
                break
        if not org_row or len(org_row) < 8:
            logger.error(f"❌ Не найдена строка '{org_name}' в 'График специалистов' или недостаточно данных.")
            return

        day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        last_work_end = None
        days_back = 0
        max_days_back = 30

        while days_back <= max_days_back:
            check_date = now.date() - timedelta(days=days_back)
            day_idx = check_date.weekday()
            col_idx = day_idx + 1  # B=1 (Пн), ..., H=7 (Вс)

            if col_idx < len(org_row):
                cell = str(org_row[col_idx]).strip()
                if cell.lower() != "выходной" and "-" in cell:
                    try:
                        _, end_str = cell.split("-", 1)
                        end_time = datetime.strptime(end_str.strip(), "%H:%M").time()
                        last_work_end = TIMEZONE.localize(datetime.combine(check_date, end_time))
                        logger.info(f"✅ Последний рабочий день: {check_date} (окончание в {end_time})")
                        break
                    except Exception as e:
                        logger.warning(f"⚠️ Ошибка парсинга времени в ячейке {cell}: {e}")
            days_back += 1

        if not last_work_end:
            logger.warning("⚠️ Не удалось определить последний рабочий день. Используем вчерашний день 20:00.")
            last_work_end = TIMEZONE.localize(datetime.combine(now.date() - timedelta(days=1), datetime.strptime("20:00", "%H:%M").time()))

        # === ШАГ 2: Найти новые заявки ПОСЛЕ last_work_end ===
        calls = safe_get_sheet_data(SHEET_ID, "Обратные звонки!A3:J") or []
        new_calls = []
        calls_to_update = []

        for idx, call in enumerate(calls, start=2):
            if len(call) < 10:
                call += [""] * (10 - len(call))
            try:
                call_time_str = call[1]
                call_time = TIMEZONE.localize(datetime.strptime(call_time_str, "%d.%m.%Y %H:%M"))
                status = call[7] if len(call) > 7 else "ожидает"
                if call_time > last_work_end and status == "ожидает":
                    new_calls.append(call)
                    calls_to_update.append(idx)
            except Exception as e:
                logger.warning(f"⚠️ Неверный формат даты/статуса в заявке (строка {idx}): {call}. Ошибка: {e}")

        # === ШАГ 3: Уведомить и обновить ===
        if new_calls:
            count = len(new_calls)
            max_in_msg = int(get_setting("Максимум заявок в уведомлении", "5"))
            text = f"📞 Новые заявки на обратный звонок (после {last_work_end.strftime('%d.%m.%Y %H:%M')}): {count} шт.\n"
            for i, call in enumerate(new_calls[:max_in_msg]):
                name = call[2] if len(call) > 2 else "Не указано"
                phone = call[3] if len(call) > 3 else "Не указано"
                contact = call[5] if len(call) > 5 else "Telegram"
                note = call[8] if len(call) > 8 else "Без примечания"
                time_str = call[1] if len(call) > 1 else "Неизвестно"
                text += f"{i+1}. {name} ({contact})\n   📞 {phone}\n   🕒 {time_str}\n   📝 {note}\n"
            if count > max_in_msg:
                text += f"... и ещё {count - max_in_msg} заявок\n"
            text += "📋 Полный список — в листе «Обратные звонки»."
            await notify_admins(context, text)
            logger.info(f"✅ Уведомлено админов о {count} заявках (после {last_work_end.strftime('%d.%m.%Y %H:%M')})")

            current_time_str = datetime.now(TIMEZONE).strftime("%d.%m.%Y %H:%M")
            for idx in calls_to_update:
                try:
                    full_row = safe_get_sheet_data(SHEET_ID, f"Обратные звонки!A{idx}:J{idx}")[0]
                    while len(full_row) < 10:
                        full_row.append("")
                    full_row[6] = current_time_str  # G — Время уведомления
                    full_row[7] = "уведомлен"        # H — Статус
                    safe_update_sheet_row(SHEET_ID, "Обратные звонки", idx, full_row)
                except Exception as e:
                    logger.error(f"❌ Не удалось обновить строку {idx}: {e}")
        else:
            logger.info(f"📭 Новых заявок после {last_work_end.strftime('%d.%m.%Y %H:%M')} нет.")

    except Exception as e:
        logger.error(f"❌ Ошибка в notify_admins_of_new_calls_job: {e}", exc_info=True)

# --- GENERIC MESSAGE HANDLER ---
async def generic_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if rate_limiter.is_limited(user_id):
        await update.message.reply_text("⚠️ Слишком много запросов. Подождите минуту.")
        return
    await update_last_activity(update, context)
    state = context.user_data.get("state")
    logger.debug(f"Получено сообщение в состоянии: {state}")

    if state == AWAITING_PHONE_FOR_CALLBACK:
        return await handle_phone_for_callback(update, context)

    handlers = {
        ENTER_NAME: enter_name,
        ENTER_PHONE: enter_phone,
        AWAITING_ADMIN_MESSAGE: lambda u,c: (notify_admins(c, f"📞 Сообщение от клиента (ID скрыт): {u.message.text}"), u.message.reply_text("✅ Администратор свяжется с вами."), c.user_data.clear(), c.user_data.update({"state": MENU}) or MENU),
        AWAITING_WAITING_LIST_DETAILS: handle_waiting_list_input,
        AWAITING_REPEAT_CONFIRMATION: lambda u,c: u.message.reply_text("❌ Пожалуйста, используйте кнопки для подтверждения или отмены.") or AWAITING_REPEAT_CONFIRMATION,
        AWAITING_ADMIN_SEARCH: handle_admin_search,
        AWAITING_MY_RECORDS_NAME: handle_my_records_input,
        AWAITING_MY_RECORDS_PHONE: handle_my_records_input,
        AWAITING_WL_CATEGORY: handle_waiting_list_input,
        AWAITING_WL_SPECIALIST: handle_waiting_list_input,
        AWAITING_WL_DATE: handle_waiting_list_input,
        AWAITING_WL_TIME: handle_waiting_list_input,
        AWAITING_WL_PRIORITY: handle_waiting_list_input,
        AWAITING_CONFIRMATION: lambda u,c: u.message.reply_text("❌ Пожалуйста, используйте кнопки 'Подтвердить' или 'Отменить'.") or AWAITING_CONFIRMATION,
    }
    if state in handlers:
        if state == AWAITING_ADMIN_MESSAGE:
            await notify_admins(context, f"📞 Сообщение от клиента (ID скрыт): {update.message.text}")
            await update.message.reply_text("✅ Администратор свяжется с вами.")
            context.user_data.clear()
            context.user_data["state"] = MENU
            return MENU
        else:
            return await handlers[state](update, context)
    await handle_trigger_words(update, context)
    return None

# --- HANDLE_PHONE_FOR_CALLBACK ---
async def handle_phone_for_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = (update.message.text or "").strip()
    if not validate_phone(phone):
        await update.message.reply_text("❌ Неверный формат телефона. Введите 10–15 цифр.")
        return AWAITING_PHONE_FOR_CALLBACK

    normalized = phone
    if normalized.startswith("+7"):
        normalized = "8" + normalized[2:]
    elif normalized.startswith("7") and len(normalized) == 11:
        normalized = "8" + normalized[1:]
    digits = ''.join(filter(str.isdigit, normalized))
    if len(digits) < 10:
        await update.message.reply_text("❌ Слишком короткий номер. Введите 10–15 цифр.")
        return AWAITING_PHONE_FOR_CALLBACK
    normalized = digits

    user = update.effective_user
    msg = context.user_data.get("reverse_call_msg", "Не указано")

    safe_append_to_sheet(SHEET_ID, "Обратные звонки", [
        f"CALL-{int(time.time())}",
        datetime.now(TIMEZONE).strftime("%d.%m.%Y %H:%M"),
        user.first_name or "Не указано",
        normalized,
        "",
        "Telegram",
        "",
        "ожидает",
        msg,
        "1"
    ])

    await update.message.reply_text("✅ Ваш запрос и номер сохранены. Администратор перезвонит в рабочее время.")
    context.user_data.clear()
    return MENU

# --- REGISTER HANDLERS ---
def register_handlers(application: Application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("record", handle_record_command))
    application.add_handler(CommandHandler("my_records", show_my_records))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generic_message_handler))

# --- ENTRYPOINT ---
def main():
    persistence_file = "bot_data.pickle"
    try:
        if os.path.exists(persistence_file):
            os.remove(persistence_file)
            logger.info("🧹 Старый файл persistence удалён при старте.")
    except Exception:
        logger.exception("Не удалось удалить старый persistence файл при старте.")
    if not create_lock_file():
        return
    setup_production_logging()
    logger.info("🔄 Запуск бота...")
    if not validate_configuration():
        remove_lock_file()
        return
    try:
        load_settings_from_table()
        logger.info("✅ Настройки загружены и закэшированы при старте")
        tw = get_setting("Триггерные слова", "админ, связаться, помощь")
        global TRIGGER_WORDS
        TRIGGER_WORDS = [w.strip().lower() for w in tw.split(",") if w.strip()]
        logger.info(f"✅ Триггерные слова загружены: {TRIGGER_WORDS}")
    except Exception as e:
        logger.critical(f"❌ Не удалось загрузить настройки: {e}")
        remove_lock_file()
        return
    try:
        load_admins()
        logger.info("✅ Администраторы загружены.")
    except Exception as e:
        logger.critical(f"❌ Не удалось загрузить администраторов: {e}")
        remove_lock_file()
        return
    log_business_event("bot_started")
    persistence = PicklePersistence(filepath=persistence_file)
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()
    application.add_error_handler(global_error_handler)
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, global_activity_updater), group=-1)
    register_handlers(application)
    logger.info("✅ Обработчики зарегистрированы.")
    application.job_queue.run_daily(cleanup_old_sessions_job, time=datetime.strptime("03:00", "%H:%M").time())
    application.job_queue.run_repeating(send_reminders, interval=60, first=10)
    notify_time = datetime.strptime(get_setting("Время утреннего уведомления о заявках", "09:00"), "%H:%M").time()
    application.job_queue.run_daily(notify_admins_of_new_calls_job, time=notify_time)
    application.job_queue.run_repeating(health_check_job, interval=300, first=10)
    application.job_queue.run_repeating(cleanup_stuck_reservations_job, interval=900, first=60)
    def _handle_exit(signum, frame):
        logger.info(f"Получен системный сигнал {signum}, завершаем работу...")
        try:
            remove_lock_file()
        except Exception:
            pass
        sys.exit(0)
    try:
        signal.signal(signal.SIGTERM, _handle_exit)
        signal.signal(signal.SIGINT, _handle_exit)
        logger.info("✅ Обработчики сигналов зарегистрированы.")
    except Exception as _err:
        logger.debug(f"Не удалось установить signal handlers: {_err}")
    try:
        logger.info("🚀 Бот запущен в режиме long polling.")
        application.run_polling()
    except KeyboardInterrupt:
        logger.info("⚠️ Получен сигнал остановки (Ctrl+C).")
    except Exception as e:
        logger.critical(f"❌ Критическая ошибка при работе бота: {e}", exc_info=True)
    finally:
        remove_lock_file()
        logger.info("🔒 Бот остановлен и lock-файл удалён.")

if __name__ == "__main__":
    main()

