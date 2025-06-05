import smtplib
from email.message import EmailMessage
import os

def send_email(to, subject, body):
    SMTP_HOST = os.getenv('SMTP_HOST')
    SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
    SMTP_USER = os.getenv('SMTP_USER')
    SMTP_PASS = os.getenv('SMTP_PASS')
    FROM = os.getenv('SMTP_FROM', SMTP_USER)

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = FROM
    msg['To'] = to
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(msg)
