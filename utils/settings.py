# utils/settings.py
import logging  # <-- Исправлено: Добавлен импорт logging
from .safe_google import safe_get_sheet_data
from config import SHEET_ID

logger = logging.getLogger(__name__) # <-- Исправлено: Теперь это будет работать

# Глобальный словарь для кэширования настроек
_cached_settings = {}

def load_settings_from_table():
    """
    Загружает настройки из листа "Настройки" и кэширует их.
    Ожидается структура: A2:C -> Ключ, Значение, Описание.
    """
    global _cached_settings
    try:
        # Читаем с A2, предполагая, что A1 - заголовки
        settings_data = safe_get_sheet_data(SHEET_ID, "Настройки!A2:C")
    except Exception as e:
        logger.exception("❌ Не удалось загрузить настройки из таблицы: %s", e)
        _cached_settings = {}
        return

    new_settings = {}
    for row in settings_data: # <-- Исправлено: было settings_
        # Убедимся, что в строке достаточно данных (Ключ и Значение обязательны)
        if len(row) >= 2: 
            key = row[0].strip()
            value = row[1].strip()
            # Кэшируем настройку
            new_settings[key] = value
        else:
            # Логируем предупреждение о неполной строке
            logger.warning(f"⚠️ Неполная строка в листе 'Настройки': {row}")

    # Обновляем глобальный кэш
    _cached_settings = new_settings
    logger.info(f"✅ Настройки загружены и кэшированы. Ключи: {list(_cached_settings.keys())}")

# Исправлено: Убрана строгая типизация -> str, чтобы разрешить возврат None/default_value другого типа
# Рекомендуется: from typing import Optional и def get_setting(...) -> Optional[str]:
def get_setting(key: str, default_value = None): # <-- Исправлено: Убрана строгая типизация -> str
    """
    Возвращает значение настройки по ключу.
    Если настройка не найдена, возвращает default_value.
    Автоматически загружает настройки, если кэш пуст.
    """
    # Проверяем, загружены ли настройки. Если нет - загружаем.
    if not _cached_settings:
        logger.warning("⚠️ Настройки не загружены. Загружаю...")
        load_settings_from_table()
        
    # Получаем значение из кэша. Если не найдено, возвращаем default_value.
    value = _cached_settings.get(key, default_value)
    logger.debug(f"⚙️ Получена настройка '{key}': {value} (по умолчанию: {default_value})")
    return value

# Исправлено: Используем logger.info вместо print
logger.info("✅ Модуль settings.py загружен.")
