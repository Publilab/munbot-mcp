# Clasificador automático de departamentos según palabras clave

import re

def validar_rut(rut: str) -> bool:
    """Valida el formato y dígito verificador del RUT chileno."""
    # Limpiar el RUT
    rut = rut.replace(".", "").replace("-", "").upper()
    if not rut or len(rut) < 2:
        return False
    
    # Separar número y dígito verificador
    numero = rut[:-1]
    dv = rut[-1]
    
    # Validar que el número sea numérico
    if not numero.isdigit():
        return False
    
    # Calcular dígito verificador
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
    
    return dv == dvr

def clasificar_departamento(mensaje: str) -> int:
    """Clasifica el departamento según palabras clave en el mensaje."""
    mensaje = mensaje.lower()
    keywords = {
        1: ["robo", "asalto", "violencia", "seguridad", "policía", "delito"], # Seguridad
        2: ["bache", "calle", "vereda", "luminaria", "obra", "reparación", "infraestructura"], # Obras
        3: ["basura", "ruido", "contaminación", "ambiente", "árbol", "residuos", "agua"], # Ambiente
        4: [] # Otros
    }
    for dept, palabras in keywords.items():
        for palabra in palabras:
            if palabra in mensaje:
                return dept
    return 4  # Otros por defecto
