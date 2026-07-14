"""
Stateless Session Manager - Tout le contexte dans Redis

Architecture Stateless:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        STATELESS DESIGN PATTERN                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ❌ AVANT (Stateful):                                                           │
│     FastAPI Instance 1: context_user_A, context_user_B                          │
│     FastAPI Instance 2: context_user_C, context_user_D                          │
│     → Si instance 1 tombe, contexte perdu!                                      │
│     → Load balancer ne peut pas répartir les requêtes librement                 │
│                                                                                  │
│  ✅ APRÈS (Stateless):                                                          │
│     FastAPI Instance 1: Aucun état local                                        │
│     FastAPI Instance 2: Aucun état local                                        │
│     FastAPI Instance N: Aucun état local                                        │
│                    │                                                             │
│                    └───────────> Redis (tout le contexte)                       │
│                                  ├── session:user_A                             │
│                                  ├── session:user_B                             │
│                                  ├── context:conv_123                           │
│                                  └── rate_limit:tenant_X                        │
│                                                                                  │
│  Avantages:                                                                      │
│  • N'importe quelle instance peut traiter n'importe quelle requête              │
│  • Scaling horizontal illimité                                                   │
│  • Résilience: une instance tombe, les autres prennent le relais               │
│  • Déploiement sans downtime (rolling update)                                   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# CONFIGURATION
# =============================================================================


@dataclass
class SessionConfig:
    """Configuration des sessions"""

    # TTL
    session_ttl_seconds: int = 3600  # 1h
    conversation_ttl_seconds: int = 86400  # 24h
    context_ttl_seconds: int = 1800  # 30min

    # Limites
    max_context_size_bytes: int = 1024 * 1024  # 1MB
    max_messages_in_context: int = 50

    # Prefixes Redis
    prefix_session: str = "session"
    prefix_conversation: str = "conv"
    prefix_context: str = "ctx"
    prefix_user: str = "user"


# =============================================================================
# SESSION MODELS
# =============================================================================


@dataclass
class UserSession:
    """Session utilisateur (stateless, stockée dans Redis)"""

    session_id: str
    tenant_id: str
    user_id: Optional[str] = None
    customer_id: Optional[str] = None

    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None

    # Context
    current_conversation_id: Optional[str] = None
    page_context: Dict[str, Any] = field(default_factory=dict)

    # Tracking
    request_count: int = 0
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "customer_id": self.customer_id,
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "current_conversation_id": self.current_conversation_id,
            "page_context": self.page_context,
            "request_count": self.request_count,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UserSession":
        return cls(
            session_id=data["session_id"],
            tenant_id=data["tenant_id"],
            user_id=data.get("user_id"),
            customer_id=data.get("customer_id"),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_activity=datetime.fromisoformat(data["last_activity"]),
            expires_at=datetime.fromisoformat(data["expires_at"])
            if data.get("expires_at")
            else None,
            current_conversation_id=data.get("current_conversation_id"),
            page_context=data.get("page_context", {}),
            request_count=data.get("request_count", 0),
            ip_address=data.get("ip_address"),
            user_agent=data.get("user_agent"),
        )


@dataclass
class ConversationContext:
    """Contexte de conversation (stateless, stockée dans Redis)"""

    conversation_id: str
    tenant_id: str
    session_id: str

    # Messages
    messages: List[Dict[str, Any]] = field(default_factory=list)

    # État
    status: str = "active"
    primary_intent: Optional[str] = None

    # Metadata LLM
    total_tokens_used: int = 0
    total_cost: float = 0.0

    # Timestamps
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "messages": self.messages,
            "status": self.status,
            "primary_intent": self.primary_intent,
            "total_tokens_used": self.total_tokens_used,
            "total_cost": self.total_cost,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationContext":
        return cls(
            conversation_id=data["conversation_id"],
            tenant_id=data["tenant_id"],
            session_id=data["session_id"],
            messages=data.get("messages", []),
            status=data.get("status", "active"),
            primary_intent=data.get("primary_intent"),
            total_tokens_used=data.get("total_tokens_used", 0),
            total_cost=data.get("total_cost", 0.0),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )

    def add_message(self, role: str, content: str, metadata: Optional[Dict] = None) -> None:
        """Ajoute un message au contexte"""
        self.messages.append(
            {
                "role": role,
                "content": content,
                "timestamp": datetime.utcnow().isoformat(),
                "metadata": metadata or {},
            }
        )
        self.updated_at = datetime.utcnow()


# =============================================================================
# STATELESS SESSION STORE (REDIS)
# =============================================================================


class StatelessSessionStore:
    """
    Store de sessions 100% stateless utilisant Redis.

    Toutes les données sont dans Redis, aucun état local dans FastAPI.
    Permet le scaling horizontal illimité.
    """

    def __init__(
        self,
        redis_client,
        config: Optional[SessionConfig] = None,
    ):
        self._redis = redis_client
        self._config = config or SessionConfig()

    # =========================================================================
    # SESSION MANAGEMENT
    # =========================================================================

    async def create_session(
        self,
        tenant_id: str,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> UserSession:
        """Crée une nouvelle session"""
        import secrets

        session_id = session_id or f"sess_{secrets.token_urlsafe(16)}"

        session = UserSession(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            customer_id=customer_id,
            expires_at=datetime.utcnow() + timedelta(seconds=self._config.session_ttl_seconds),
            ip_address=ip_address,
            user_agent=user_agent,
        )

        # Stocker dans Redis
        key = self._session_key(tenant_id, session_id)
        await self._redis.set(
            key,
            json.dumps(session.to_dict()),
            ex=self._config.session_ttl_seconds,
        )

        logger.debug(f"Created session {session_id} for tenant {tenant_id}")

        return session

    async def get_session(
        self,
        tenant_id: str,
        session_id: str,
    ) -> Optional[UserSession]:
        """Récupère une session depuis Redis"""
        key = self._session_key(tenant_id, session_id)
        data = await self._redis.get(key)

        if not data:
            return None

        session = UserSession.from_dict(json.loads(data))

        # Mettre à jour last_activity
        session.last_activity = datetime.utcnow()
        session.request_count += 1

        # Refresh TTL
        await self._redis.set(
            key,
            json.dumps(session.to_dict()),
            ex=self._config.session_ttl_seconds,
        )

        return session

    async def update_session(
        self,
        session: UserSession,
    ) -> None:
        """Met à jour une session"""
        session.last_activity = datetime.utcnow()

        key = self._session_key(session.tenant_id, session.session_id)
        await self._redis.set(
            key,
            json.dumps(session.to_dict()),
            ex=self._config.session_ttl_seconds,
        )

    async def delete_session(
        self,
        tenant_id: str,
        session_id: str,
    ) -> bool:
        """Supprime une session"""
        key = self._session_key(tenant_id, session_id)
        result = await self._redis.delete(key)
        return result > 0

    # =========================================================================
    # CONVERSATION CONTEXT
    # =========================================================================

    async def create_conversation(
        self,
        tenant_id: str,
        session_id: str,
        conversation_id: Optional[str] = None,
    ) -> ConversationContext:
        """Crée un nouveau contexte de conversation"""
        import secrets

        conversation_id = conversation_id or f"conv_{secrets.token_urlsafe(12)}"

        context = ConversationContext(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            session_id=session_id,
        )

        # Stocker dans Redis
        key = self._conversation_key(tenant_id, conversation_id)
        await self._redis.set(
            key,
            json.dumps(context.to_dict()),
            ex=self._config.conversation_ttl_seconds,
        )

        # Lier à la session
        session = await self.get_session(tenant_id, session_id)
        if session:
            session.current_conversation_id = conversation_id
            await self.update_session(session)

        logger.debug(f"Created conversation {conversation_id}")

        return context

    async def get_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
    ) -> Optional[ConversationContext]:
        """Récupère un contexte de conversation"""
        key = self._conversation_key(tenant_id, conversation_id)
        data = await self._redis.get(key)

        if not data:
            return None

        return ConversationContext.from_dict(json.loads(data))

    async def update_conversation(
        self,
        context: ConversationContext,
    ) -> None:
        """Met à jour un contexte de conversation"""
        context.updated_at = datetime.utcnow()

        # Limiter le nombre de messages
        if len(context.messages) > self._config.max_messages_in_context:
            # Garder le premier message (système) et les N derniers
            context.messages = [context.messages[0]] + context.messages[
                -self._config.max_messages_in_context + 1 :
            ]

        key = self._conversation_key(context.tenant_id, context.conversation_id)

        data = json.dumps(context.to_dict())

        # Vérifier la taille
        if len(data.encode()) > self._config.max_context_size_bytes:
            logger.warning("Context too large, truncating messages")
            # Réduire le nombre de messages
            context.messages = context.messages[:10]
            data = json.dumps(context.to_dict())

        await self._redis.set(
            key,
            data,
            ex=self._config.conversation_ttl_seconds,
        )

    async def add_message_to_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict] = None,
    ) -> Optional[ConversationContext]:
        """Ajoute un message à une conversation"""
        context = await self.get_conversation(tenant_id, conversation_id)

        if not context:
            return None

        context.add_message(role, content, metadata)
        await self.update_conversation(context)

        return context

    # =========================================================================
    # GENERIC KEY-VALUE CONTEXT
    # =========================================================================

    async def set_context(
        self,
        tenant_id: str,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Stocke une valeur de contexte générique"""
        full_key = self._context_key(tenant_id, key)
        ttl = ttl_seconds or self._config.context_ttl_seconds

        await self._redis.set(
            full_key,
            json.dumps(value) if not isinstance(value, str) else value,
            ex=ttl,
        )

    async def get_context(
        self,
        tenant_id: str,
        key: str,
    ) -> Optional[Any]:
        """Récupère une valeur de contexte générique"""
        full_key = self._context_key(tenant_id, key)
        data = await self._redis.get(full_key)

        if not data:
            return None

        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return data

    async def delete_context(
        self,
        tenant_id: str,
        key: str,
    ) -> bool:
        """Supprime une valeur de contexte"""
        full_key = self._context_key(tenant_id, key)
        result = await self._redis.delete(full_key)
        return result > 0

    # =========================================================================
    # KEY BUILDERS
    # =========================================================================

    def _session_key(self, tenant_id: str, session_id: str) -> str:
        return f"{self._config.prefix_session}:{tenant_id}:{session_id}"

    def _conversation_key(self, tenant_id: str, conversation_id: str) -> str:
        return f"{self._config.prefix_conversation}:{tenant_id}:{conversation_id}"

    def _context_key(self, tenant_id: str, key: str) -> str:
        return f"{self._config.prefix_context}:{tenant_id}:{key}"

    # =========================================================================
    # STATS
    # =========================================================================

    async def get_tenant_stats(self, tenant_id: str) -> Dict[str, int]:
        """Statistiques des sessions d'un tenant"""
        session_pattern = f"{self._config.prefix_session}:{tenant_id}:*"
        conv_pattern = f"{self._config.prefix_conversation}:{tenant_id}:*"

        session_keys = []
        conv_keys = []

        # Scan des clés (attention: à utiliser avec parcimonie en prod)
        async for key in self._redis.scan_iter(match=session_pattern, count=100):
            session_keys.append(key)

        async for key in self._redis.scan_iter(match=conv_pattern, count=100):
            conv_keys.append(key)

        return {
            "active_sessions": len(session_keys),
            "active_conversations": len(conv_keys),
        }


# =============================================================================
# STATELESS REQUEST CONTEXT
# =============================================================================


@dataclass
class RequestContext:
    """
    Contexte de requête stateless.

    Créé au début de chaque requête, détruit à la fin.
    Toutes les données persistantes sont dans Redis.
    """

    request_id: str
    tenant_id: str
    session: Optional[UserSession] = None
    conversation: Optional[ConversationContext] = None

    # Metadata
    started_at: datetime = field(default_factory=datetime.utcnow)
    client_ip: Optional[str] = None
    endpoint: Optional[str] = None

    @property
    def session_id(self) -> Optional[str]:
        return self.session.session_id if self.session else None

    @property
    def conversation_id(self) -> Optional[str]:
        return self.conversation.conversation_id if self.conversation else None


class RequestContextManager:
    """
    Gestionnaire de contexte de requête stateless.

    Usage:
        async with request_context_manager.context(request) as ctx:
            # ctx.session disponible
            # ctx.conversation disponible
            pass
    """

    def __init__(self, session_store: StatelessSessionStore):
        self._store = session_store

    async def create_context(
        self,
        tenant_id: str,
        session_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        client_ip: Optional[str] = None,
        endpoint: Optional[str] = None,
    ) -> RequestContext:
        """Crée un contexte de requête"""
        import secrets

        request_id = f"req_{secrets.token_urlsafe(8)}"

        ctx = RequestContext(
            request_id=request_id,
            tenant_id=tenant_id,
            client_ip=client_ip,
            endpoint=endpoint,
        )

        # Charger ou créer la session
        if session_id:
            ctx.session = await self._store.get_session(tenant_id, session_id)

        if not ctx.session:
            ctx.session = await self._store.create_session(
                tenant_id=tenant_id,
                ip_address=client_ip,
            )

        # Charger la conversation si spécifiée
        if conversation_id:
            ctx.conversation = await self._store.get_conversation(tenant_id, conversation_id)
        elif ctx.session.current_conversation_id:
            ctx.conversation = await self._store.get_conversation(
                tenant_id,
                ctx.session.current_conversation_id,
            )

        return ctx

    async def save_context(self, ctx: RequestContext) -> None:
        """Sauvegarde le contexte à la fin de la requête"""
        if ctx.session:
            await self._store.update_session(ctx.session)

        if ctx.conversation:
            await self._store.update_conversation(ctx.conversation)


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "SessionConfig",
    "UserSession",
    "ConversationContext",
    "StatelessSessionStore",
    "RequestContext",
    "RequestContextManager",
]
