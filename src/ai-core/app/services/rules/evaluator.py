"""
Rule Evaluator - matching pur, sans effet de bord.

Évalue une liste de règles (déjà triées par priorité, déjà chargées via
RuleRepository.list_active()) contre un message/intention et retourne la
PREMIÈRE règle qui matche. Aucun accès base de données ici - c'est
volontairement une fonction pure pour rester testable sans DB.
"""

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


class RuleEvaluator:
    """
    Conditions supportées (clés de `rule.conditions`):
      - "intent": str - doit correspondre exactement à l'intention détectée
      - "keywords_any": list[str] - au moins un mot-clé présent dans le
        message (insensible à la casse)
      - "keywords_all": list[str] - tous les mots-clés doivent être présents

    Toutes les clés présentes doivent matcher (ET logique). Une règle sans
    aucune de ces clés ne matche jamais - une règle "vide" ne doit pas
    s'appliquer à tout par accident.
    """

    @staticmethod
    def _matches_conditions(conditions: Dict[str, Any], intent: str, message_lower: str) -> bool:
        if not conditions:
            return False

        known_keys = ("intent", "keywords_any", "keywords_all")
        if not any(key in conditions for key in known_keys):
            return False

        if "intent" in conditions and conditions["intent"] != intent:
            return False

        if "keywords_any" in conditions:
            keywords = [kw.lower() for kw in conditions["keywords_any"]]
            if not any(kw in message_lower for kw in keywords):
                return False

        if "keywords_all" in conditions:
            keywords = [kw.lower() for kw in conditions["keywords_all"]]
            if not all(kw in message_lower for kw in keywords):
                return False

        return True

    def evaluate(self, rules: List[RuleLike], intent: str, message: str) -> Optional[RuleMatch]:
        """
        Retourne la première règle qui matche, dans l'ordre fourni (déjà
        trié par priorité croissante par l'appelant) - ou None.
        """
        message_lower = message.lower()

        for rule in rules:
            if self._matches_conditions(rule.conditions or {}, intent, message_lower):
                return RuleMatch(rule=rule, action=rule.action or {})

        return None
