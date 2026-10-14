from typing import Optional
import os
import smtplib
import ssl
from email.message import EmailMessage

SMTP_SERVER = "smtp.uni-stuttgart.de"
EMAIL_ENV_KEY = "SOLAR_PARK_RGB_NOTIFY_EMAIL"
USERNAME_ENV_KEY = "SOLAR_PARK_RGB_NOTIFY_USERNAME"
PASSWORD_ENV_KEY = "SOLAR_PARK_RGB_NOTIFY_PASSWORD"


class EmailCredentials:
    def __init__(self, email: str, username: str, password: str):
        """Create a new set of email credentials.

        Args:
            email (str): Email address.
            username (str): Username for the SMTP server.
            password (str): Password for the SMTP server.
        """
        self.email = email
        self.username = username
        self.password = password


def send_error_email(credentials: EmailCredentials, content: str):
    """Send an email to the same address as `credentials` indicating an error ocurred while trying to backup the database.

    Args:
        credentials (EmailCredentials): Email credentials to send the mail. The mail is sent to the same address.
        content (str): Email content.
    """
    msg = EmailMessage()
    msg["From"] = credentials.email
    msg["To"] = credentials.email
    msg["Subject"] = "Solar Park Error"
    msg.set_content(content)

    with smtplib.SMTP(SMTP_SERVER, port=587) as server:
        ssl_context = ssl.create_default_context()
        server.starttls(context=ssl_context)
        server.login(credentials.username, credentials.password)
        server.send_message(msg)


def get_credentials() -> Optional[EmailCredentials]:
    email = os.getenv(EMAIL_ENV_KEY)
    username = os.getenv(USERNAME_ENV_KEY)
    password = os.getenv(PASSWORD_ENV_KEY)
    if email is None or username is None or password is None:
        return None

    return EmailCredentials(email, username, password)