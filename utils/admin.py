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
    
    print(f"\n{'='*60}")
    print(f"🔧 НАЧИНАЮ ЗАГРУЗКУ АДМИНИСТРАТОРОВ")
    print(f"{'='*60}")
    
    try:
        # Читаем с A3, предполагая, что A1 - название листа, а A2 - заголовки
        print(f"🔧 Читаю таблицу 'Администраторы!A3:C'...")
        admins = safe_get_sheet_data(SHEET_ID, "Администраторы!A3:C")
        
        if not admins:
            print(f"❌ ТАБЛИЦА 'Администраторы' ПУСТАЯ ИЛИ НЕ НАЙДЕНА!")
            ADMIN_CHAT_IDS = []
            return
            
        print(f"✅ Получено строк из таблицы: {len(admins)}")
        for i, row in enumerate(admins, start=1):
            print(f"  Строка {i}: {row}")
            
    except Exception as e:
        print(f"❌ ОШИБКА получения данных: {e}")
        import traceback
        traceback.print_exc()
        ADMIN_CHAT_IDS = []
        return

    ids = []
    for i, row in enumerate(admins, start=1):
        print(f"\n🔧 Обрабатываю строку {i}:")
        print(f"   Содержимое: {row}")
        
        if len(row) < 3:
            print(f"   ⚠️ Пропускаю: строка слишком короткая (нужно 3 колонки)")
            continue
            
        try:
            # Колонка A: chat_id
            chat_id_raw = row[0]
            print(f"   Колонка A (chat_id): '{chat_id_raw}'")
            
            # Преобразуем в строку и чистим
            chat_id_str = str(chat_id_raw).strip()
            print(f"   После очистки: '{chat_id_str}'")
            
            # Проверяем, не пустая ли строка
            if not chat_id_str:
                print(f"   ⚠️ Пропускаю: chat_id пустой")
                continue
                
            # Преобразуем в число
            chat_id = int(chat_id_str)
            print(f"   chat_id как число: {chat_id}")
            
            # Колонка B: Имя (для информации)
            name = str(row[1]).strip() if len(row) > 1 else ""
            print(f"   Колонка B (имя): '{name}'")
            
            # Колонка C: Доступ
            access_raw = row[2] if len(row) > 2 else ""
            access_flag = str(access_raw).strip().lower()
            print(f"   Колонка C (доступ): '{access_raw}' → '{access_flag}'")
            
            # Проверяем доступ
            if access_flag in ("да", "yes", "y", "true", "1", "включено", "активно"):
                ids.append(chat_id)
                print(f"   ✅ ДОБАВЛЯЮ: {chat_id} ({name})")
            else:
                print(f"   ❌ Пропускаю: доступ='{access_flag}' (не разрешено)")
                
        except ValueError as e:
            print(f"   ❌ ОШИБКА: не могу преобразовать '{chat_id_raw}' в число: {e}")
        except Exception as e:
            print(f"   ❌ ОШИБКА обработки строки: {e}")
            import traceback
            traceback.print_exc()

    ADMIN_CHAT_IDS = ids
    
    print(f"\n{'='*60}")
    print(f"📊 ИТОГИ ЗАГРУЗКИ:")
    print(f"   Найдено админов: {len(ADMIN_CHAT_IDS)}")
    print(f"   Список ID: {ADMIN_CHAT_IDS}")
    
    # Проверяем, есть ли мой ID
    my_id = 1163253697
    if my_id in ADMIN_CHAT_IDS:
        print(f"   ✅ МОЙ ID {my_id} НАЙДЕН В СПИСКЕ!")
    else:
        print(f"   ❌ МОЙ ID {my_id} НЕ НАЙДЕН!")
        
        # ВРЕМЕННО добавляем для теста
        ADMIN_CHAT_IDS.append(my_id)
        print(f"   ⚠️ ВРЕМЕННО ДОБАВЛЯЮ {my_id} ВРУЧНУЮ")
    
    print(f"{'='*60}\n")

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
