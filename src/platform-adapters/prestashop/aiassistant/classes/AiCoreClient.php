<?php
/**
 * Client HTTP minimal pour l'AI Core (FastAPI).
 *
 * Utilisé uniquement côté serveur (module PHP), jamais exposé au
 * navigateur du visiteur - voir controllers/front/chat.php.
 */

if (!defined('_PS_VERSION_')) {
    exit;
}

class AiCoreClient
{
    /** @var string */
    private $baseUrl;

    /** @var string */
    private $tenantId;

    /** @var int */
    private $timeoutSeconds;

    public function __construct($baseUrl, $tenantId, $timeoutSeconds = 10)
    {
        $this->baseUrl = rtrim((string) $baseUrl, '/');
        $this->tenantId = (string) $tenantId;
        $this->timeoutSeconds = (int) $timeoutSeconds;
    }

    /**
     * Envoie un message au chatbot et retourne la réponse décodée.
     *
     * @return array{ok: bool, data?: array, error?: string}
     */
    public function sendChatMessage($message, $conversationId = null)
    {
        $payload = ['message' => (string) $message, 'use_rag' => true];

        if ($conversationId !== null && $conversationId !== '') {
            $payload['conversation_id'] = (string) $conversationId;
        }

        return $this->request('POST', '/chat/message', $payload);
    }

    /**
     * Récupère l'historique persisté d'une conversation - voir
     * controllers/front/chat.php::proxyConversationHistory(), utilisé pour
     * réafficher le fil de discussion après une navigation (le widget
     * perd son état DOM à chaque rechargement de page, contrairement à la
     * conversation elle-même, déjà persistée côté AI Core).
     *
     * @return array{ok: bool, data?: array, error?: string}
     */
    public function getConversationHistory($conversationId)
    {
        return $this->request('GET', '/chat/history/'.rawurlencode((string) $conversationId));
    }

    /**
     * Vérifie que la boutique peut effectivement joindre et s'authentifier
     * auprès de l'AI Core (utilisé lors de la sauvegarde de la config).
     *
     * @return array{ok: bool, error?: string}
     */
    public function checkConnection()
    {
        $result = $this->request('GET', '/tenants/current');

        if (!$result['ok']) {
            return $result;
        }

        return ['ok' => true];
    }

    /**
     * @return array{ok: bool, data?: array, error?: string, status?: int}
     */
    private function request($method, $path, ?array $payload = null)
    {
        if ($this->baseUrl === '' || $this->tenantId === '') {
            return ['ok' => false, 'error' => 'AI Core non configuré'];
        }

        $url = $this->baseUrl.$path;

        $headers = [
            'Content-Type: application/json',
            'Accept: application/json',
            'X-Tenant-ID: '.$this->tenantId,
        ];

        $ch = curl_init($url);
        curl_setopt($ch, CURLOPT_CUSTOMREQUEST, $method);
        curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_TIMEOUT, $this->timeoutSeconds);

        if ($payload !== null) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($payload));
        }

        $body = curl_exec($ch);
        $errno = curl_errno($ch);
        $error = curl_error($ch);
        $status = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);

        if ($errno !== 0) {
            return ['ok' => false, 'error' => $error];
        }

        $decoded = json_decode($body, true);

        if ($status >= 400) {
            $message = is_array($decoded) && isset($decoded['message'])
                ? $decoded['message']
                : ('HTTP '.$status);

            return ['ok' => false, 'error' => $message, 'status' => $status];
        }

        return ['ok' => true, 'data' => is_array($decoded) ? $decoded : [], 'status' => $status];
    }
}
