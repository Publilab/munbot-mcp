import os
import smtplib
from email.message import EmailMessage
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)

def send_email(to: str, subject: str, template: str, **ctx):
    SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.sendgrid.net')
    # If the environment variable is empty or invalid fallback to default 587
    try:
        SMTP_PORT = int(os.getenv('SMTP_PORT') or 587)
    except ValueError:
        SMTP_PORT = 587
    SMTP_USER = os.getenv('SMTP_USER', 'apikey')
    SMTP_PASS = os.getenv('SMTP_PASS', '')
    FROM = os.getenv('SENDER_EMAIL', SMTP_USER)
    if not to:
        return
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = FROM
    msg['To'] = to
    tmpl = env.get_template(template)
    body = tmpl.render(**ctx)
    msg.set_content(body, subtype='html')
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
