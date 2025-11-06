# utils/reminders.py
from datetime import datetime, timedelta
import pytz
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import TIMEZONE, SHEET_ID
from .safe_google import safe_get_sheet_data, safe_update_sheet_row
from .admin import notify_admins

logger = logging.getLogger(__name__)

async def send_reminders(context):
    """
    Фоновая задача: отправляет напоминания за 24ч и 1ч.
    """
    now = datetime.now(TIMEZONE)
    records = safe_get_sheet_data(SHEET_ID, "Записи!A2:P")

    for i, row in enumerate(records, start=2): # start=2, потому что A2:P
        if len(row) < 15 or row[8] != "подтверждено": # [8] = Статус
            continue

        record_id = row[0] # [0] = ID
        name = row[1] # [1] = Имя
        phone = row[2] # [2] = Телефон
        date_str = row[6] # [6] = Дата
        time_str = row[7] # [7] = Время
        chat_id = row[13] # [13] = chat_id

        try:
            event_time = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
            event_time = TIMEZONE.localize(event_time)
        except ValueError:
            logger.error(f"❌ Неверный формат даты/времени в записи {record_id}: {date_str} {time_str}")
            continue

        # --- Напоминание за 24 часа ---
        if abs((event_time - now).total_seconds() - 24*3600) < 300 and row[11] == "❌": # [11] = Напоминание 24ч
            try:
                # Загружаем текст из настроек (псевдокод, нужно реализовать get_setting)
                # message_text = get_setting("Текст напоминания 24ч", f"Напоминаем: завтра у вас запись на {row[4]} к {row[5]} в {time_str}.")
                message_text = f"Напоминаем: завтра у вас запись на {row[4]} к {row[5]} в {time_str}." # Временно
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    reply_markup=build_confirm_cancel_kb(record_id) # См. ниже
                )
                # Обновляем статус напоминания 24ч на "✅"
                updated_row = row.copy()
                updated_row[11] = "✅"
                safe_update_sheet_row(SHEET_ID, "Записи", i, updated_row)
                logger.info(f"📤 24ч напоминание отправлено {name} (ID: {record_id})")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки 24ч напоминания {record_id}: {e}")
                # Уведомляем админа
                # admin_message = get_setting("Текст уведомления админу об ошибке", f"❌ Не удалось отправить напоминание клиенту {name}. Позвоните: {phone}.")
                admin_message = f"❌ Не удалось отправить напоминание клиенту {name}. Позвоните: {phone}. Ошибка: {e}"
                await notify_admins(context, admin_message)

        # --- Напоминание за 1 час ---
        if abs((event_time - now).total_seconds() - 3600) < 300 and row[12] == "❌": # [12] = Напоминание 1ч
            try:
                # message_text = get_setting("Текст напоминания 1ч", f"Через час у вас приём. Не опаздывайте!")
                message_text = f"Через час у вас приём. Не опаздывайте!"
                await context.bot.send_message(chat_id=chat_id, text=message_text)
                # Обновляем статус напоминания 1ч на "✅"
                updated_row = row.copy()
                updated_row[12] = "✅"
                safe_update_sheet_row(SHEET_ID, "Записи", i, updated_row)
                logger.info(f"📤 1ч напоминание отправлено {name} (ID: {record_id})")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки 1ч напоминания {record_id}: {e}")
                admin_message = f"❌ Не удалось отправить 1ч напоминание клиенту {name}. Позвоните: {phone}. Ошибка: {e}"
                await notify_admins(context, admin_message)

def build_confirm_cancel_kb(record_id: str):
    """Создаёт inline-клавиатуру для 24ч напоминания."""
    keyboard = [
        [InlineKeyboardButton("✅ Подтверждаю", callback_data=f"confirm_reminder_{record_id}")],
        [InlineKeyboardButton("❌ Отменяю", callback_data=f"cancel_reminder_{record_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def handle_confirm_reminder(record_id: str, query, context):
    """Обрабатывает нажатие кнопки 'Подтверждаю' в напоминании."""
    try:
        records = safe_get_sheet_data(SHEET_ID, "Записи!A2:P")
        for idx, row in enumerate(records, start=2):
            if len(row) > 0 and row[0] == record_id:
                if len(row) < 12:
                    row.extend([""] * (12 - len(row)))
                # Обновляем статус напоминания 24ч на "✅", если он был "❌"
                if row[11] == "❌":
                    row[11] = "✅"
                    safe_update_sheet_row(SHEET_ID, "Записи", idx, row)
                    await query.edit_message_text("✅ Спасибо! Ваша запись подтверждена.")
                    logger.info(f"✅ Клиент подтвердил запись {record_id}")
                else:
                    await query.edit_message_text("ℹ️ Ваш ответ уже был учтён.")
                return
        await query.edit_message_text("❌ Запись не найдена.")
    except Exception as e:
        logger.exception(f"❌ Ошибка при подтверждении напоминания {record_id}: {e}")
        await query.edit_message_text("❌ Ошибка при обработке подтверждения.")

async def handle_cancel_reminder(record_id: str, query, context):
    """Обрабатывает нажатие кнопки 'Отменяю' в напоминании."""
    try:
        records = safe_get_sheet_data(SHEET_ID, "Записи!A2:P")
        for idx, row in enumerate(records, start=2):
            if len(row) > 0 and row[0] == record_id:
                if len(row) < 9:
                    row.extend([""] * (9 - len(row)))
                # Меняем статус записи на "отменено"
                row[8] = "отменено" # [8] = Статус
                event_id = row[14] if len(row) > 14 else None # [14] = event_id
                safe_update_sheet_row(SHEET_ID, "Записи", idx, row)

                # Удаляем событие из календаря
                if event_id:
                    safe_delete_calendar_event(CALENDAR_ID, event_id)
                    logger.info(f"캘 Календарное событие {event_id} удалено при отмене записи {record_id}")

                await query.edit_message_text("❌ Запись отменена. Спасибо, что сообщили.")
                logger.info(f"❌ Клиент отменил запись {record_id}")

                # Уведомляем админа
                # admin_message = get_setting("Текст уведомления админу о новой заявке", f"❗ Клиент отменил запись {record_id}.")
                admin_message = f"❗ Клиент отменил запись {record_id}."
                await notify_admins(context, admin_message)
                return
        await query.edit_message_text("❌ Запись не найдена.")
    except Exception as e:
        logger.exception(f"❌ Ошибка при отмене записи из напоминания {record_id}: {e}")
        await query.edit_message_text("❌ Ошибка при обработке отмены.")

print("✅ Модуль reminders.py загружен.")
