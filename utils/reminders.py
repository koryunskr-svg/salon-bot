# utils/reminders.py
from datetime import datetime, timedelta
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config import TIMEZONE, SHEET_ID
from utils.google import get_sheet_data, update_sheet_row
from utils.admin import notify_admins

logger = logging.getLogger(__name__)


def build_confirm_cancel_kb(record_id: str) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("✅ Подтверждаю", callback_data=f"confirm_reminder_{record_id}")],
        [InlineKeyboardButton("❌ Отменяю", callback_data=f"cancel_reminder_{record_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)


async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    try:
        now = datetime.now(TIMEZONE)
        records = get_sheet_data(SHEET_ID, "Записи!A2:P")
    except Exception as e:
        logger.exception("Не удалось получить записи из таблицы: %s", e)
        return

    for i, row in enumerate(records):
        if len(row) < 9 or row[8] != "подтверждено":
            continue

        record_id = row[0]
        name = row[1] if len(row) > 1 else ""
        phone = row[2] if len(row) > 2 else ""
        date_str = row[6] if len(row) > 6 else ""
        time_str = row[7] if len(row) > 7 else ""
        chat_id = None
        if len(row) > 13:
            try:
                chat_id = int(row[13])
            except Exception:
                chat_id = row[13]

        try:
            event_time = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
            event_time = TIMEZONE.localize(event_time)
        except Exception:
            continue

        try:
            # 24 hours reminder
            if abs((event_time - now).total_seconds() - 24 * 3600) < 300:
                if len(row) <= 11 or row[11] == "❌":
                    if chat_id:
                        try:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=f"Напоминаем: завтра у вас запись на {row[4] if len(row) > 4 else ''} к {row[5] if len(row) > 5 else ''} в {time_str}.",
                                reply_markup=build_confirm_cancel_kb(record_id)
                            )
                        except Exception:
                            await notify_admins(context, f"❌ Не удалось отправить напоминание клиенту {name}. Позвоните: {phone}.")
                    try:
                        update_sheet_row(SHEET_ID, "Записи", i + 2, (row[:11] + ["✅"] + row[12:])[:len(row)])
                    except Exception as e:
                        logger.exception("Ошибка обновления статуса напоминания 24h: %s", e)
        except Exception:
            logger.exception("Ошибка при отправке 24h напоминания для %s", record_id)

        try:
            # 1 hour reminder
            if abs((event_time - now).total_seconds() - 3600) < 300:
                if len(row) <= 12 or row[12] == "❌":
                    if chat_id:
                        try:
                            await context.bot.send_message(chat_id=chat_id, text="Через час у вас приём. Не опаздывайте!")
                        except Exception:
                            await notify_admins(context, f"❌ Не удалось отправить часовой напоминание клиенту {name}. Позвоните: {phone}.")
                    try:
                        update_sheet_row(SHEET_ID, "Записи", i + 2, (row[:12] + ["✅"] + row[13:])[:len(row)])
                    except Exception as e:
                        logger.exception("Ошибка обновления статуса напоминания 1h: %s", e)
        except Exception:
            logger.exception("Ошибка при отправке 1h напоминания для %s", record_id)
