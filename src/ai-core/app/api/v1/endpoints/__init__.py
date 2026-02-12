"""
API v1 Endpoints
"""

from app.api.v1.endpoints import (
    health,
    chat,
    recommendations,
    coupons,
    faq,
    admin,
    analytics,
    tenants
)

__all__ = [
    "health",
    "chat",
    "recommendations",
    "coupons",
    "faq",
    "admin",
    "analytics",
    "tenants"
]

