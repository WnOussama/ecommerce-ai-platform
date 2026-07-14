"""
Context Manager - Gestion avancée du contexte conversationnel
Implémente mémoire court-terme, résumé, compression
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


# ============================================================================
# TYPES
# ============================================================================


class MemoryType(str, Enum):
    SHORT_TERM = "short_term"  # Messages récents (window)
    WORKING = "working"  # Résumé de la session
    LONG_TERM = "long_term"  # Profil utilisateur persisté


@dataclass
class ContextWindow:
    """Fenêtre de contexte avec gestion des tokens"""

    messages: List[Dict[str, str]] = field(default_factory=list)
    total_tokens: int = 0
    max_tokens: int = 4000  # Réserve pour le prompt système et la réponse

    def add_message(self, role: str, content: str, token_count: int):
        """Ajoute un message, supprime les anciens si nécessaire"""
        # Vérifier si on dépasse la limite
        while self.total_tokens + token_count > self.max_tokens and self.messages:
            removed = self.messages.pop(0)
            self.total_tokens -= removed.get("token_count", 0)

        self.messages.append(
            {
                "role": role,
                "content": content,
                "token_count": token_count,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        self.total_tokens += token_count

    def get_messages(self) -> List[Dict[str, str]]:
        """Retourne les messages sans les métadonnées internes"""
        return [{"role": m["role"], "content": m["content"]} for m in self.messages]

    def clear(self):
        self.messages = []
        self.total_tokens = 0


@dataclass
class WorkingMemory:
    """Mémoire de travail - résumé de la conversation en cours"""

    summary: str = ""
    key_facts: List[str] = field(default_factory=list)
    current_intent: Optional[str] = None
    entities_mentioned: Dict[str, Any] = field(default_factory=dict)
    actions_taken: List[str] = field(default_factory=list)
    last_updated: datetime = field(default_factory=datetime.utcnow)

    def to_prompt_context(self) -> str:
        """Convertit en texte pour le prompt"""
        parts = []

        if self.summary:
            parts.append(f"Résumé de la conversation: {self.summary}")

        if self.key_facts:
            parts.append(f"Points clés: {'; '.join(self.key_facts)}")

        if self.entities_mentioned:
            entities_str = ", ".join(f"{k}: {v}" for k, v in self.entities_mentioned.items())
            parts.append(f"Informations mentionnées: {entities_str}")

        if self.actions_taken:
            parts.append(f"Actions effectuées: {', '.join(self.actions_taken)}")

        return "\n".join(parts) if parts else ""


@dataclass
class CustomerProfile:
    """Profil client persisté (mémoire long terme)"""

    customer_id: UUID = field(default_factory=uuid4)
    tenant_id: UUID = field(default_factory=uuid4)

    # Préférences apprises
    preferred_categories: List[str] = field(default_factory=list)
    preferred_price_range: Optional[Dict[str, float]] = None
    communication_style: str = "formal"  # formal, casual
    language: str = "fr"

    # Comportement
    avg_response_time: float = 0.0  # Temps moyen entre messages (secondes)
    typical_session_length: int = 5  # Nombre de messages par session

    # Historique résumé
    past_issues: List[str] = field(default_factory=list)  # Problèmes récurrents
    successful_recommendations: List[str] = field(default_factory=list)

    # Dates
    first_interaction: datetime = field(default_factory=datetime.utcnow)
    last_interaction: datetime = field(default_factory=datetime.utcnow)
    interaction_count: int = 0

    def to_prompt_context(self) -> str:
        """Convertit en contexte pour le prompt"""
        parts = []

        if self.preferred_categories:
            parts.append(f"Catégories préférées: {', '.join(self.preferred_categories[:5])}")

        if self.preferred_price_range:
            parts.append(
                f"Budget habituel: {self.preferred_price_range.get('min', 0)}-"
                f"{self.preferred_price_range.get('max', 'illimité')}€"
            )

        if self.past_issues:
            parts.append(f"Sujets de préoccupation passés: {', '.join(self.past_issues[:3])}")

        parts.append(f"Style de communication préféré: {self.communication_style}")

        return "\n".join(parts) if parts else "Nouveau client"


@dataclass
class ConversationState:
    """État complet d'une conversation"""

    conversation_id: UUID = field(default_factory=uuid4)
    tenant_id: UUID = field(default_factory=uuid4)
    customer_id: Optional[UUID] = None
    session_id: str = ""

    # Mémoires
    context_window: ContextWindow = field(default_factory=ContextWindow)
    working_memory: WorkingMemory = field(default_factory=WorkingMemory)
    customer_profile: Optional[CustomerProfile] = None

    # Navigation
    current_page: Optional[str] = None
    current_product_id: Optional[str] = None
    cart_items: List[Dict[str, Any]] = field(default_factory=list)

    # Métriques
    message_count: int = 0
    start_time: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)

    # État
    is_active: bool = True
    needs_human_handoff: bool = False


# ============================================================================
# CONTEXT MANAGER
# ============================================================================


class ContextManager:
    """
    Gestionnaire de contexte conversationnel.

    Responsabilités:
    - Gérer la fenêtre de contexte (short-term)
    - Maintenir la mémoire de travail (working memory)
    - Charger/persister le profil client (long-term)
    - Compresser le contexte quand nécessaire
    - Résumer les conversations longues
    """

    def __init__(
        self,
        cache,  # Redis pour stockage sessions
        repository,  # Repository pour profils persistés
        llm_service=None,  # Pour résumé/compression
        max_context_tokens: int = 4000,
        session_timeout_minutes: int = 30,
    ):
        self.cache = cache
        self.repository = repository
        self.llm = llm_service
        self.max_context_tokens = max_context_tokens
        self.session_timeout = timedelta(minutes=session_timeout_minutes)

        # Cache local pour sessions actives
        self._active_sessions: Dict[str, ConversationState] = {}

    async def get_or_create_state(
        self, tenant_id: UUID, session_id: str, customer_id: Optional[UUID] = None
    ) -> ConversationState:
        """Récupère ou crée un état de conversation"""

        cache_key = f"conv_state:{tenant_id}:{session_id}"

        # Vérifier cache local
        if cache_key in self._active_sessions:
            state = self._active_sessions[cache_key]
            if datetime.utcnow() - state.last_activity < self.session_timeout:
                return state
            else:
                # Session expirée, archiver et créer nouvelle
                await self._archive_state(state)
                del self._active_sessions[cache_key]

        # Vérifier Redis
        if self.cache:
            cached_data = await self.cache.get(cache_key)
            if cached_data:
                state = self._deserialize_state(cached_data)
                self._active_sessions[cache_key] = state
                return state

        # Créer nouvel état
        state = ConversationState(
            tenant_id=tenant_id,
            session_id=session_id,
            customer_id=customer_id,
            context_window=ContextWindow(max_tokens=self.max_context_tokens),
        )

        # Charger profil client si disponible
        if customer_id and self.repository:
            state.customer_profile = await self.repository.get_customer_profile(
                tenant_id, customer_id
            )

        self._active_sessions[cache_key] = state

        return state

    async def add_message(
        self,
        state: ConversationState,
        role: str,
        content: str,
        token_count: int,
        intent: Optional[str] = None,
        entities: Dict[str, Any] = None,
    ):
        """Ajoute un message et met à jour le contexte"""

        # Ajouter au context window
        state.context_window.add_message(role, content, token_count)
        state.message_count += 1
        state.last_activity = datetime.utcnow()

        # Mettre à jour working memory
        if intent:
            state.working_memory.current_intent = intent

        if entities:
            state.working_memory.entities_mentioned.update(entities)

        # Si la conversation devient longue, comprimer
        if state.message_count > 10 and state.message_count % 5 == 0:
            await self._update_working_memory_summary(state)

        # Persister en cache
        await self._persist_state(state)

    async def add_action(self, state: ConversationState, action: str):
        """Enregistre une action effectuée"""
        state.working_memory.actions_taken.append(action)
        state.working_memory.last_updated = datetime.utcnow()

    async def build_context_for_llm(
        self, state: ConversationState, current_message: str
    ) -> Dict[str, Any]:
        """
        Construit le contexte complet pour l'appel LLM.

        Returns:
            {
                "messages": [...],  # Historique formaté
                "system_context": "...",  # Contexte pour le prompt système
                "customer_context": "...",  # Info client
            }
        """
        context = {
            "messages": state.context_window.get_messages(),
            "system_context": "",
            "customer_context": "",
        }

        # Working memory context
        working_context = state.working_memory.to_prompt_context()
        if working_context:
            context["system_context"] = working_context

        # Customer profile context
        if state.customer_profile:
            context["customer_context"] = state.customer_profile.to_prompt_context()

        # Navigation context
        nav_parts = []
        if state.current_page:
            nav_parts.append(f"Page actuelle: {state.current_page}")
        if state.current_product_id:
            nav_parts.append(f"Produit consulté: {state.current_product_id}")
        if state.cart_items:
            cart_total = sum(
                item.get("price", 0) * item.get("quantity", 1) for item in state.cart_items
            )
            nav_parts.append(f"Panier: {len(state.cart_items)} articles, {cart_total:.2f}€")

        if nav_parts:
            context["navigation_context"] = "\n".join(nav_parts)

        return context

    async def _update_working_memory_summary(self, state: ConversationState):
        """Met à jour le résumé de la working memory via LLM"""
        if not self.llm:
            return

        try:
            # Construire le prompt de résumé
            messages_text = "\n".join(
                [
                    f"{m['role']}: {m['content']}"
                    for m in state.context_window.messages[-10:]  # Derniers 10 messages
                ]
            )

            summary_prompt = f"""Résume cette conversation de support client en 2-3 phrases.
Identifie:
1. Le sujet principal
2. Les informations clés mentionnées
3. Les actions effectuées

Conversation:
{messages_text}

Résumé:"""

            response = await self.llm.generate(
                [{"role": "user", "content": summary_prompt}],
                tenant_id=state.tenant_id,
            )

            state.working_memory.summary = response.content
            state.working_memory.last_updated = datetime.utcnow()

            logger.debug(f"Updated working memory summary for {state.session_id}")

        except Exception as e:
            logger.error(f"Failed to update working memory summary: {e}")

    async def _persist_state(self, state: ConversationState):
        """Persiste l'état en cache Redis"""
        if not self.cache:
            return

        cache_key = f"conv_state:{state.tenant_id}:{state.session_id}"
        serialized = self._serialize_state(state)

        await self.cache.set(cache_key, serialized, ex=int(self.session_timeout.total_seconds()))

    async def _archive_state(self, state: ConversationState):
        """Archive une session terminée"""
        if not self.repository:
            return

        # Mettre à jour le profil client avec les apprentissages
        if state.customer_id and state.customer_profile:
            profile = state.customer_profile

            # Mettre à jour les préférences basées sur les entités
            if "category" in state.working_memory.entities_mentioned:
                cat = state.working_memory.entities_mentioned["category"]
                if cat not in profile.preferred_categories:
                    profile.preferred_categories.append(cat)

            profile.last_interaction = datetime.utcnow()
            profile.interaction_count += 1

            await self.repository.save_customer_profile(profile)

        # Archiver la conversation
        await self.repository.archive_conversation(state)

    def _serialize_state(self, state: ConversationState) -> str:
        """Sérialise l'état pour Redis"""
        data = {
            "conversation_id": str(state.conversation_id),
            "tenant_id": str(state.tenant_id),
            "customer_id": str(state.customer_id) if state.customer_id else None,
            "session_id": state.session_id,
            "context_window": {
                "messages": state.context_window.messages,
                "total_tokens": state.context_window.total_tokens,
                "max_tokens": state.context_window.max_tokens,
            },
            "working_memory": {
                "summary": state.working_memory.summary,
                "key_facts": state.working_memory.key_facts,
                "current_intent": state.working_memory.current_intent,
                "entities_mentioned": state.working_memory.entities_mentioned,
                "actions_taken": state.working_memory.actions_taken,
            },
            "current_page": state.current_page,
            "current_product_id": state.current_product_id,
            "cart_items": state.cart_items,
            "message_count": state.message_count,
            "start_time": state.start_time.isoformat(),
            "last_activity": state.last_activity.isoformat(),
            "is_active": state.is_active,
            "needs_human_handoff": state.needs_human_handoff,
        }
        return json.dumps(data)

    def _deserialize_state(self, data: str) -> ConversationState:
        """Désérialise l'état depuis Redis"""
        obj = json.loads(data)

        state = ConversationState(
            conversation_id=UUID(obj["conversation_id"]),
            tenant_id=UUID(obj["tenant_id"]),
            customer_id=UUID(obj["customer_id"]) if obj["customer_id"] else None,
            session_id=obj["session_id"],
            message_count=obj["message_count"],
            start_time=datetime.fromisoformat(obj["start_time"]),
            last_activity=datetime.fromisoformat(obj["last_activity"]),
            is_active=obj["is_active"],
            needs_human_handoff=obj["needs_human_handoff"],
            current_page=obj.get("current_page"),
            current_product_id=obj.get("current_product_id"),
            cart_items=obj.get("cart_items", []),
        )

        # Context window
        cw_data = obj.get("context_window", {})
        state.context_window = ContextWindow(
            messages=cw_data.get("messages", []),
            total_tokens=cw_data.get("total_tokens", 0),
            max_tokens=cw_data.get("max_tokens", 4000),
        )

        # Working memory
        wm_data = obj.get("working_memory", {})
        state.working_memory = WorkingMemory(
            summary=wm_data.get("summary", ""),
            key_facts=wm_data.get("key_facts", []),
            current_intent=wm_data.get("current_intent"),
            entities_mentioned=wm_data.get("entities_mentioned", {}),
            actions_taken=wm_data.get("actions_taken", []),
        )

        return state

    async def end_conversation(self, state: ConversationState, reason: str = "completed"):
        """Termine une conversation proprement"""
        state.is_active = False

        # Archiver
        await self._archive_state(state)

        # Supprimer du cache
        cache_key = f"conv_state:{state.tenant_id}:{state.session_id}"
        self._active_sessions.pop(cache_key, None)

        if self.cache:
            await self.cache.delete(cache_key)

        logger.info(
            f"Conversation ended: {state.conversation_id}",
            extra={"reason": reason, "message_count": state.message_count},
        )

    async def request_human_handoff(self, state: ConversationState, reason: str):
        """Marque la conversation pour escalade humaine"""
        state.needs_human_handoff = True
        state.working_memory.key_facts.append(f"Escalade demandée: {reason}")

        await self._persist_state(state)

        logger.info(
            f"Human handoff requested for {state.conversation_id}", extra={"reason": reason}
        )
