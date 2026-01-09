# utils/validation.py - новый
import re

def validate_name(name_str: str) -> bool:
    """
    Проверяет имя: длина 2-30, только буквы, пробелы, один дефис.
    """
    if not name_str or not (2 <= len(name_str) <= 30):
        return False
    if not re.match(r"^[a-zA-Zа-яА-ЯёЁ\s\-]+$", name_str):
        return False
    if name_str.count('-') > 1:
        return False
    if name_str.startswith((' ', '-')) or name_str.endswith((' ', '-')):
        return False
    if '  ' in name_str or '--' in name_str or '- ' in name_str or ' -' in name_str:
        return False
    return True


def validate_phone(phone_str: str) -> str:
    """
    Проверяет телефон:
    - Российские: 8XXXXXXXXXX (11 цифр) или +7XXXXXXXXXX → 8XXXXXXXXXX
    - Международные: +XXXXXXXXX (10-15 цифр, сохраняем +)
    
    Возвращает нормализованный номер или пустую строку при ошибке.
    """
    if not phone_str:
        return ""
    
    # Убираем пробелы, скобки, дефисы
    cleaned = re.sub(r'[\s\(\)\-]', '', phone_str)
    
    # МЕЖДУНАРОДНЫЙ НОМЕР (начинается с +)
    if cleaned.startswith('+'):
        digits_after_plus = cleaned[1:]  # Убираем +
        digits_only = re.sub(r'\D', '', digits_after_plus)  # Только цифры
        
        # Проверяем длину (10-15 цифр после +)
        if not (10 <= len(digits_only) <= 15):
            return ""
        
        # Российский номер +7XXXXXXXXXX → 8XXXXXXXXXX (ТОЛЬКО 11 цифр)
        if cleaned.startswith('+7'):
            if len(digits_only) == 11:
                return '8' + digits_only[1:]  # +79034371439 → 89034371439
            else:
                return ""  # +7 с не 11 цифрами - ОШИБКА!
        
        # Другие международные - оставляем с +
        return '+' + digits_only
    
    # РОССИЙСКИЙ НОМЕР (без +)
    else:
        digits_only = re.sub(r'\D', '', cleaned)  # Только цифры
        
        # Проверяем длину
        if not (10 <= len(digits_only) <= 15):
            return ""
        
        # РОССИЙСКИЕ НОМЕРА: начинаются с 8, должны быть 11 ЦИФР
        if digits_only.startswith('8'):
            if len(digits_only) == 11:
                return digits_only  # 89034371439 → 89034371439
            return ""
        
        # НОМЕР начинается с 7 без + - автоисправляем на 8 (только 11 цифр)
        if digits_only.startswith('7'):
            if len(digits_only) == 11:
                return '8' + digits_only[1:]  # 79034371439 → 89034371439
            return ""
        
        # ДРУГОЙ НОМЕР (не начинается с +, 8, 7) - оставляем как есть
        return digits_only


def validate_phone_bool(phone_str: str) -> bool:
    """Совместимость: возвращает bool вместо строки"""
    return bool(validate_phone(phone_str))


print("✅ Модуль validation.py загружен.")
