<?php

namespace App\Services;

use App\Exceptions\AiCoreException;
use Illuminate\Http\Client\ConnectionException;
use Illuminate\Http\Client\PendingRequest;
use Illuminate\Support\Facades\Http;

/**
 * REST client for the AI Core FastAPI backend.
 *
 * The backoffice never queries the AI Core's database directly - every
 * piece of data shown in this admin panel comes through here.
 */
class AiCoreClient
{
    protected function request(): PendingRequest
    {
        $request = Http::baseUrl(config('aicore.base_url'))
            ->timeout(config('aicore.timeout'))
            ->acceptJson();

        if ($apiKey = config('aicore.api_key')) {
            $request = $request->withToken($apiKey);
        }

        if ($tenantId = config('aicore.tenant_id')) {
            $request = $request->withHeaders(['X-Tenant-ID' => $tenantId]);
        }

        return $request;
    }

    protected function get(string $path, array $query = []): array
    {
        try {
            $response = $this->request()->get($path, $query);
        } catch (ConnectionException $e) {
            throw AiCoreException::connectionFailed($e->getMessage());
        }

        if ($response->failed()) {
            throw AiCoreException::fromStatus($response->status(), $response->body());
        }

        return $response->json() ?? [];
    }

    protected function post(string $path, array $payload = []): array
    {
        try {
            $response = $this->request()->post($path, $payload);
        } catch (ConnectionException $e) {
            throw AiCoreException::connectionFailed($e->getMessage());
        }

        if ($response->failed()) {
            throw AiCoreException::fromStatus($response->status(), $response->body());
        }

        return $response->json() ?? [];
    }

    protected function put(string $path, array $payload = []): array
    {
        try {
            $response = $this->request()->put($path, $payload);
        } catch (ConnectionException $e) {
            throw AiCoreException::connectionFailed($e->getMessage());
        }

        if ($response->failed()) {
            throw AiCoreException::fromStatus($response->status(), $response->body());
        }

        return $response->json() ?? [];
    }

    /**
     * True if the AI Core API is reachable (used for a connection-status
     * indicator - this hits the public /health endpoint, no auth needed).
     */
    public function isReachable(): bool
    {
        try {
            $response = Http::baseUrl(config('aicore.root_url'))
                ->timeout(5)
                ->get('/health');

            return $response->successful();
        } catch (ConnectionException) {
            return false;
        }
    }

    // -------------------------------------------------------------------
    // Analytics
    // -------------------------------------------------------------------

    public function dashboardMetrics(string $timeRange = 'last_30_days'): array
    {
        return $this->get('/analytics/dashboard', ['time_range' => $timeRange]);
    }

    public function aiPerformance(string $timeRange = 'last_30_days'): array
    {
        return $this->get('/analytics/ai-performance', ['time_range' => $timeRange]);
    }

    public function customerAnalytics(string $timeRange = 'last_30_days'): array
    {
        return $this->get('/analytics/customers', ['time_range' => $timeRange]);
    }

    public function couponAnalytics(string $timeRange = 'last_30_days'): array
    {
        return $this->get('/analytics/coupons', ['time_range' => $timeRange]);
    }

    // -------------------------------------------------------------------
    // Tenant
    // -------------------------------------------------------------------

    public function currentTenant(): array
    {
        return $this->get('/tenants/current');
    }

    public function updateCurrentTenant(array $data): array
    {
        return $this->put('/tenants/current', $data);
    }

    public function tenantUsage(): array
    {
        return $this->get('/tenants/current/usage');
    }

    public function tenantSettings(): array
    {
        return $this->get('/tenants/current/settings');
    }

    public function updateTenantSettings(array $settings): array
    {
        return $this->put('/tenants/current/settings', ['settings' => $settings]);
    }

    public function rotateApiKey(): array
    {
        return $this->post('/tenants/current/api-keys/rotate');
    }

    // -------------------------------------------------------------------
    // Admin AI agent
    // -------------------------------------------------------------------

    public function adminActions(?string $status = null, int $limit = 20): array
    {
        return $this->get('/admin/actions', array_filter([
            'status' => $status,
            'limit' => $limit,
        ]));
    }

    public function adminAction(string $actionId): array
    {
        return $this->get("/admin/actions/{$actionId}");
    }

    public function sendAdminCommand(string $command, array $context = []): array
    {
        // PHP has no distinct empty-object type, so an empty array serializes
        // to JSON `[]` - the AI Core expects `context` to be a dict (`{}`) or
        // omitted entirely. Omit it when empty rather than sending `[]`.
        return $this->post('/admin/command', array_filter([
            'command' => $command,
            'context' => $context ?: null,
        ], fn ($value) => $value !== null));
    }

    public function confirmAdminAction(string $actionId, bool $confirmed = true, array $modifications = []): array
    {
        // NOTE: array_filter()'s default callback drops falsy values,
        // including `false` - it must NOT be used on `confirmed` here, or a
        // rejection (confirmed: false) would be silently dropped from the
        // payload and default to true server-side, inverting the decision.
        return $this->post('/admin/confirm', array_filter([
            'action_id' => $actionId,
            'confirmed' => $confirmed,
            'modifications' => $modifications ?: null,
        ], fn ($value) => $value !== null));
    }

    // -------------------------------------------------------------------
    // Chat / conversations
    // -------------------------------------------------------------------

    public function conversationHistory(string $conversationId, int $limit = 50): array
    {
        return $this->get("/chat/history/{$conversationId}", ['limit' => $limit]);
    }

    public function closeConversation(string $conversationId): void
    {
        try {
            $this->request()->delete("/chat/conversation/{$conversationId}");
        } catch (ConnectionException $e) {
            throw AiCoreException::connectionFailed($e->getMessage());
        }
    }
}
