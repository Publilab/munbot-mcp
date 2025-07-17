import os

from utils.email_utils import send_email as gmail_send_email
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
env = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "xml"]),
)

def send_email(to: str, subject: str, template: str, **ctx):
    if not to:
        return
    tmpl = env.get_template(template)
    body = tmpl.render(**ctx)
    try:
        gmail_send_email(to, subject, body)
    except Exception as e:
        print(f"Error enviando correo: {e}")

def send_whatsapp(to, body):
    # Placeholder para integración real
    print(f"Enviando WhatsApp a {to}: {body}")
    # Puedes integrar Twilio, WhatsApp Cloud API, etc.
