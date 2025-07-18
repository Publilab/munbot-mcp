import os
from typing import Iterable
import requests
from psycopg2.extras import RealDictCursor

from db import get_conn
from notifications import send_email

META_PHONE_ID = os.getenv('META_PHONE_ID')
META_TOKEN = os.getenv('META_TOKEN')


def fetch_tomorrow_confirmed(conn) -> Iterable[dict]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        """SELECT * FROM appointments
           WHERE fecha = CURRENT_DATE + INTERVAL '1 day'
             AND disponible=TRUE AND confirmada=TRUE"""
    )
    return cur.fetchall()


def send_whatsapp(cita):
    if not META_PHONE_ID or not META_TOKEN:
        return
    try:
        phone_number = cita['usuario_whatsapp'].replace('+', '')
        url = f"https://graph.facebook.com/v19.0/{META_PHONE_ID}/messages"
        hi = cita["hora_inicio"]
        hf = cita["hora_fin"]
        hi_str = hi.strftime("%H:%M") if isinstance(hi, time) else str(hi)[:5]
        hf_str = hf.strftime("%H:%M") if isinstance(hf, time) else str(hf)[:5]
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {"body": f"Recordatorio: Su cita es mañana {cita['fecha']} a las {hi_str}-{hf_str} con {cita['funcionario_nombre']}."}
        }
        headers = {
            "Authorization": f"Bearer {META_TOKEN}",
            "Content-Type": "application/json",
        }
        requests.post(url, json=payload, headers=headers)
    except Exception as e:
        print(f"Error al enviar WhatsApp a {cita['usuario_whatsapp']}: {e}")


def send_reminder(dry: bool = False):
    with get_conn() as conn:
        citas = fetch_tomorrow_confirmed(conn)
    count = 0
    for cita in citas:
        if not dry:
            hi = cita["hora_inicio"]
            hf = cita["hora_fin"]
            hi_str = hi.strftime("%H:%M") if isinstance(hi, time) else str(hi)[:5]
            hf_str = hf.strftime("%H:%M") if isinstance(hf, time) else str(hf)[:5]
            send_email(
                cita['usuario_email'],
                'Recordatorio de cita municipal',
                'email/reminder.html',
                usuario=cita['usuario_nombre'],
                fecha_legible=str(cita['fecha']),
                hora=f"{hi_str}-{hf_str}",
            )
            if cita.get('usuario_whatsapp'):
                send_whatsapp(cita)
        count += 1
    if dry:
        print(f"{count} correos de recordatorio generados")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--dry', action='store_true', help='No enviar correos')
    args = parser.parse_args()
    send_reminder(dry=args.dry)
