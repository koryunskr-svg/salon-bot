# utils/validation.py
import re
import logging

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

def validate_phone(phone_str: str) -> str:
    """
    Проверяет телефон и нормализует к формату 8XXXXXXXXXX.
    Возвращает нормализованный номер или пустую строку при ошибке.
    """
    if not phone_str:
        return ""
    
    # Извлекаем только цифры
    digits_only = re.sub(r'\D', '', phone_str)

    # Проверяем длину
    if not (10 <= len(digits_only) <= 15):   # 10-15 цифр
        return ""
    
    # Нормализуем российский номер
    if digits_only.startswith('7') and len(digits_only) == 11:
        # 7XXXXXXXXXX → 8XXXXXXXXXX
        normalized = '8' + digits_only[1:]
    elif digits_only.startswith('8') and len(digits_only) == 11:
        # 8XXXXXXXXXX - уже нормализован
        normalized = digits_only
    elif len(digits_only) == 10:
        # XXXXXXXXXX → 8XXXXXXXXXX
        normalized = '8' + digits_only   # ← Если digits_only=8903437143 (9 цифр), эта проверка НЕ сработает
    else:
        # Международный формат или другой - возвращаем как есть
        # Если начинается с 7 или 8 - это должен быть российский номер длиной 11
        if digits_only.startswith(('7', '8')):  # Российский номер
            if len(digits_only) != 11:  # Но не 11 цифр!
            return ""  # ← ВАЖНО! Российский номер не 11 цифр - ошибка!
        # Оставляем как есть (международные номера)
        normalized = digits_only    # ← Вот тут 9-значный номер проходит как "международный"!
    
    return normalized
    

# Для обратной совместимости (если где-то используется bool версия)
def validate_phone_bool(phone_str: str) -> bool:
    """Совместимость: возвращает bool вместо строки"""
    return bool(validate_phone(phone_str))

print("✅ Модуль validation.py загружен.")
