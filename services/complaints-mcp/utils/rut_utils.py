import re

def validar_y_formatear_rut(rut: str) -> str:
    """
    Valida y formatea un RUT chileno. Devuelve el RUT formateado si es válido, o None si no lo es.
    """
    if not rut:
        return None
    rut = rut.replace(".", "").replace("-", "").upper().strip()
    if len(rut) < 8:
        return None
    numero = rut[:-1]
    dv = rut[-1]
    if not numero.isdigit():
        return None
    suma = 0
    multiplicador = 2
    for r in reversed(numero):
        suma += int(r) * multiplicador
        multiplicador = multiplicador + 1 if multiplicador < 7 else 2
    dvr = 11 - (suma % 11)
    if dvr == 11:
        dvr = '0'
    elif dvr == 10:
        dvr = 'K'
    else:
        dvr = str(dvr)
    if dv != dvr:
        return None
    # Formatea como 12.345.678-5
    rut_formateado = f"{int(numero):,}".replace(",", ".") + "-" + dv
    return rut_formateado