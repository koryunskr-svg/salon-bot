# utils/settings.py
import logging
from .safe_google import safe_get_sheet_data
from config import SHEET_ID

logger = logging.getLogger(__name__)

# Глобальный словарь для кэширования настроек
_cached_settings = {}

def load_settings_from_table():
    """
    Загружает настройки из листа "Настройки" и кэширует их.
    Ожидается структура: A3:C → Ключ, Значение, Описание.
    (A1 — название листа, A2 — заголовки, данные — с A3)
    """
    global _cached_settings
    try:
        # Читаем с A3 — данные начинаются с 3-й строки!
        settings_data = safe_get_sheet_data(SHEET_ID, "Настройки!A3:C")
    except Exception as e:
        logger.exception("❌ Не удалось загрузить настройки из таблицы: %s", e)
        _cached_settings = {}
        return

    new_settings = {}
    for row in settings_data:
        # Убедимся, что в строке достаточно данных (Ключ и Значение обязательны)
        if len(row) >= 2:
            key = row[0].strip()
            value = row[1].strip()
            new_settings[key] = value
        else:
            logger.warning(f"⚠️ Неполная строка в листе 'Настройки': {row}")

    _cached_settings = new_settings
    logger.info(f"✅ Настройки загружены и кэшированы. Ключи: {list(_cached_settings.keys())}")


def get_setting(key: str, default_value=None):
    """
    Возвращает значение настройки по ключу.
    Если настройка не найдена, возвращает default_value.
    Автоматически загружает настройки, если кэш пуст.
    """
    if not _cached_settings:
        logger.warning("⚠️ Настройки не загружены. Загружаю...")
        load_settings_from_table()

    value = _cached_settings.get(key, default_value)
    logger.debug(f"⚙️ Получена настройка '{key}': {value} (по умолчанию: {default_value})")
    return value


logger.info("✅ Модуль settings.py загружен.")
