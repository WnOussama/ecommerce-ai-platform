"""
Intent Classifier - matching pur, sans effet de bord.

Extrait de app/api/v1/endpoints/chat.py::_classify_intent (voir
docs/adr/0001-chat-turn-orchestrator-pure-decision-engine.md et la revue
d'architecture du 13/09/2026, Candidat 2). Remplace l'ancien dict plat où
`str` (sous-chaîne) et `re.Pattern` (regex) cohabitaient sans distinction
de type par une liste ordonnée de règles typées - chaque mot-clé est soit
explicitement littéral, soit explicitement regex.

Portée volontairement limitée à cette seule clarification de type : pas de
détection de langue ni de dispatch par langue pour l'instant - rien dans
le code existant ne détecte la langue du message, l'ajouter serait une
nouvelle brique d'infrastructure, pas juste un refactor de l'existant
(voir la revue d'architecture, Candidat 2, pour le choix de portée).
"""

import re
from dataclasses import dataclass
from typing import List, Union

DEFAULT_INTENT = "general"


@dataclass(frozen=True)
class LiteralKeyword:
    """Sous-chaîne insensible à la casse (le message est déjà lower()-isé avant matching)."""

    text: str

    def matches(self, message_lower: str) -> bool:
        return self.text in message_lower


@dataclass(frozen=True)
class RegexKeyword:
    """Motif regex précompilé, appliqué au message déjà lower()-isé."""

    pattern: "re.Pattern[str]"

    def matches(self, message_lower: str) -> bool:
        return bool(self.pattern.search(message_lower))


Keyword = Union[LiteralKeyword, RegexKeyword]


@dataclass(frozen=True)
class IntentRule:
    intent: str
    keywords: List[Keyword]


def _literals(*words: str) -> List[Keyword]:
    return [LiteralKeyword(w) for w in words]


# "commande" (nom : une commande existante) est une sous-chaîne de
# "commander"/"commandez" (verbe : passer commande) - un matching par
# simple sous-chaîne faisait classer "je veux commander des vêtements"
# (un visiteur qui veut acheter) en order_status (suivi d'une commande
# existante), donc le bot répondait "vous recevrez un email" au sujet
# d'une commande qui n'a jamais existé. Le matching par frontière de mot
# pour ce seul mot-clé referme cette faille, sans toucher au stemming
# volontaire des autres entrées (recommand*, rembours*, suggé*...).
_COMMANDE_NOUN = re.compile(r"\bcommandes?\b")

INTENT_RULES: List[IntentRule] = [
    IntentRule(
        "order_status",
        [
            RegexKeyword(_COMMANDE_NOUN),
            *_literals("order", "suivi", "tracking", "colis", "package", "shipment"),
        ],
    ),
    IntentRule(
        "product_search",
        _literals(
            "cherche", "recherche", "produit", "article", "trouver", "looking for", "search", "find", "product"
        ),
    ),
    IntentRule(
        "price_inquiry", _literals("prix", "price", "coût", "cost", "tarif", "combien", "how much")
    ),
    IntentRule(
        "shipping_info",
        _literals("livraison", "shipping", "délai", "expédition", "delivery", "arrive"),
    ),
    IntentRule(
        "return_request",
        _literals("retour", "rembours", "échange", "renvoyer", "return", "refund", "exchange"),
    ),
    IntentRule(
        "coupon_request",
        _literals("promo", "code", "réduction", "coupon", "remise", "discount", "voucher"),
    ),
    IntentRule(
        "recommendation",
        _literals("recommand", "suggé", "conseil", "similaire", "recommend", "suggest", "similar"),
    ),
    IntentRule("greeting", _literals("bonjour", "hello", "salut", "bonsoir", "hi", "hey")),
]


class IntentClassifier:
    """classify(message) -> nom de l'intention. Première règle qui matche gagne."""

    def __init__(self, rules: List[IntentRule] = INTENT_RULES):
        self._rules = rules

    def classify(self, message: str) -> str:
        message_lower = message.lower()
        for rule in self._rules:
            if any(kw.matches(message_lower) for kw in rule.keywords):
                return rule.intent
        return DEFAULT_INTENT
