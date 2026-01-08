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
    Проверяет телефон:
    - Российские: 8XXXXXXXXXX (11 цифр, начинается с 7 или 8)
    - Международные: +XXXXXXXXX (10-15 цифр, сохраняем +)
    Возвращает нормализованный номер или пустую строку при ошибке.
    """
    if not phone_str:
        print(f"🔧 validate_phone: пустой ввод")
        return ""
    
    # Убираем пробелы, скобки, дефисы, но сохраняем + если есть
    cleaned = re.sub(r'[\s\(\)\-]', '', phone_str)
    print(f"🔧 validate_phone: '{phone_str}' → очищено: '{cleaned}'")
    
    # МЕЖДУНАРОДНЫЙ НОМЕР (начинается с +)
    if cleaned.startswith('+'):
        digits_after_plus = cleaned[1:]  # Убираем +
        digits_only = re.sub(r'\D', '', digits_after_plus)  # Убираем всё кроме цифр
        
        print(f"🔧 validate_phone: международный, цифры: '{digits_only}', длина: {len(digits_only)}")
        
        # Проверяем длину (10-15 цифр после +)
        if not (10 <= len(digits_only) <= 15):
            print(f"🔧 validate_phone: ОШИБКА - международный номер длина {len(digits_only)} не в диапазоне 10-15")
            return ""
        
        # Возвращаем с +
        normalized = '+' + digits_only
        print(f"🔧 validate_phone: Международный номер: '{normalized}'")
        return normalized
    
    # РОССИЙСКИЙ НОМЕР (без +)
    else:
        digits_only = re.sub(r'\D', '', cleaned)  # Убираем всё кроме цифр
        print(f"🔧 validate_phone: российский, цифры: '{digits_only}', длина: {len(digits_only)}")
        
        # Проверяем длину
        if not (10 <= len(digits_only) <= 15):
            print(f"🔧 validate_phone: ОШИБКА - длина {len(digits_only)} не в диапазоне 10-15")
            return ""
        
        # РОССИЙСКИЕ НОМЕРА: начинаются с 7 или 8, ДОЛЖНЫ БЫТЬ 11 ЦИФР
        if digits_only.startswith(('7', '8')):
            if len(digits_only) == 11:
                # 7XXXXXXXXXX → 8XXXXXXXXXX
                if digits_only.startswith('7'):
                    normalized = '8' + digits_only[1:]
                    print(f"🔧 validate_phone: Российский 7→8: '{normalized}'")
                else:  # начинается с 8
                    normalized = digits_only
                    print(f"🔧 validate_phone: Российский 8: '{normalized}'")
                return normalized
            else:
                # Российский номер, но не 11 цифр → ОШИБКА
                print(f"🔧 validate_phone: ОШИБКА - российский номер '{digits_only}' длина {len(digits_only)}, нужно 11")
                return ""
        
        # ДРУГОЙ НОМЕР (не начинается с +, 7, 8) - оставляем как есть
        print(f"🔧 validate_phone: Другой номер: '{digits_only}'")
        return digits_only

# Для обратной совместимости (если где-то используется bool версия)
def validate_phone_bool(phone_str: str) -> bool:
    """Совместимость: возвращает bool вместо строки"""
    return bool(validate_phone(phone_str))


print("✅ Модуль validation.py загружен.")
