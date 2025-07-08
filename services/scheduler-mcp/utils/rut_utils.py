import re

def validar_y_formatear_rut(rut: str) -> str | None:
    """
    Valida un RUT chileno y lo devuelve formateado si es válido.
    Ejemplo de formato de entrada: "12.345.678-K", "12345678-K", "12345678K".
    Devuelve el RUT en formato "12.345.678-K" si es válido, o None si no lo es.
    """
    if not isinstance(rut, str):
        return None

    rut_limpio = re.sub(r'[.\-]', '', rut).upper()
    
    if not re.match(r'^\d{7,8}[0-9K]$', rut_limpio):
        return None

    cuerpo = rut_limpio[:-1]
    dv_ingresado = rut_limpio[-1]

    try:
        cuerpo_int = int(cuerpo)
    except ValueError:
        return None

    # Algoritmo de cálculo de dígito verificador
    suma = 0
    multiplo = 2
    for d in reversed(cuerpo):
        suma += int(d) * multiplo
        multiplo += 1
        if multiplo == 8:
            multiplo = 2
    
    resto = suma % 11
    dv_calculado = 11 - resto
    
    if dv_calculado == 11:
        dv_calculado_str = '0'
    elif dv_calculado == 10:
        dv_calculado_str = 'K'
    else:
        dv_calculado_str = str(dv_calculado)

    if dv_calculado_str == dv_ingresado:
        # Formatear el RUT
        cuerpo_formateado = f"{cuerpo_int:,}".replace(",", ".")
        return f"{cuerpo_formateado}-{dv_ingresado}"
    
    return None