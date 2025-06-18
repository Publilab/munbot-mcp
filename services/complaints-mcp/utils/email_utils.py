import os
import base64
from email.message import EmailMessage
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

# Alcance necesario para enviar correos con Gmail API
SCOPES = ['https://www.googleapis.com/auth/gmail.send']


def gmail_authenticate():
    """
    Autentica con la Gmail API usando OAuth2. Guarda y reutiliza el token en token.json.
    """
    creds = None
    token_path = os.path.join(os.path.dirname(__file__), 'token.json')
    creds_path = os.path.join(os.path.dirname(__file__), 'credentials.json')
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
        # Guarda el token para futuros usos
        with open(token_path, 'w') as token:
            token.write(creds.to_json())
    return creds


def send_email(to, subject, body):
    """
    Envía un correo usando la Gmail API.
    Args:
        to (str): Correo destinatario
        subject (str): Asunto
        body (str): Cuerpo del mensaje
    Returns:
        dict: Respuesta de la API de Gmail
    Raises:
        Exception: Si ocurre un error en el envío
    """
    creds = gmail_authenticate()
    service = build('gmail', 'v1', credentials=creds)
    message = EmailMessage()
    message.set_content(body)
    # Usa GMAIL_FROM si está definido, si no, usa el correo autenticado
    from_addr = os.getenv('GMAIL_FROM') or creds._id_token.get('email') or 'me'
    message['To'] = to
    message['From'] = from_addr
    message['Subject'] = subject

    # Codifica el mensaje en base64 para la API
    encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    create_message = {'raw': encoded_message}

    try:
        send_message = (
            service.users().messages().send(userId="me", body=create_message).execute()
        )
        return send_message
    except Exception as e:
        # Puedes loggear aquí si lo deseas
        raise Exception(f"Error enviando correo con Gmail API: {e}")
