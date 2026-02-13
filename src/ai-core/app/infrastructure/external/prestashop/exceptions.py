"""
PrestaShop API Exceptions

Exceptions spécifiques pour la gestion des erreurs de l'API PrestaShop.
Hiérarchie claire pour faciliter le handling dans les services.
"""

from typing import Optional, Dict, Any


class PrestaShopError(Exception):
    """
    Exception de base pour toutes les erreurs PrestaShop.

    Attributes:
        message: Description de l'erreur
        status_code: Code HTTP si applicable
        response_body: Corps de la réponse si disponible
        tenant_id: ID du tenant concerné
    """

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
    ):
        self.message = message
        self.status_code = status_code
        self.response_body = response_body
        self.tenant_id = tenant_id
        super().__init__(self.message)

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code:
            parts.append(f"[HTTP {self.status_code}]")
        if self.tenant_id:
            parts.append(f"[tenant: {self.tenant_id}]")
        return " ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        """Sérialise l'exception pour logging/API."""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "status_code": self.status_code,
            "tenant_id": self.tenant_id,
        }


class PrestaShopConnectionError(PrestaShopError):
    """
    Erreur de connexion au serveur PrestaShop.

    Causes possibles:
    - Serveur inaccessible
    - Timeout
    - Erreur DNS
    - Certificat SSL invalide
    """

    def __init__(
        self,
        message: str = "Failed to connect to PrestaShop server",
        original_error: Optional[Exception] = None,
        **kwargs,
    ):
        self.original_error = original_error
        if original_error:
            message = f"{message}: {str(original_error)}"
        super().__init__(message, **kwargs)


class PrestaShopAuthenticationError(PrestaShopError):
    """
    Erreur d'authentification API PrestaShop.

    Causes possibles:
    - API key invalide
    - API key expirée
    - API key sans permissions suffisantes
    - WebService désactivé sur PrestaShop
    """

    def __init__(
        self,
        message: str = "Authentication failed - invalid or expired API key",
        **kwargs,
    ):
        super().__init__(message, status_code=401, **kwargs)


class PrestaShopNotFoundError(PrestaShopError):
    """
    Ressource non trouvée sur PrestaShop.

    Causes possibles:
    - Produit/catégorie supprimé
    - ID invalide
    - Ressource non exposée via WebService
    """

    def __init__(
        self,
        resource_type: str,
        resource_id: Optional[int] = None,
        message: Optional[str] = None,
        **kwargs,
    ):
        self.resource_type = resource_type
        self.resource_id = resource_id

        if not message:
            if resource_id:
                message = f"{resource_type} with ID {resource_id} not found"
            else:
                message = f"{resource_type} not found"

        super().__init__(message, status_code=404, **kwargs)


class PrestaShopRateLimitError(PrestaShopError):
    """
    Rate limit atteint sur l'API PrestaShop.

    Le serveur PrestaShop ou un proxy a bloqué les requêtes.
    """

    def __init__(
        self,
        retry_after: Optional[int] = None,
        message: str = "Rate limit exceeded on PrestaShop API",
        **kwargs,
    ):
        self.retry_after = retry_after
        if retry_after:
            message = f"{message}. Retry after {retry_after} seconds"
        super().__init__(message, status_code=429, **kwargs)


class PrestaShopValidationError(PrestaShopError):
    """
    Erreur de validation des données PrestaShop.

    Causes possibles:
    - Format de réponse inattendu
    - Données manquantes
    - Types incompatibles
    """

    def __init__(
        self,
        message: str = "Invalid data received from PrestaShop",
        field: Optional[str] = None,
        **kwargs,
    ):
        self.field = field
        if field:
            message = f"{message} (field: {field})"
        super().__init__(message, status_code=422, **kwargs)


class PrestaShopServerError(PrestaShopError):
    """
    Erreur serveur PrestaShop (5xx).

    Le serveur PrestaShop a rencontré une erreur interne.
    """

    def __init__(
        self,
        message: str = "PrestaShop server error",
        **kwargs,
    ):
        super().__init__(message, **kwargs)

