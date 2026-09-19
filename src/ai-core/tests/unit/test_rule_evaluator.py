"""
Tests unitaires pour RuleEvaluator - matching pur, sans DB.

RuleEvaluator est le coeur de la logique de règles (chat.py délègue tout
le matching ici) : il doit être testable indépendamment de la base et de
l'endpoint HTTP.
"""

from dataclasses import dataclass, field
from typing import Any, Dict

import pytest

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

    def test_keyword_must_be_a_whole_word_not_a_substring(self):
        """Regression: "carte" (bank card) must not fire the "cart" abandoned cart rule."""
        rule = _FakeRule(conditions={"keywords_any": ["cart", "panier"]})
        evaluator = RuleEvaluator()
        assert (
            evaluator.evaluate([rule], "general", "Quel est le numéro de carte du client ?") is None
        )
        assert evaluator.evaluate([rule], "general", "Une cartouche d'encre") is None
        assert evaluator.evaluate([rule], "general", "my cart is empty") is not None

    def test_short_keyword_does_not_match_inside_other_words(self):
        rule = _FakeRule(conditions={"keywords_any": ["hi", "salut"]})
        evaluator = RuleEvaluator()
        assert evaluator.evaluate([rule], "general", "Do you sell a machine for this?") is None
        assert evaluator.evaluate([rule], "general", "Hi, I need help") is not None

    def test_keyword_matches_next_to_punctuation_and_accents(self):
        rule = _FakeRule(conditions={"keywords_any": ["abandonné", "fidèle"]})
        evaluator = RuleEvaluator()
        assert evaluator.evaluate([rule], "general", "Mon panier est abandonné!") is not None
        assert evaluator.evaluate([rule], "general", "Je suis client fidèle.") is not None

    def test_multi_word_keyword_is_matched_as_a_phrase(self):
        rule = _FakeRule(conditions={"keywords_any": ["come back", "been a while"]})
        evaluator = RuleEvaluator()
        assert evaluator.evaluate([rule], "general", "I want to come back to your shop") is not None
        assert evaluator.evaluate([rule], "general", "welcome backpack") is None

    # ------------------------------------------------------------------
    # first_message / min_cart_total (coupon de bienvenue et de palier)
    # ------------------------------------------------------------------

    def test_first_message_rule_only_matches_on_the_first_turn(self):
        rule = _FakeRule(conditions={"first_message": True})
        evaluator = RuleEvaluator()
        assert evaluator.evaluate([rule], "general", "Bonjour", is_first_message=True) is not None
        assert evaluator.evaluate([rule], "general", "Encore moi", is_first_message=False) is None

    def test_first_message_false_excludes_the_first_turn(self):
        rule = _FakeRule(conditions={"first_message": False})
        evaluator = RuleEvaluator()
        assert evaluator.evaluate([rule], "general", "Bonjour", is_first_message=True) is None
        assert evaluator.evaluate([rule], "general", "Suite", is_first_message=False) is not None

    def test_min_cart_total_matches_at_or_above_the_threshold(self):
        rule = _FakeRule(conditions={"min_cart_total": 100})
        evaluator = RuleEvaluator()
        assert evaluator.evaluate([rule], "general", "ok", cart_total=99.99) is None
        assert evaluator.evaluate([rule], "general", "ok", cart_total=100) is not None
        assert evaluator.evaluate([rule], "general", "ok", cart_total=250.5) is not None

    def test_min_cart_total_never_matches_without_a_known_cart(self):
        rule = _FakeRule(conditions={"min_cart_total": 100})
        assert RuleEvaluator().evaluate([rule], "general", "ok", cart_total=None) is None

    @pytest.mark.parametrize("bad_threshold", ["100", True, None, [100]])
    def test_min_cart_total_with_a_non_numeric_threshold_never_matches(self, bad_threshold):
        rule = _FakeRule(conditions={"min_cart_total": bad_threshold})
        assert RuleEvaluator().evaluate([rule], "general", "ok", cart_total=500) is None

    def test_welcome_and_threshold_combine_with_and(self):
        rule = _FakeRule(conditions={"first_message": True, "min_cart_total": 50})
        evaluator = RuleEvaluator()
        assert (
            evaluator.evaluate([rule], "general", "hi", is_first_message=True, cart_total=10)
            is None
        )
        assert (
            evaluator.evaluate([rule], "general", "hi", is_first_message=True, cart_total=80)
            is not None
        )

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
