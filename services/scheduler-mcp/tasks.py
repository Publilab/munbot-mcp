from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail
import json
import os
import pytz
import requests # Importar requests
from datetime import datetime, timedelta

# Configuración de WhatsApp (Meta Cloud API)
META_PHONE_ID = os.getenv('META_PHONE_ID')
META_TOKEN = os.getenv('META_TOKEN')

def send_reminder():
    """Enviar recordatorios un día antes de la cita"""
    # Leer citas desde el archivo JSON
    with open('databases/appointments.json', 'r') as f:
        citas = json.load(f)['citas']
    
    # Obtener fecha de mañana en Santiago de Chile
    tomorrow = datetime.now(pytz.timezone('America/Santiago')) + timedelta(days=1)
    tomorrow_str = tomorrow.strftime("%Y-%m-%d")
    
    for cita in citas:
        if (cita['fecha'] == tomorrow_str and 
            cita['AVLB'] == 0 and  # Ocupado
            cita['USU_CONF'] == 1):  # Confirmado
        
            # Enviar correo
            send_email(cita)
            
            # Enviar WhatsApp (si hay número)
            if cita.get('USU_WHATSAPP'):
                send_whatsapp(cita)

def send_email(cita):
    """Enviar correo de recordatorio usando SendGrid"""
    message = Mail(
        from_email=os.getenv('SENDER_EMAIL'),
        to_emails=cita['USU_MAIL'],
        subject='Recordatorio de cita municipal',
        plain_text_content=f"""
        Estimado {cita['USU_NAME']},

        Recordatorio: Su cita está programada para mañana {cita['fecha']} 
        en el horario {cita['hora']} con el funcionario {cita['FUNC']}.

        Código de cita: {cita['ID']}
        Funcionario: {cita['FUNC']} ({cita['COD_FUNC']})
        """
    )
    try:
        sg = SendGridAPIClient(os.getenv('SENDGRID_API_KEY'))
        sg.send(message)
    except Exception as e:
        print(f"Error al enviar correo a {cita['USU_MAIL']}: {str(e)}")

def send_whatsapp(cita):
    """Enviar mensaje de WhatsApp usando Meta Cloud API"""
    if not META_PHONE_ID or not META_TOKEN:
        print("Error: Faltan variables de entorno META_PHONE_ID o META_TOKEN")
        return
    try:
        phone_number = cita['USU_WHATSAPP'].replace('+', '')
        url = f"https://graph.facebook.com/v19.0/{META_PHONE_ID}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": { "body": f"Recordatorio: Su cita es mañana {cita['fecha']} a las {cita['hora']} con {cita['FUNC']}." }
        }
        headers = {
            "Authorization": f"Bearer {META_TOKEN}",
            "Content-Type": "application/json"
        }
        response = requests.post(url, json=payload, headers=headers)
    except Exception as e:
        print(f"Error al enviar WhatsApp a {cita['USU_WHATSAPP']}: {str(e)}")

def setup_scheduler():
    """Configurar el scheduler de APScheduler"""
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    
    # Programar el recordatorio diario a las 9:00 AM
    scheduler.add_job(
        send_reminder,
        'cron',
        hour=9,  # Hora en zona horaria de Santiago
        minute=0,
        id='daily_reminder'
    )
    
    scheduler.start()