# Clasificador automático de departamentos según palabras clave

def clasificar_departamento(mensaje):
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
