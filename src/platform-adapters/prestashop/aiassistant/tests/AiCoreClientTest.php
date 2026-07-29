<?php

namespace Tests;

use AiCoreClient;
use PHPUnit\Framework\TestCase;

/**
 * Tests d'intégration réels contre l'AI Core en cours d'exécution (pas de
 * mock HTTP) - s'ignorent proprement si l'AI Core n'est pas joignable,
 * comme les tests Python et Laravel équivalents de ce dépôt.
 */
class AiCoreClientTest extends TestCase
{
    private const BASE_URL = 'http://localhost:8000/api/v1';
    private const HEALTH_URL = 'http://localhost:8000/health';
    private const TENANT_ID = '3478e866-016e-4f72-a77f-e564bde1ca24';

    protected function setUp(): void
    {
        $ch = curl_init(self::HEALTH_URL);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_TIMEOUT, 2);
        $result = curl_exec($ch);
        $errno = curl_errno($ch);
        curl_close($ch);

        if ($errno !== 0 || $result === false) {
            $this->markTestSkipped('AI Core indisponible sur '.self::BASE_URL);
        }
    }

    public function test_check_connection_fails_when_not_configured(): void
    {
        $client = new AiCoreClient('', '');
        $result = $client->checkConnection();

        $this->assertFalse($result['ok']);
        $this->assertSame('AI Core non configuré', $result['error']);
    }

    public function test_check_connection_succeeds_with_valid_tenant(): void
    {
        $client = new AiCoreClient(self::BASE_URL, self::TENANT_ID);
        $result = $client->checkConnection();

        $this->assertTrue($result['ok'], isset($result['error']) ? $result['error'] : '');
    }

    public function test_check_connection_fails_with_unknown_endpoint(): void
    {
        // Régression: une AI Core injoignable (mauvaise URL) doit renvoyer
        // ok=false avec un message exploitable, pas une exception non
        // gérée qui ferait planter la page front-office de la boutique.
        $client = new AiCoreClient('http://localhost:1', self::TENANT_ID, 2);
        $result = $client->checkConnection();

        $this->assertFalse($result['ok']);
        $this->assertNotEmpty($result['error']);
    }

    public function test_send_chat_message_returns_real_response(): void
    {
        $client = new AiCoreClient(self::BASE_URL, self::TENANT_ID);
        $result = $client->sendChatMessage('Bonjour');

        $this->assertTrue($result['ok'], isset($result['error']) ? $result['error'] : '');
        $this->assertArrayHasKey('conversation_id', $result['data']);
        $this->assertArrayHasKey('response', $result['data']);
    }

    public function test_send_chat_message_continues_conversation(): void
    {
        $client = new AiCoreClient(self::BASE_URL, self::TENANT_ID);
        $first = $client->sendChatMessage('Bonjour');
        $this->assertTrue($first['ok']);

        $conversationId = $first['data']['conversation_id'];
        $second = $client->sendChatMessage('Une autre question', $conversationId);

        $this->assertTrue($second['ok']);
        $this->assertSame($conversationId, $second['data']['conversation_id']);
    }
}
