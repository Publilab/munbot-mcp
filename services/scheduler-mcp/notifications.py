import os
import smtplib
from email.message import EmailMessage

def send_email(to, subject, body):
    SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.sendgrid.net')
    SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
    SMTP_USER = os.getenv('SMTP_USER', 'apikey')
    SMTP_PASS = os.getenv('SMTP_PASS', '')
    FROM = os.getenv('SENDER_EMAIL', SMTP_USER)
    if not to:
        return
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = FROM
    msg['To'] = to
    msg.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.starttls()
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(msg)
    except Exception as e:
        print(f"Error enviando correo: {e}")

def send_whatsapp(to, body):
    # Placeholder para integración real
    print(f"Enviando WhatsApp a {to}: {body}")
    # Puedes integrar Twilio, WhatsApp Cloud API, etc.
