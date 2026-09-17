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

    public function initContent()
    {
        parent::initContent();

        header('Content-Type: application/json');

        if (!Tools::getValue('ajax') && $_SERVER['REQUEST_METHOD'] !== 'POST') {
            http_response_code(405);
            echo json_encode(['error' => 'method_not_allowed']);
            exit;
        }

        if (!Configuration::get('AIASSISTANT_ENABLED')) {
            http_response_code(503);
            echo json_encode(['error' => 'assistant_disabled']);
            exit;
        }

        $input = json_decode(Tools::file_get_contents('php://input'), true);
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

        $result = $client->sendChatMessage($message, $conversationId);

        if (!$result['ok']) {
            http_response_code(502);
            echo json_encode(['error' => $result['error']]);
            exit;
        }

        echo json_encode($this->withNavigation($result['data']));
        exit;
    }

    /**
     * The AI Core response carries real PrestaShop product ids (see
     * app/services/rag/retrieval_service.py - `product_id` is the synced
     * catalog's external_id) but has no notion of this shop's URL
     * structure (friendly URLs, language, multistore...) - that's this
     * shop's own routing, resolved here with Link::getProductLink()
     * rather than asked of the AI Core. Adds a `url` on every product in
     * the response, plus a top-level `navigate_to` pointing at the best
     * RAG match (widget.js auto-navigates there after showing the reply)
     * - `products` is already sorted by similarity descending (see
     * retrieval_service.py::search, `results.sort(...reverse=True)`), so
     * the first entry is the best match.
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

        if (isset($data['products'][0]['url'])) {
            $data['navigate_to'] = $data['products'][0]['url'];
        }

        return $data;
    }
}
