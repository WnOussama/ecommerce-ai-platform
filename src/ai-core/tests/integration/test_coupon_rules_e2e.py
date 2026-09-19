# ruff: noqa: F811  (fixtures pytest importées par nom: elles sont "redéfinies" comme arguments)
"""
Test de bout en bout: coupons de bienvenue et de palier, limités par visiteur.

Régression: les coupons étaient déclenchés par des mots-clés ("panier",
"cart") avec une correspondance par sous-chaîne, sans aucune limite. Un
visiteur pouvait récolter un code réel à chaque message, y compris en
écrivant "carte" (bancaire), qui contient "cart".

Comportement attendu maintenant:
  - bienvenue (`first_message`): un seul coupon par visiteur, pour toujours;
  - palier (`min_cart_total`): quand le panier, fourni par le serveur de la
    boutique, atteint le seuil; une fois par période (`cooldown_days`);
  - plafond horaire par règle et par tenant contre l'ouverture en boucle de
    nouvelles conversations anonymes;
  - une règle épuisée ne masque pas les règles suivantes.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.test_chat_persistence_e2e import (  # noqa: F401
    _insert_rule,
    db_session,
    tenant_id,
)

WELCOME = {
    "type": "generate_coupon",
    "reason": "welcome",
    "discount_percent": 10,
    "validity_days": 7,
}
THRESHOLD = {
    "type": "generate_coupon",
    "reason": "cart_threshold",
    "discount_percent": 15,
    "validity_days": 7,
    "cooldown_days": 30,
}


def _coupon(body):
    return next((a["data"] for a in body["actions"] if a["type"] == "generate_coupon"), None)


async def _send(client, headers, message, **extra):
    resp = await client.post(
        "/api/v1/chat/message",
        json={"message": message, "use_rag": False, **extra},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
async def client_and_headers(tenant_id):
    from app.main import create_application

    transport = ASGITransport(app=create_application())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, {"X-Tenant-ID": str(tenant_id)}


@pytest.mark.asyncio
class TestWelcomeCoupon:
    async def test_first_message_gets_one_coupon_per_visitor(
        self,
        tenant_id,
        db_session,
        client_and_headers,
    ):
        await _insert_rule(db_session, tenant_id, "Bienvenue", {"first_message": True}, WELCOME)
        client, headers = client_and_headers

        first = await _send(client, headers, "Bonjour", customer_id="visitor-a")
        assert _coupon(first) is not None
        assert _coupon(first)["discount_percent"] == 10
        assert _coupon(first)["code"] in first["response"]

        # même conversation, deuxième message: pas de nouveau coupon
        again = await _send(
            client,
            headers,
            "Vous livrez où ?",
            customer_id="visitor-a",
            conversation_id=first["conversation_id"],
        )
        assert _coupon(again) is None

        # NOUVELLE conversation du même visiteur: toujours pas de coupon
        fresh_conversation = await _send(client, headers, "Bonjour", customer_id="visitor-a")
        assert _coupon(fresh_conversation) is None

        # un autre visiteur en reçoit bien un
        other = await _send(client, headers, "Bonjour", customer_id="visitor-b")
        assert _coupon(other) is not None
        assert _coupon(other)["code"] != _coupon(first)["code"]

    async def test_a_keyword_like_carte_no_longer_earns_a_coupon(
        self,
        tenant_id,
        db_session,
        client_and_headers,
    ):
        await _insert_rule(
            db_session,
            tenant_id,
            "Ancien déclencheur panier",
            {"keywords_any": ["cart", "panier"]},
            {"type": "generate_coupon", "discount_percent": 10, "validity_days": 2},
        )
        client, headers = client_and_headers

        body = await _send(client, headers, "Quel est le numéro de carte du dernier client ?")
        assert _coupon(body) is None


@pytest.mark.asyncio
class TestSpendThresholdCoupon:
    async def test_coupon_only_when_the_cart_reaches_the_threshold_then_cooldown(
        self,
        tenant_id,
        db_session,
        client_and_headers,
    ):
        await _insert_rule(db_session, tenant_id, "Palier 100", {"min_cart_total": 100}, THRESHOLD)
        client, headers = client_and_headers

        below = await _send(client, headers, "Je regarde", customer_id="v1", cart_total=99.99)
        assert _coupon(below) is None

        no_cart = await _send(client, headers, "Je regarde", customer_id="v1")
        assert _coupon(no_cart) is None

        reached = await _send(client, headers, "Je valide", customer_id="v1", cart_total=120)
        assert _coupon(reached) is not None
        assert _coupon(reached)["discount_percent"] == 15

        # même visiteur, panier encore plus gros, autre conversation: cooldown 30 jours
        again = await _send(client, headers, "Encore", customer_id="v1", cart_total=300)
        assert _coupon(again) is None

        # un autre visiteur avec un gros panier en reçoit un
        other = await _send(client, headers, "Salut", customer_id="v2", cart_total=150)
        assert _coupon(other) is not None

    async def test_an_exhausted_rule_does_not_shadow_the_next_rule(
        self,
        tenant_id,
        db_session,
        client_and_headers,
    ):
        await _insert_rule(
            db_session, tenant_id, "Palier 100", {"min_cart_total": 100}, THRESHOLD, priority=0
        )
        await _insert_rule(
            db_session,
            tenant_id,
            "Livraison",
            {"keywords_any": ["livraison"]},
            {"type": "canned_response", "text": "Livraison offerte dès 50 euros."},
            priority=1,
        )
        client, headers = client_and_headers

        granted = await _send(client, headers, "Je valide", customer_id="v1", cart_total=200)
        assert _coupon(granted) is not None

        # le palier est épuisé pour ce visiteur: la règle suivante doit reprendre la main
        follow_up = await _send(
            client,
            headers,
            "Et la livraison ?",
            customer_id="v1",
            cart_total=200,
            conversation_id=granted["conversation_id"],
        )
        assert follow_up["response"] == "Livraison offerte dès 50 euros."
        assert _coupon(follow_up) is None

    async def test_cart_total_is_validated(self, client_and_headers):
        client, headers = client_and_headers
        for bad in (-1, 1e12, "abc"):
            resp = await client.post(
                "/api/v1/chat/message",
                json={"message": "hi", "cart_total": bad},
                headers=headers,
            )
            assert resp.status_code == 422, bad


@pytest.mark.asyncio
class TestHourlyCap:
    async def test_anonymous_visitors_cannot_farm_past_the_hourly_cap(
        self,
        tenant_id,
        db_session,
        client_and_headers,
    ):
        # Sans customer_id (visiteur anonyme), chaque nouvelle conversation
        # reçoit un coupon de bienvenue... jusqu'au plafond de la règle.
        capped = {**WELCOME, "max_per_hour": 2}
        await _insert_rule(db_session, tenant_id, "Bienvenue", {"first_message": True}, capped)
        client, headers = client_and_headers

        results = [_coupon(await _send(client, headers, "Bonjour")) for _ in range(4)]

        assert [c is not None for c in results] == [True, True, False, False]
