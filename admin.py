# utils/admin.py
ADMIN_CHAT_IDS = []

def load_admins():
    global ADMIN_CHAT_IDS
    admins = get_sheet_data("SHEET_ID", "Администраторы!A:B")
    ADMIN_CHAT_IDS = [int(row[0]) for row in admins if len(row) > 1 and row[2] == "Да"]

async def notify_admins(context, message):
    for chat_id in ADMIN_CHAT_IDS:
        try:
            await context.bot.send_message(chat_id=chat_id, text=message)
        except Exception as e:
            print(f"Не удалось отправить админу {chat_id}: {e}")