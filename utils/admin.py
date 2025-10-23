# utils/admin.py
import logging
from typing import List
from utils.safe_google import safe_get_sheet_data as get_sheet_data  # ← через safe_google
from config import SHEET_ID

# Импортируем через safe_google, как в main.py
from utils.safe_google import safe_get_sheet_data as get_sheet_data
from config import SHEET_ID

logger = logging.getLogger(__name__)

ADMIN_CHAT_IDS: List[int] = []


def load_admins():
    """
    Загружает список администраторов из Google Sheets.
    Ожидается лист "Администраторы" с колонками A: chat_id, B: имя, C: 'Да'/'Нет' (активен).
    """
    global ADMIN_CHAT_IDS
    try:
        admins = get_sheet_data(SHEET_ID, "Администраторы!A2:C")
    except Exception as e:
        logger.exception("Не удалось получить список админов из таблицы: %s", e)
        ADMIN_CHAT_IDS = []
        return

    ids = []
    for row in admins:
        if len(row) >= 3:
            try:
                active_flag = row[2].strip().lower()
            except Exception:
                active_flag = ""
            if active_flag in ("да", "yes", "y", "true", "1"):
                try:
                    ids.append(int(row[0]))
                except Exception as e:
                    logger.warning("Неверный chat_id в Администраторы: %s (%s)", row[0], e)
    ADMIN_CHAT_IDS = ids
    logger.info("Загружены админы: %s", ADMIN_CHAT_IDS)


async def notify_admins(context, message: str):
    """
    Асинхронно шлёт сообщение всем загруженным администраторам.
    """
    if not ADMIN_CHAT_IDS:
        logger.debug("ADMIN_CHAT_IDS пуст — нет кому слать уведомления")
        return

    for chat_id in ADMIN_CHAT_IDS:
        try:
            await context.bot.send_message(chat_id=chat_id, text=message)
        except Exception as e:
            logger.exception("Не удалось отправить админу %s сообщение: %s", chat_id, e)

