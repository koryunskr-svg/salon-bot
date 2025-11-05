# utils/validation.py
import re

logger = logging.getLogger(__name__)

def validate_name(name_str: str) -> bool:
    """
    Проверяет имя: длина 2-30, только буквы, пробелы, один дефис.
    """
    if not name_str or not (2 <= len(name_str) <= 30):
        return False
    # Проверяем, содержит ли строка только разрешённые символы
    if not re.match(r"^[a-zA-Zа-яА-ЯёЁ\s\-]+$", name_str):
        return False
    # Проверяем количество дефисов
    if name_str.count('-') > 1:
        return False
    # Проверяем, не начинается/заканчивается ли на пробел/дефис
    if name_str.startswith((' ', '-')) or name_str.endswith((' ', '-')):
        return False
    # Проверяем, нет ли двойных пробелов или дефисов
    if '  ' in name_str or '--' in name_str or '- ' in name_str or ' -' in name_str:
        return False

    return True

def validate_phone(phone_str: str) -> bool:
    """
    Проверяет телефон: 10-15 цифр.
    """
    if not phone_str:
        return False
    # Извлекаем только цифры
    digits_only = re.sub(r'\D', '', phone_str)
    # Проверяем длину
    return 10 <= len(digits_only) <= 15

print("✅ Модуль validation.py загружен.")