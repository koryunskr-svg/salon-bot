# utils/admin.py
import logging
from typing import List
from config import SHEET_ID
from .safe_google import safe_get_sheet_data

logger = logging.getLogger(__name__)

# Глобальный список chat_id администраторов
ADMIN_CHAT_IDS: List[int] = []

def load_admins():
    """
    Загружает список администраторов из Google Таблицы "Администраторы".
    Ожидается лист "Администраторы" с колонками A: chat_id, B: Имя админа, C: Доступ (Да/Нет).
    """
    global ADMIN_CHAT_IDS
    try:
        # Читаем с A3, предполагая, что A1 - название листа, а A2 - заголовки
        admins = safe_get_sheet_data(SHEET_ID, "Администраторы!A3:C")
    except Exception as e:
        logger.exception("❌ Не удалось получить список админов из таблицы: %s", e)
        ADMIN_CHAT_IDS = []
        return

    ids = []
    for row in admins:
        if len(row) >= 3:
            try:
                chat_id = int(row[0])
                access_flag = row[2].strip().lower()
                if access_flag in ("да", "yes", "y", "true", "1"):
                    ids.append(chat_id)
                else:
                    logger.debug(f"⚠️ Админ {chat_id} (имя: {row[1] if len(row) > 1 else 'не указано'}) имеет доступ = '{row[2]}', пропускаем.")
            except ValueError:
                logger.warning(f"⚠️ Неверный chat_id в таблице Администраторы: {row[0]}, строка: {row}")
            except Exception as e:
                logger.warning(f"⚠️ Ошибка при обработке строки администратора: {row}, ошибка: {e}")

    ADMIN_CHAT_IDS = ids
    logger.info(f"✅ Загружены админы: {ADMIN_CHAT_IDS}")

async def notify_admins(context, message: str):
    """Асинхронно отправляет сообщение всем загруженным администраторам."""
    if not ADMIN_CHAT_IDS:
        logger.debug("⚠️ ADMIN_CHAT_IDS пуст — нет кому слать уведомления")
        return
    for chat_id in ADMIN_CHAT_IDS:
        try:
            await context.bot.send_message(chat_id=chat_id, text=message)
            logger.info(f"📤 Уведомление админу {chat_id}: {message[:50]}...")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось отправить админу {chat_id}: {e}")

print("✅ Модуль admin.py загружен.")
