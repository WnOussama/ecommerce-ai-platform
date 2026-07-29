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

        echo json_encode($result['data']);
        exit;
    }
}
