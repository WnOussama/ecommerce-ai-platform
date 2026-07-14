"""
API v1 Endpoints
"""

from app.api.v1.endpoints import (
    admin,
    analytics,
    chat,
    coupons,
    faq,
    health,
    recommendations,
    tenants,
)

__all__ = ["health", "chat", "recommendations", "coupons", "faq", "admin", "analytics", "tenants"]
