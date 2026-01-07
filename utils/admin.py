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
        print(f"🔧 DEBUG load_admins: сырые данные из таблицы: {admins}")
    except Exception as e:
        logger.exception("❌ Не удалось получить список админов из таблицы: %s", e)
        ADMIN_CHAT_IDS = []
        return

    ids = []
    for row in admins:
        print(f"🔧 DEBUG load_admins: обработка строки: {row}")
        if len(row) >= 3:
            try:
                chat_id = int(row[0])
                access_flag = row[2].strip().lower()
                print(f"🔧 DEBUG: chat_id={chat_id}, access='{access_flag}'")
                if access_flag in ("да", "yes", "y", "true", "1"):
                    ids.append(chat_id)
                    print(f"🔧 DEBUG: добавлен админ {chat_id}")
                else:
                    print(f"🔧 DEBUG: пропущен (доступ='{access_flag}')")
            except ValueError:
                print(f"🔧 DEBUG: ошибка преобразования chat_id: {row[0]}")
            except Exception as e:
                print(f"🔧 DEBUG: ошибка обработки строки: {e}")

    ADMIN_CHAT_IDS = ids
    print(f"🔧 DEBUG load_admins: итоговый список админов: {ADMIN_CHAT_IDS}")

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
