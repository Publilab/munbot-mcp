import phonenumbers
from phonenumbers import PhoneNumberFormat, PhoneNumberType, phonenumberutil

def validar_telefono_movil(numero_raw: str, region: str = "CL") -> str | None:
    """
    Retorna el número formateado E.164 (+56912345678)
    solo si es válido, posible y de tipo móvil.
    """
    try:
        num = phonenumbers.parse(numero_raw, region)
    except phonenumbers.NumberParseException:
        return None

    if not phonenumbers.is_valid_number(num):
        return None

    tipo = phonenumberutil.number_type(num)
    if tipo not in (PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE):
        return None

    return phonenumbers.format_number(num, PhoneNumberFormat.E164)
