# utils/reminders.py
import logging
from datetime import datetime, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from config import TIMEZONE, SHEET_ID, CALENDAR_ID
from .safe_google import safe_get_sheet_data, safe_update_sheet_row, safe_delete_calendar_event
from .admin import notify_admins
from .settings import get_setting

logger = logging.getLogger(__name__)

async def send_reminders(context):
    """
    Фоновая задача: отправляет напоминания за 24ч и 1ч.
    """
    now = datetime.now(TIMEZONE)
    records = safe_get_sheet_data(SHEET_ID, "Записи!A2:P") or []

    for i, row in enumerate(records, start=2):
        if len(row) < 15 or row[8] != "подтверждено":
            continue

        record_id = row[0]
        name = row[1]
        phone = row[2]
        date_str = row[6]
        time_str = row[7]
        chat_id = row[13]

        try:
            event_time = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
            event_time = TIMEZONE.localize(event_time)
        except ValueError:
            logger.error(f"❌ Неверный формат даты/времени в записи {record_id}: {date_str} {time_str}")
            continue

        # Напоминание за 24 часа
        if abs((event_time - now).total_seconds() - 24*3600) < 300 and row[11] == "❌":
            try:
                msg = get_setting("Текст напоминания 24ч", "Напоминаем: завтра у вас запись на {service} в {time}.").format(
                    service=row[4], time=time_str
                )
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    reply_markup=build_confirm_cancel_kb(record_id)
                )
                updated = row.copy()
                updated[11] = "✅"
                safe_update_sheet_row(SHEET_ID, "Записи", i, updated)
                logger.info(f"📤 24ч напоминание отправлено {name} (ID: {record_id})")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки 24ч напоминания: {e}")
                await notify_admins(context, f"❌ Не удалось отправить напоминание {name} ({phone}): {e}")

        # Напоминание за 1 час
        if abs((event_time - now).total_seconds() - 3600) < 300 and row[12] == "❌":
            try:
                msg = get_setting("Текст напоминания 1ч", "Через час у вас приём. Не опаздывайте!")
                await context.bot.send_message(chat_id=chat_id, text=msg)
                updated = row.copy()
                updated[12] = "✅"
                safe_update_sheet_row(SHEET_ID, "Записи", i, updated)
                logger.info(f"📤 1ч напоминание отправлено {name} (ID: {record_id})")
            except Exception as e:
                logger.error(f"❌ Ошибка отправки 1ч напоминания: {e}")
                await notify_admins(context, f"❌ Не удалось отправить напоминание {name} ({phone}): {e}")

def build_confirm_cancel_kb(record_id: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтверждаю", callback_data=f"confirm_reminder_{record_id}")],
        [InlineKeyboardButton("❌ Отменяю", callback_data=f"cancel_reminder_{record_id}")]
    ])

async def handle_confirm_reminder(record_id: str, query, context):
    records = safe_get_sheet_data(SHEET_ID, "Записи!A2:P") or []
    for idx, row in enumerate(records, start=2):
        if len(row) > 0 and row[0] == record_id:
            if len(row) < 12:
                row.extend([""] * (12 - len(row)))
            if row[11] == "❌":
                updated = row.copy()
                updated[11] = "✅"
                safe_update_sheet_row(SHEET_ID, "Записи", idx, updated)
                await query.edit_message_text("✅ Спасибо! Ваша запись подтверждена.")
                logger.info(f"✅ Клиент подтвердил запись {record_id}")
            else:
                await query.edit_message_text("ℹ️ Ваш ответ уже учтён.")
            return
    await query.edit_message_text("❌ Запись не найдена.")

async def handle_cancel_reminder(record_id: str, query, context):
    records = safe_get_sheet_data(SHEET_ID, "Записи!A2:P") or []
    for idx, row in enumerate(records, start=2):
        if len(row) > 0 and row[0] == record_id:
            if len(row) < 15:
                row.extend([""] * (15 - len(row)))
            updated = row.copy()
            updated[8] = "отменено"
            safe_update_sheet_row(SHEET_ID, "Записи", idx, updated)

            event_id = row[14] if len(row) > 14 else None
            if event_id:
                safe_delete_calendar_event(CALENDAR_ID, event_id)
                logger.info(f"✅ Событие {event_id} удалено при отмене записи {record_id}")

            await query.edit_message_text("❌ Запись отменена. Спасибо!")
            logger.info(f"❌ Клиент отменил запись {record_id}")
            await notify_admins(context, f"❗ Клиент отменил запись {record_id}.")
            return
    await query.edit_message_text("❌ Запись не найдена.")
