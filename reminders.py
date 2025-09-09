# utils/reminders.py
from datetime import datetime, timedelta
import pytz
from config import TIMEZONE
from utils.google import get_sheet_data, update_sheet_row

async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TIMEZONE)
    records = get_sheet_data(SHEET_ID, "Записи!A2:P")

    for i, row in enumerate(records):
        if len(row) < 15 or row[8] != "подтверждено":
            continue

        record_id = row[0]
        name = row[1]
        phone = row[2]
        date_str = row[6]
        time_str = row[7]
        chat_id = row[13]

        event_time = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
        event_time = TIMEZONE.localize(event_time)

        if abs((event_time - now).total_seconds() - 24*3600) < 300 and row[11] == "❌":
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Напоминаем: завтра у вас запись на {row[4]} к {row[5]} в {time_str}.",
                    reply_markup=build_confirm_cancel_kb(record_id)
                )
                update_sheet_row("SHEET_ID", "Записи", i+2, row[:11] + ["✅"] + row[12:])
            except:
                await notify_admins(context, f"❌ Не удалось отправить напоминание клиенту {name}. Позвоните: {phone}.")

        if abs((event_time - now).total_seconds() - 3600) < 300 and row[12] == "❌":
            try:
                await context.bot.send_message(chat_id=chat_id, text="Через час у вас приём. Не опаздывайте!")
                update_sheet_row("SHEET_ID", "Записи", i+2, row[:12] + ["✅"] + row[13:])
            except:
                await notify_admins(context, f"❌ Не удалось отправить напоминание клиенту {name}. Позвоните: {phone}.")

def build_confirm_cancel_kb(record_id):
    keyboard = [
        [InlineKeyboardButton("✅ Подтверждаю", callback_data=f"confirm_reminder_{record_id}")],
        [InlineKeyboardButton("❌ Отменяю", callback_data=f"cancel_reminder_{record_id}")]
    ]
    return InlineKeyboardMarkup(keyboard)