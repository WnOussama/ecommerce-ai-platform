"""
Email Provider Factory - Abstraction multi-provider, même schéma que
app/infrastructure/llm/provider_factory.py (mock en dev/test, réel en
production - ici un relai SMTP standard, compatible avec n'importe quel
fournisseur : Resend, Mailgun, Gmail, Mailpit en local, etc.)
"""

import logging
import os
from abc import ABC, abstractmethod
from typing import List, Optional

from app.core.config.settings import settings

logger = logging.getLogger(__name__)


class BaseEmailProvider(ABC):
    """Interface abstraite pour tous les email providers."""

    @abstractmethod
    async def send(self, to: str, subject: str, html_body: str, text_body: str) -> bool:
        """Envoie un email. Retourne True si l'envoi a réussi."""
        pass


class MockEmailProvider(BaseEmailProvider):
    """
    Provider de test/dev - n'envoie rien sur le réseau, garde les emails
    en mémoire pour que les tests puissent les inspecter.
    """

    def __init__(self):
        self.sent_emails: List[dict] = []

    async def send(self, to: str, subject: str, html_body: str, text_body: str) -> bool:
        self.sent_emails.append(
            {"to": to, "subject": subject, "html_body": html_body, "text_body": text_body}
        )
        logger.info("Mock email 'sent'", extra={"to": to, "subject": subject})
        return True


class SMTPEmailProvider(BaseEmailProvider):
    """Provider SMTP réel - fonctionne avec tout relai SMTP standard."""

    def __init__(
        self,
        host: str,
        port: int,
        user: Optional[str],
        password: Optional[str],
        use_tls: bool,
        from_address: str,
        from_name: str,
    ):
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._use_tls = use_tls
        self._from_address = from_address
        self._from_name = from_name

    async def send(self, to: str, subject: str, html_body: str, text_body: str) -> bool:
        from email.message import EmailMessage

        import aiosmtplib

        message = EmailMessage()
        message["From"] = f"{self._from_name} <{self._from_address}>"
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")

        try:
            await aiosmtplib.send(
                message,
                hostname=self._host,
                port=self._port,
                username=self._user,
                password=self._password,
                start_tls=self._use_tls,
            )
            logger.info("Email sent", extra={"to": to, "subject": subject})
            return True
        except Exception as e:
            logger.error(
                "Failed to send email", extra={"to": to, "subject": subject, "error": str(e)}
            )
            return False


class EmailProviderFactory:
    """Factory pour créer le bon email provider selon la configuration."""

    _instance: Optional[BaseEmailProvider] = None

    @classmethod
    def get_provider(cls, force_new: bool = False) -> BaseEmailProvider:
        if cls._instance is not None and not force_new:
            return cls._instance

        provider_type = os.getenv("EMAIL_PROVIDER", "").lower() or settings.email.provider.lower()

        if provider_type == "smtp":
            cls._instance = SMTPEmailProvider(
                host=settings.email.smtp_host,
                port=settings.email.smtp_port,
                user=settings.email.smtp_user,
                password=settings.email.smtp_password,
                use_tls=settings.email.smtp_use_tls,
                from_address=settings.email.from_address,
                from_name=settings.email.from_name,
            )
        else:
            cls._instance = MockEmailProvider()

        return cls._instance

    @classmethod
    def reset(cls):
        cls._instance = None


def get_email_provider() -> BaseEmailProvider:
    return EmailProviderFactory.get_provider()
