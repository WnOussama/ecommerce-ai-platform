"""
Tests unitaires pour RuleEvaluator - matching pur, sans DB.

RuleEvaluator est le coeur de la logique de règles (chat.py délègue tout
le matching ici) : il doit être testable indépendamment de la base et de
l'endpoint HTTP.
"""

from dataclasses import dataclass, field
from typing import Any, Dict

from app.services.rules.evaluator import RuleEvaluator


@dataclass
class _FakeRule:
    conditions: Dict[str, Any] = field(default_factory=dict)
    action: Dict[str, Any] = field(default_factory=dict)
    name: str = "fake-rule"


class TestRuleEvaluator:
    def setup_method(self):
        self.evaluator = RuleEvaluator()

    # -------------------------------------------------------------------
    # intent
    # -------------------------------------------------------------------

    def test_matches_on_exact_intent(self):
        rule = _FakeRule(
            conditions={"intent": "coupon_request"}, action={"type": "canned_response"}
        )
        match = self.evaluator.evaluate(
            [rule], intent="coupon_request", message="j'ai un code promo ?"
        )
        assert match is not None
        assert match.rule is rule
        assert match.action == {"type": "canned_response"}

    def test_no_match_on_different_intent(self):
        rule = _FakeRule(
            conditions={"intent": "coupon_request"}, action={"type": "canned_response"}
        )
        match = self.evaluator.evaluate([rule], intent="greeting", message="bonjour")
        assert match is None

    # -------------------------------------------------------------------
    # keywords_any / keywords_all
    # -------------------------------------------------------------------

    def test_keywords_any_matches_if_one_keyword_present(self):
        rule = _FakeRule(conditions={"keywords_any": ["panier", "abandon"]})
        match = self.evaluator.evaluate([rule], intent="general", message="Mon panier est vide")
        assert match is not None

    def test_keywords_any_is_case_insensitive(self):
        rule = _FakeRule(conditions={"keywords_any": ["PROMO"]})
        match = self.evaluator.evaluate(
            [rule], intent="general", message="je cherche un code promo"
        )
        assert match is not None

    def test_keywords_any_no_match_if_none_present(self):
        rule = _FakeRule(conditions={"keywords_any": ["panier", "abandon"]})
        match = self.evaluator.evaluate([rule], intent="general", message="quel est le prix ?")
        assert match is None

    def test_keywords_all_requires_every_keyword(self):
        rule = _FakeRule(conditions={"keywords_all": ["livraison", "gratuite"]})
        match = self.evaluator.evaluate(
            [rule], intent="general", message="la livraison est-elle gratuite ?"
        )
        assert match is not None

    def test_keywords_all_no_match_if_one_missing(self):
        rule = _FakeRule(conditions={"keywords_all": ["livraison", "gratuite"]})
        match = self.evaluator.evaluate([rule], intent="general", message="la livraison est rapide")
        assert match is None

    def test_intent_and_keywords_combine_with_and(self):
        rule = _FakeRule(conditions={"intent": "coupon_request", "keywords_any": ["fidèle"]})
        no_keyword = self.evaluator.evaluate(
            [rule], intent="coupon_request", message="un code promo svp"
        )
        assert no_keyword is None

        both = self.evaluator.evaluate(
            [rule], intent="coupon_request", message="je suis client fidèle, un code promo ?"
        )
        assert both is not None

    # -------------------------------------------------------------------
    # edge cases
    # -------------------------------------------------------------------

    def test_empty_conditions_never_matches(self):
        """Une règle sans conditions ne doit pas s'appliquer à tout par accident."""
        rule = _FakeRule(conditions={})
        match = self.evaluator.evaluate([rule], intent="general", message="n'importe quoi")
        assert match is None

    def test_unknown_condition_keys_never_match(self):
        rule = _FakeRule(conditions={"some_unsupported_key": "value"})
        match = self.evaluator.evaluate([rule], intent="general", message="n'importe quoi")
        assert match is None

    def test_no_rules_returns_none(self):
        assert self.evaluator.evaluate([], intent="general", message="bonjour") is None

    # -------------------------------------------------------------------
    # priority ordering (caller sorts, evaluator returns the first match)
    # -------------------------------------------------------------------

    def test_returns_first_matching_rule_in_given_order(self):
        high_priority = _FakeRule(
            conditions={"keywords_any": ["promo"]}, action={"type": "canned_response"}, name="first"
        )
        low_priority = _FakeRule(
            conditions={"keywords_any": ["promo"]},
            action={"type": "generate_coupon"},
            name="second",
        )
        match = self.evaluator.evaluate(
            [high_priority, low_priority], intent="general", message="un code promo ?"
        )
        assert match.rule.name == "first"
        assert match.action == {"type": "canned_response"}

    def test_skips_non_matching_rule_and_matches_next(self):
        first = _FakeRule(conditions={"intent": "greeting"}, action={"type": "a"}, name="first")
        second = _FakeRule(
            conditions={"keywords_any": ["promo"]}, action={"type": "b"}, name="second"
        )
        match = self.evaluator.evaluate(
            [first, second], intent="coupon_request", message="un code promo ?"
        )
        assert match.rule.name == "second"
