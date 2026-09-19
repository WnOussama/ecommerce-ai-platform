<?php
/**
 * Front controller "chat" - reçoit les messages du widget et les relaie
 * à l'AI Core en utilisant l'identifiant tenant configuré côté serveur.
 *
 * Le visiteur (navigateur) n'a jamais accès à l'URL de l'AI Core ni à
 * l'identifiant tenant - seul ce endpoint local (même origine que la
 * boutique) est appelé depuis le JS du widget.
 */

if (!defined('_PS_VERSION_')) {
    exit;
}

class AiAssistantChatModuleFrontController extends ModuleFrontController
{
    public $ssl = true;

    /** Nom des CartRule créées par ce module: seules celles-ci sont applicables via le widget. */
    const AI_CART_RULE_NAME = 'AI Assistant discount';

    public function initContent()
    {
        parent::initContent();

        header('Content-Type: application/json');

        if (!Configuration::get('AIASSISTANT_ENABLED')) {
            http_response_code(503);
            echo json_encode(['error' => 'assistant_disabled']);
            exit;
        }

        $this->rejectCrossSiteRequest();

        // Reprise d'une conversation après un changement de page - le
        // widget perdait tout son historique affiché à chaque navigation
        // (son état n'existe que dans le DOM, détruit au rechargement de
        // page), alors même que conversation_id lui survit dans
        // sessionStorage et que la conversation entière est déjà persistée
        // côté AI Core. Proxy en lecture seule vers GET /chat/history/{id}
        // (voir widget.js::loadHistory()) plutôt qu'une copie côté
        // PrestaShop de ce que l'AI Core persiste déjà.
        if ($_SERVER['REQUEST_METHOD'] === 'GET' && Tools::getValue('conversation_id')) {
            $this->proxyConversationHistory((string) Tools::getValue('conversation_id'));
            exit;
        }

        if (!Tools::getValue('ajax') && $_SERVER['REQUEST_METHOD'] !== 'POST') {
            http_response_code(405);
            echo json_encode(['error' => 'method_not_allowed']);
            exit;
        }

        $input = json_decode(Tools::file_get_contents('php://input'), true);

        // Redemption d'un code déjà généré par un tour de chat précédent -
        // action distincte du flux "envoyer un message", distinguée par la
        // présence de ce seul champ dans le corps JSON (voir
        // applyCouponToCart() et widget.js::applyCoupon()). Ne repasse pas
        // par l'AI Core : le code existe déjà (createCartRuleFromCoupon()
        // l'a matérialisé en vraie CartRule PrestaShop au moment de sa
        // génération), il ne reste qu'à l'appliquer au panier courant.
        if (is_array($input) && isset($input['apply_coupon_code'])) {
            $this->applyCouponToCart((string) $input['apply_coupon_code']);
            exit;
        }

        $message = is_array($input) && isset($input['message']) ? (string) $input['message'] : '';
        $conversationId = is_array($input) && isset($input['conversation_id']) ? (string) $input['conversation_id'] : null;

        if (trim($message) === '') {
            http_response_code(422);
            echo json_encode(['error' => 'empty_message']);
            exit;
        }

        $client = new AiCoreClient(
            Configuration::get('AIASSISTANT_API_URL'),
            Configuration::get('AIASSISTANT_TENANT_ID')
        );

        // Jamais lus depuis $input: le navigateur ne peut pas les choisir.
        $result = $client->sendChatMessage(
            $message,
            $conversationId,
            $this->getVisitorId(),
            $this->getCartTotal()
        );

        if (!$result['ok']) {
            http_response_code(502);
            echo json_encode(['error' => $result['error']]);
            exit;
        }

        echo json_encode($this->withRedeemableCoupons($this->withNavigation($result['data'])));
        exit;
    }

    /**
     * Protection CSRF: un site tiers pouvait faire poster le navigateur d'un
     * visiteur vers ce endpoint (le corps est du JSON lu tel quel, sans
     * preflight CORS avec un Content-Type text/plain) pour consommer le
     * quota du LLM ou appliquer des coupons. Un navigateur envoie toujours
     * l'en-tête Origin sur ce genre de requête: s'il est présent, il doit
     * être celui de la boutique.
     */
    private function rejectCrossSiteRequest()
    {
        $origin = isset($_SERVER['HTTP_ORIGIN']) ? (string) $_SERVER['HTTP_ORIGIN'] : '';

        if ($origin === '') {
            return;
        }

        $shopOrigin = Tools::getShopProtocol().Tools::getHttpHost(false);

        if ($this->normalizeOrigin($origin) !== $this->normalizeOrigin($shopOrigin)) {
            http_response_code(403);
            echo json_encode(['error' => 'cross_site_request_refused']);
            exit;
        }
    }

    /**
     * "https://Shop.example:443" -> "https://shop.example:443" ; le port par
     * défaut est rendu explicite pour que http://x et http://x:80 soient égaux.
     */
    private function normalizeOrigin($url)
    {
        $parts = parse_url((string) $url);

        if (empty($parts['scheme']) || empty($parts['host'])) {
            return '';
        }

        $scheme = strtolower($parts['scheme']);
        $port = isset($parts['port']) ? (int) $parts['port'] : ($scheme === 'https' ? 443 : 80);

        return $scheme.'://'.strtolower($parts['host']).':'.$port;
    }

    /**
     * Identité stable du visiteur, décidée côté serveur: client connecté,
     * sinon invité PrestaShop, sinon un identifiant aléatoire gardé dans le
     * cookie signé de PrestaShop. Sert à limiter les coupons par personne.
     */
    private function getVisitorId()
    {
        $customer = $this->context->customer;

        if ($customer && Validate::isLoadedObject($customer) && $customer->isLogged()) {
            return 'c'.(int) $customer->id;
        }

        $cookie = $this->context->cookie;

        if (!empty($cookie->id_guest)) {
            return 'g'.(int) $cookie->id_guest;
        }

        if (empty($cookie->ai_assistant_visitor)) {
            $cookie->ai_assistant_visitor = bin2hex(random_bytes(16));
            $cookie->write();
        }

        return 'v'.preg_replace('/[^a-f0-9]/', '', (string) $cookie->ai_assistant_visitor);
    }

    /**
     * Total des produits du panier (TTC, avant réductions), calculé par
     * PrestaShop. null si le visiteur n'a pas de panier.
     */
    private function getCartTotal()
    {
        $cart = $this->context->cart;

        if (!$cart || !$cart->id) {
            return null;
        }

        return (float) $cart->getOrderTotal(true, Cart::ONLY_PRODUCTS);
    }

    private function proxyConversationHistory($conversationId)
    {
        $client = new AiCoreClient(
            Configuration::get('AIASSISTANT_API_URL'),
            Configuration::get('AIASSISTANT_TENANT_ID')
        );

        $result = $client->getConversationHistory($conversationId);

        if (!$result['ok']) {
            http_response_code(502);
            echo json_encode(['error' => $result['error']]);
            return;
        }

        echo json_encode($result['data']);
    }

    /**
     * Materializes each `generate_coupon` action's code into a real,
     * active PrestaShop CartRule - without this, ai-core's coupon (real in
     * its own DB, see app/api/v1/endpoints/chat.py::_generate_coupon_from_rule)
     * had no PrestaShop-side counterpart at all: zero rows in
     * ps_cart_rule, no way for a shopper to actually redeem the code the
     * bot just gave them (confirmed live: empty ps_cart_rule table, no
     * voucher input anywhere in the storefront because
     * PS_CART_RULE_FEATURE_ACTIVE was still 0). CartRule::add() flips that
     * flag automatically, which also makes the theme's own "add a promo
     * code" field start appearing. Marks the action `redeemable` so
     * widget.js can offer a one-click "Apply to cart" button instead of
     * requiring the shopper to type the code themselves.
     */
    private function withRedeemableCoupons(array $data)
    {
        if (empty($data['actions']) || !is_array($data['actions'])) {
            return $data;
        }

        foreach ($data['actions'] as &$action) {
            if (($action['type'] ?? null) !== 'generate_coupon' || empty($action['data']['code'])) {
                continue;
            }

            $action['data']['redeemable'] = $this->createCartRuleFromCoupon($action['data']);
        }
        unset($action);

        return $data;
    }

    private function createCartRuleFromCoupon(array $couponData)
    {
        $code = (string) $couponData['code'];

        // Idempotent: ai-core already guarantees code uniqueness for this
        // coupon (see uow.coupons.generate_code()) - reuse the existing
        // CartRule on a retry/duplicate call instead of erroring on
        // PrestaShop's own unique `code` constraint.
        $existingId = (int) CartRule::getIdByCode($code);
        if ($existingId) {
            return true;
        }

        $cartRule = new CartRule();
        $cartRule->code = $code;
        $cartRule->name = [(int) Configuration::get('PS_LANG_DEFAULT') => self::AI_CART_RULE_NAME];
        $cartRule->quantity = 1;
        $cartRule->quantity_per_user = 1;
        $cartRule->date_from = date('Y-m-d H:i:s');
        $cartRule->date_to = !empty($couponData['expires_at'])
            ? date('Y-m-d H:i:s', strtotime((string) $couponData['expires_at']))
            : date('Y-m-d H:i:s', strtotime('+7 days'));
        $cartRule->reduction_percent = (float) ($couponData['discount_percent'] ?? 0);
        $cartRule->reduction_tax = true;
        $cartRule->highlight = true;
        $cartRule->active = true;
        // Non cumulable: sans cette restriction, un coupon de bienvenue et un
        // coupon de palier s'empilaient (15 % puis encore 15 % sur le reste,
        // constaté sur le vrai panier). checkValidity() refuse maintenant un
        // second code quand un autre est déjà dans le panier.
        $cartRule->cart_rule_restriction = true;

        return (bool) $cartRule->add();
    }

    /**
     * Applique au panier courant un coupon déjà matérialisé en CartRule
     * PrestaShop réelle (voir createCartRuleFromCoupon()). Réponse JSON
     * minimale - pas de schéma partagé avec l'AI Core ici, ce endpoint ne
     * lui appartient pas.
     */
    private function applyCouponToCart($code)
    {
        $idCartRule = (int) CartRule::getIdByCode($code);
        $cartRule = $idCartRule ? new CartRule($idCartRule) : null;

        // Seuls les codes émis par l'assistant sont applicables ici, et la
        // réponse est identique pour un code inconnu ou un code de la boutique
        // (codes VIP, partenaires...): le widget ne doit pas servir d'oracle
        // pour deviner quels codes existent.
        if (
            !$cartRule
            || !Validate::isLoadedObject($cartRule)
            || !in_array(self::AI_CART_RULE_NAME, (array) $cartRule->name, true)
        ) {
            http_response_code(404);
            echo json_encode(['error' => 'coupon_not_found']);
            return;
        }

        // Mêmes contrôles que le champ code promo standard de PrestaShop
        // (dates, quantité restante, restrictions client/groupe...) - avant,
        // seul `active` était vérifié.
        if (!$cartRule->active || $cartRule->checkValidity($this->context, false, false) !== true) {
            http_response_code(410);
            echo json_encode(['error' => 'coupon_inactive_or_expired']);
            return;
        }

        $cart = $this->context->cart;

        if (!$cart || !$cart->id) {
            http_response_code(409);
            echo json_encode(['error' => 'empty_cart']);
            return;
        }

        foreach ($cart->getCartRules() as $existing) {
            if ((int) $existing['id_cart_rule'] === $idCartRule) {
                echo json_encode(['ok' => true, 'already_applied' => true]);
                return;
            }
        }

        if (!$cart->addCartRule($idCartRule)) {
            http_response_code(500);
            echo json_encode(['error' => 'apply_failed']);
            return;
        }

        echo json_encode([
            'ok' => true,
            'total' => (float) $cart->getOrderTotal(true, Cart::BOTH),
        ]);
    }

    /**
     * The AI Core response carries real PrestaShop product ids (see
     * app/services/rag/retrieval_service.py - `product_id` is the synced
     * catalog's external_id) but has no notion of this shop's URL
     * structure (friendly URLs, language, multistore...) - that's this
     * shop's own routing, resolved here with Link::getProductLink()
     * rather than asked of the AI Core. Adds a `url` on every product in
     * the response, plus a top-level `navigate_to` pointing at the best
     * RAG match, but ONLY when the classified intent is actually about
     * finding a product.
     *
     * Real, confirmed live bug: RAG's min_similarity=0.3 threshold (see
     * app/services/rag/factory.py) is deliberately low - RAG always returns
     * its nearest products, even for a question that is not about a product -
     * so a pure policy question like "What's your return policy?"
     * (intent=return_request) still comes back with weak "matches", and the widget
     * was silently redirecting the shopper to an unrelated product page
     * mid-conversation, before they'd even read the reply. `products` is
     * already sorted similarity-descending (retrieval_service.py::search,
     * `results.sort(...reverse=True)`), so index 0 is the best (still
     * possibly irrelevant) match.
     */
    private function withNavigation(array $data)
    {
        if (empty($data['products']) || !is_array($data['products'])) {
            return $data;
        }

        $link = $this->context->link;

        foreach ($data['products'] as &$product) {
            if (isset($product['id'])) {
                $product['url'] = $link->getProductLink((int) $product['id']);
            }
        }
        unset($product);

        $navigationIntents = ['product_search', 'recommendation'];
        if (in_array($data['intent'] ?? null, $navigationIntents, true) && isset($data['products'][0]['url'])) {
            $data['navigate_to'] = $data['products'][0]['url'];
        }

        return $data;
    }
}
