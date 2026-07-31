"""
Mock LLM Provider - Pour développement et tests sans dépendance externe
Production-ready avec réponses intelligentes basées sur patterns
"""

import asyncio
import logging
import random
import time
from typing import Any, Dict, List, Optional

from app.domain.entities.models import LLMUsage

logger = logging.getLogger(__name__)


class MockLLMResponse:
    """Réponse standardisée du Mock LLM"""

    def __init__(
        self, content: str, input_tokens: int = 0, output_tokens: int = 0, latency_ms: int = 0
    ):
        self.content = content
        self.usage = LLMUsage(
            input_tokens=input_tokens, output_tokens=output_tokens, model="mock-llm-v1"
        )
        self.model = "mock-llm-v1"
        self.finish_reason = "stop"
        self.latency_ms = latency_ms


class MockLLMProvider:
    """
    Mock LLM Provider avec réponses contextuelles intelligentes.

    Utilisation:
    - Développement local sans clé API
    - Tests unitaires et d'intégration
    - CI/CD pipelines
    - Démos sans coût

    Features:
    - Réponses basées sur patterns (prix, stock, produit, etc.)
    - Simulation de latence réaliste
    - Comptage de tokens simulé
    - Support multi-langue (FR/EN)
    """

    RESPONSE_PATTERNS: Dict[str, Dict[str, Any]] = {
        "price": {
            "keywords": ["prix", "price", "coût", "cost", "combien", "tarif", "how much"],
            "responses": [
                "Le prix de ce produit est de {price}€. Souhaitez-vous plus d'informations sur ses caractéristiques ?",
                "Ce produit est actuellement à {price}€. N'hésitez pas si vous avez d'autres questions !",
                "Le tarif est de {price}€. Nous proposons également la livraison gratuite à partir de 50€.",
            ],
        },
        "stock": {
            "keywords": ["stock", "disponible", "available", "dispo", "en stock", "rupture"],
            "responses": [
                "Ce produit est actuellement en stock. Livraison estimée sous 2-3 jours ouvrés.",
                "Bonne nouvelle ! L'article est disponible et peut être expédié dès aujourd'hui.",
                "Le produit est disponible. Souhaitez-vous l'ajouter à votre panier ?",
            ],
        },
        "shipping": {
            "keywords": ["livraison", "shipping", "délai", "delivery", "expédition", "envoyer"],
            "responses": [
                "La livraison standard est de 2-3 jours ouvrés. Livraison express disponible en 24h.",
                "Nous livrons sous 2-5 jours selon votre localisation. La livraison est gratuite dès 50€.",
                "L'expédition se fait sous 24-48h. Vous recevrez un email de suivi.",
            ],
        },
        "return": {
            "keywords": ["retour", "return", "rembours", "refund", "échange", "exchange"],
            "responses": [
                "Vous disposez de 30 jours pour retourner votre article. Le remboursement est effectué sous 5-7 jours.",
                "Notre politique de retour est simple : 30 jours pour changer d'avis, retour gratuit.",
                "Pour effectuer un retour, rendez-vous dans votre espace client ou contactez notre service client.",
            ],
        },
        "order": {
            "keywords": ["commande", "order", "suivi", "tracking", "où est", "colis"],
            "responses": [
                "Pour suivre votre commande, consultez votre espace client ou utilisez le numéro de suivi reçu par email.",
                "Votre commande est en cours de traitement. Vous recevrez un email dès l'expédition.",
                "Je peux vous aider à localiser votre colis. Avez-vous votre numéro de commande ?",
            ],
        },
        "recommendation": {
            "keywords": ["recommand", "suggest", "conseil", "similaire", "meilleur", "populaire"],
            "responses": [
                "Basé sur votre recherche, je vous recommande nos produits les mieux notés de cette catégorie.",
                "Voici quelques suggestions qui pourraient vous plaire. Souhaitez-vous des détails sur l'un d'eux ?",
                "Nos clients apprécient particulièrement ces articles. Puis-je vous en dire plus ?",
            ],
        },
        "coupon": {
            "keywords": [
                "code promo",
                "promo",
                "réduction",
                "coupon",
                "discount",
                "remise",
                "offre",
            ],
            "responses": [
                "Bonne nouvelle ! Utilisez le code BIENVENUE10 pour obtenir 10% de réduction sur votre première commande.",
                "Actuellement, profitez de 15% de réduction avec le code FLASH15 (valable 24h).",
                "Je peux vous proposer une offre spéciale. Souhaitez-vous recevoir un code promo personnalisé ?",
            ],
        },
        "product": {
            "keywords": ["produit", "product", "article", "cherche", "recherche", "trouver"],
            "responses": [
                "Je serais ravi de vous aider à trouver le produit idéal. Pouvez-vous me préciser vos critères ?",
                "Nous avons une large gamme de produits. Qu'est-ce qui vous intéresse particulièrement ?",
                "Je peux vous présenter nos produits. Avez-vous une préférence de marque ou de budget ?",
            ],
        },
        "help": {
            "keywords": ["aide", "help", "assistance", "problème", "issue", "support"],
            "responses": [
                "Je suis là pour vous aider ! Pouvez-vous me décrire votre problème plus en détail ?",
                "Notre équipe est à votre disposition. Comment puis-je vous assister ?",
                "N'hésitez pas à me poser vos questions, je ferai de mon mieux pour vous aider.",
            ],
        },
        "greeting": {
            "keywords": ["bonjour", "hello", "salut", "hi", "bonsoir", "hey"],
            "responses": [
                "Bonjour ! Je suis l'assistant IA de votre boutique. Comment puis-je vous aider aujourd'hui ?",
                "Bonjour et bienvenue ! Je suis à votre disposition pour toute question.",
                "Bonjour ! Ravi de vous accueillir. Que puis-je faire pour vous ?",
            ],
        },
        "thanks": {
            "keywords": ["merci", "thank", "thanks", "parfait", "super", "excellent"],
            "responses": [
                "Je vous en prie ! N'hésitez pas si vous avez d'autres questions.",
                "Avec plaisir ! Je reste disponible si vous avez besoin d'aide.",
                "Merci à vous ! Bonne continuation sur notre boutique.",
            ],
        },
    }

    DEFAULT_RESPONSES = [
        "Je suis l'assistant IA de votre boutique. Comment puis-je vous aider ?",
        "Je serais ravi de vous assister. Pouvez-vous préciser votre demande ?",
        "N'hésitez pas à me poser vos questions sur nos produits, livraisons ou commandes.",
        "Je suis là pour vous aider avec vos achats. Que recherchez-vous ?",
    ]

    def __init__(
        self, simulate_latency: bool = True, min_latency_ms: int = 50, max_latency_ms: int = 200
    ):
        """
        Initialise le Mock LLM Provider.

        Args:
            simulate_latency: Si True, simule une latence réaliste
            min_latency_ms: Latence minimum simulée
            max_latency_ms: Latence maximum simulée
        """
        self.simulate_latency = simulate_latency
        self.min_latency_ms = min_latency_ms
        self.max_latency_ms = max_latency_ms
        logger.info("MockLLMProvider initialized")

    async def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 1000,
        **kwargs,
    ) -> MockLLMResponse:
        """
        Génère une réponse basée sur les patterns détectés.

        Args:
            messages: Liste de messages au format OpenAI
            temperature: Ignoré (pour compatibilité API)
            max_tokens: Ignoré (pour compatibilité API)

        Returns:
            MockLLMResponse avec contenu et métadonnées
        """
        start_time = time.time()

        # Extraire le dernier message utilisateur
        user_message = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        # Simuler latence réseau
        if self.simulate_latency:
            delay = random.uniform(self.min_latency_ms / 1000, self.max_latency_ms / 1000)
            await asyncio.sleep(delay)

        # Générer réponse
        response_text = self._generate_response(user_message)

        # Calculer tokens simulés
        input_tokens = self._count_tokens(messages)
        output_tokens = len(response_text.split()) * 2  # Approximation

        latency_ms = int((time.time() - start_time) * 1000)

        logger.debug(
            "MockLLM generated response",
            extra={
                "input_length": len(user_message),
                "output_length": len(response_text),
                "latency_ms": latency_ms,
            },
        )

        return MockLLMResponse(
            content=response_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )

    async def chat(self, message: str, context: Optional[str] = None, **kwargs) -> str:
        """
        Interface simplifiée pour le chat.

        Args:
            message: Message de l'utilisateur
            context: Contexte optionnel

        Returns:
            Réponse textuelle
        """
        messages = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": message})

        response = await self.generate(messages, **kwargs)
        return response.content

    def _generate_response(self, message: str) -> str:
        """Génère une réponse basée sur le message."""
        # Les appels random.choice/randint ci-dessous choisissent un texte de
        # démo ou un prix factice, sans usage cryptographique ou sécurité.
        message_lower = message.lower().strip()

        if not message_lower:
            return random.choice(self.DEFAULT_RESPONSES)  # NOSONAR

        # Chercher un pattern correspondant
        best_match = None
        best_score = 0

        for pattern_name, pattern_data in self.RESPONSE_PATTERNS.items():
            keywords = pattern_data["keywords"]
            score = sum(1 for kw in keywords if kw in message_lower)

            if score > best_score:
                best_score = score
                best_match = pattern_name

        if best_match and best_score > 0:
            responses = self.RESPONSE_PATTERNS[best_match]["responses"]
            response = random.choice(responses)  # NOSONAR

            # Substituer les variables si présentes
            if "{price}" in response:
                prices = [19.99, 29.99, 49.99, 79.99]
                response = response.format(price=random.choice(prices))  # NOSONAR

            return response

        return random.choice(self.DEFAULT_RESPONSES)  # NOSONAR

    def _count_tokens(self, messages: List[Dict[str, str]]) -> int:
        """Compte approximativement les tokens des messages."""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            # Approximation: ~1.3 tokens par mot
            total += int(len(content.split()) * 1.3)
        return max(total, 10)

    def count_tokens(self, text: str) -> int:
        """Compte les tokens d'un texte (approximation)."""
        return int(len(text.split()) * 1.3)

    def get_model_name(self) -> str:
        """Retourne le nom du modèle."""
        return "mock-llm-v1"

    def is_available(self) -> bool:
        """Le mock est toujours disponible."""
        return True
