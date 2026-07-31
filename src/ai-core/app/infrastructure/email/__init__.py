"""
Email Infrastructure - Abstraction multi-provider
"""

from app.infrastructure.email.provider_factory import (
    BaseEmailProvider,
    EmailProviderFactory,
    MockEmailProvider,
    SMTPEmailProvider,
    get_email_provider,
)

__all__ = [
    "BaseEmailProvider",
    "EmailProviderFactory",
    "MockEmailProvider",
    "SMTPEmailProvider",
    "get_email_provider",
]
