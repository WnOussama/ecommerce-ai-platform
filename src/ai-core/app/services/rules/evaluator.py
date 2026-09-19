"""
Rule Evaluator - matching pur, sans effet de bord.

Évalue une liste de règles (déjà triées par priorité, déjà chargées via
RuleRepository.list_active()) contre un message/intention et retourne la
PREMIÈRE règle qui matche. Aucun accès base de données ici - c'est
volontairement une fonction pure pour rester testable sans DB.
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol


class RuleLike(Protocol):
    """N'importe quel objet portant ces trois attributs (le modèle Rule les a tous)."""

    conditions: Dict[str, Any]
    action: Dict[str, Any]


@dataclass
class RuleMatch:
    rule: RuleLike
    action: Dict[str, Any]


def _contains_keyword(message_lower: str, keyword_lower: str) -> bool:
    """
    Vrai si `keyword_lower` apparaît comme mot (ou expression) entier.

    Un simple `in` faisait matcher "cart" dans "carte" (bancaire) : un
    visiteur pouvait obtenir un vrai coupon de panier abandonné en
    mentionnant sa carte. `\\w` est Unicode : les accents sont des lettres.
    """
    if not keyword_lower:
        return False
    pattern = r"(?<!\w)" + re.escape(keyword_lower) + r"(?!\w)"
    return re.search(pattern, message_lower) is not None


class RuleEvaluator:
    """
    Conditions supportées (clés de `rule.conditions`):
      - "intent": str - doit correspondre exactement à l'intention détectée
      - "keywords_any": list[str] - au moins un mot-clé présent dans le
        message (insensible à la casse, MOT ENTIER: "cart" ne matche pas
        "carte" ni "hi" "machine"; une expression comme "come back" est
        cherchée telle quelle, entourée de frontières de mot)
      - "keywords_all": list[str] - tous les mots-clés doivent être présents
      - "first_message": bool - true = seulement au premier message de la
        conversation (ex: coupon de bienvenue), false = jamais au premier
      - "min_cart_total": number - le total du panier, fourni par la boutique
        (jamais par le navigateur), doit être au moins égal à cette valeur
        (ex: coupon de palier). Sans total de panier connu, la règle ne matche pas.

    Toutes les clés présentes doivent matcher (ET logique). Une règle sans
    aucune de ces clés ne matche jamais - une règle "vide" ne doit pas
    s'appliquer à tout par accident.
    """

    @staticmethod
    def _matches_conditions(
        conditions: Dict[str, Any],
        intent: str,
        message_lower: str,
        is_first_message: bool = False,
        cart_total: Optional[float] = None,
    ) -> bool:
        if not conditions:
            return False

        known_keys = ("intent", "keywords_any", "keywords_all", "first_message", "min_cart_total")
        if not any(key in conditions for key in known_keys):
            return False

        if "intent" in conditions and conditions["intent"] != intent:
            return False

        if "keywords_any" in conditions:
            keywords = [kw.lower() for kw in conditions["keywords_any"]]
            if not any(_contains_keyword(message_lower, kw) for kw in keywords):
                return False

        if "keywords_all" in conditions:
            keywords = [kw.lower() for kw in conditions["keywords_all"]]
            if not all(_contains_keyword(message_lower, kw) for kw in keywords):
                return False

        if "first_message" in conditions and bool(conditions["first_message"]) != is_first_message:
            return False

        if "min_cart_total" in conditions:
            threshold = conditions["min_cart_total"]
            if (
                isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or cart_total is None
                or cart_total < threshold
            ):
                return False

        return True

    def evaluate(
        self,
        rules: List[RuleLike],
        intent: str,
        message: str,
        *,
        is_first_message: bool = False,
        cart_total: Optional[float] = None,
    ) -> Optional[RuleMatch]:
        """
        Retourne la première règle qui matche, dans l'ordre fourni (déjà
        trié par priorité croissante par l'appelant) - ou None.
        """
        message_lower = message.lower()

        for rule in rules:
            if self._matches_conditions(
                rule.conditions or {}, intent, message_lower, is_first_message, cart_total
            ):
                return RuleMatch(rule=rule, action=rule.action or {})

        return None
