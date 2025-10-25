import json
import datetime

# Configuración de funcionarios (ciclo infinito)
funcionarios = [
    ("Nien Nunb", "FN001"),
    ("Greedo", "FN002"),
    ("Lobot", "FN003"),
    ("Bib Fortuna", "FN004"),
    ("Salacious B. Crumb", "FN005"),
    ("Wicket", "FN006"),
    ("Poggle the Lesser", "FN007"),
    ("Sebulba", "FN008"),
    ("Zam Wesell", "FN009"),
    ("EV-9D9", "FN010")
]

# Rango de fechas (abril - mayo 2025)
start_date = datetime.date(2025, 4, 1)
end_date = datetime.date(2025, 5, 31)

# Horarios de atención (bloques de 30 minutos)
bloques = [
    "08:30-09:00",
    "09:00-09:30",
    "09:30-10:00",
    "10:00-10:30",
    "10:30-11:00",
    "11:00-11:30",
    "11:30-12:00",
    "12:00-12:30"
]

citas = []
id_counter = 1

current_date = start_date
while current_date <= end_date:
    # Validar días hábiles (lunes a viernes)
    if current_date.weekday() < 5:
        for bloque in bloques:
            # Asignar funcionario rotativo
            idx = (id_counter - 1) % len(funcionarios)
            func, cod = funcionarios[idx]
            
            cita = {
                "ID": f"C{id_counter:04d}",  # Formato C0001
                "FUNC": func,
                "COD_FUNC": cod,
                "MOTIV": "",
                "USU_NAME": "",
                "USU_MAIL": "",
                "USU_WHATSAPP": "",
                "AVLB": 0,  # Disponible por defecto (0 = disponible)
                "USU_CONF": 0,  # No confirmado por defecto
                "fecha": current_date.isoformat(),
                "hora": bloque
            }
            
            # Validación de campos obligatorios
            required = ["ID", "FUNC", "COD_FUNC", "MOTIV", "USU_NAME", "USU_MAIL", 
                        "USU_WHATSAPP", "AVLB", "USU_CONF", "fecha", "hora"]
            assert all(k in cita for k in required), "Falta campo requerido"
            
            citas.append(cita)
            id_counter += 1
    current_date += datetime.timedelta(days=1)

data = {"citas": citas}

# Serialización con formato legible
with open("appointments.json", "w") as f:
    json.dump(data, f, indent=4, ensure_ascii=False)

print(f"Archivo appointments.json generado con {len(citas)} citas.")