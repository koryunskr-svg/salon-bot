# utils/validation.py
import re
import logging  # <-- Исправлено: Добавлен импорт logging

logger = logging.getLogger(__name__) # <-- Исправлено: Теперь это будет работать

def validate_name(name_str: str) -> bool:
    """
    Проверяет имя: длина 2-30, только буквы, пробелы, один дефис.
    """
    if not name_str or not (2 <= len(name_str) <= 30):
        logger.debug(f"validate_name: Длина имени '{name_str}' вне допустимого диапазона (2-30).")
        return False
    # Проверяем, содержит ли строка только разрешённые символы
    if not re.match(r"^[a-zA-Zа-яА-ЯёЁ\s\-]+$", name_str):
        logger.debug(f"validate_name: Имя '{name_str}' содержит недопустимые символы.")
        return False
    # Проверяем количество дефисов
    if name_str.count('-') > 1:
        logger.debug(f"validate_name: Имя '{name_str}' содержит более одного дефиса.")
        return False
    # Проверяем, не начинается/заканчивается ли на пробел/дефис
    if name_str.startswith((' ', '-')) or name_str.endswith((' ', '-')):
        logger.debug(f"validate_name: Имя '{name_str}' начинается или заканчивается на пробел/дефис.")
        return False
    # Проверяем, нет ли двойных пробелов или дефисов
    if '  ' in name_str or '--' in name_str or '- ' in name_str or ' -' in name_str:
        logger.debug(f"validate_name: Имя '{name_str}' содержит двойные пробелы или дефисы.")
        return False

    logger.debug(f"validate_name: Имя '{name_str}' прошло валидацию.")
    return True

def validate_phone(phone_str: str) -> bool:
    """
    Проверяет телефон: 10-15 цифр.
    """
    if not phone_str:
        logger.debug("validate_phone: Номер телефона пуст.")
        return False
    # Извлекаем только цифры
    digits_only = re.sub(r'\D', '', phone_str)
    # Проверяем длину
    length = len(digits_only)
    if not (10 <= length <= 15):
        logger.debug(f"validate_phone: Номер телефона '{phone_str}' имеет недопустимую длину ({length} цифр). Должно быть 10-15.")
        return False
    
    logger.debug(f"validate_phone: Номер телефона '{phone_str}' прошёл валидацию.")
    return True

# Исправлено: Используем logger.info вместо print
logger.info("✅ Модуль validation.py загружен.")
