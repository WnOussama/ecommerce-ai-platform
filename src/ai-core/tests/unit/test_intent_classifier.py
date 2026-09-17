"""
Tests for app/services/chat/intent_classifier.py.

No test existed for this module before - the ordering bug this file
guards against was only caught live in the browser: "How many days do I
have to return a product?" classified as product_search (matched the bare
"product" keyword) instead of return_request, which triggered chat.php's
navigationIntents redirect mid policy-conversation.
"""

from app.services.chat.intent_classifier import IntentClassifier


def test_return_request_wins_over_product_search_generic_noun():
    classifier = IntentClassifier()
    assert classifier.classify("How many days do I have to return a product?") == "return_request"


def test_recommendation_wins_over_product_search_generic_noun():
    classifier = IntentClassifier()
    assert classifier.classify("Can you recommend an article for running?") == "recommendation"


def test_order_status_wins_over_product_search_generic_noun():
    classifier = IntentClassifier()
    assert classifier.classify("Can you track my order for this product?") == "order_status"


def test_product_search_still_matches_plain_search_phrasing():
    classifier = IntentClassifier()
    assert classifier.classify("I'm looking for running shoes") == "product_search"
    assert classifier.classify("Je cherche un produit pour la course") == "product_search"


def test_greeting_and_coupon_request_unaffected_by_reorder():
    classifier = IntentClassifier()
    assert classifier.classify("Hello!") == "greeting"
    assert classifier.classify("Do you have a discount code?") == "coupon_request"
