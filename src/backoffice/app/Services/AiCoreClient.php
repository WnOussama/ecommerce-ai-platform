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

    public function adminActions(int $limit = 20): array
    {
        return $this->get('/admin/actions', ['limit' => $limit]);
    }

    public function adminAction(string $actionId): array
    {
        return $this->get("/admin/actions/{$actionId}");
    }

    /**
     * Send an admin command - either free text (classified server-side into
     * one of the predefined actions, see AdminCommandParser) or a structured
     * action_name + parameters (required for actions that need real
     * parameters, e.g. generate_bulk_coupons).
     *
     * PHP has no distinct empty-object/empty-array type, so an empty
     * `parameters` array serializes to JSON `[]` - the AI Core expects a
     * dict (`{}`) or the key omitted entirely. Omit falsy-but-meaningful
     * fields explicitly with `!== null` rather than array_filter()'s default
     * callback, which would also drop `reason: ''` or other legitimate
     * empty-but-intentional values.
     */
    public function sendAdminCommand(?string $command = null, ?string $actionName = null, array $parameters = [], string $reason = ''): array
    {
        return $this->post('/admin/command', array_filter([
            'command' => $command,
            'action_name' => $actionName,
            'parameters' => $parameters ?: null,
            'reason' => $reason ?: null,
        ], fn ($value) => $value !== null));
    }

    public function confirmAdminAction(string $actionId, string $confirmationToken): array
    {
        return $this->post('/admin/confirm', [
            'action_id' => $actionId,
            'confirmation_token' => $confirmationToken,
        ]);
    }

    public function rejectAdminAction(string $actionId, string $reason = ''): array
    {
        return $this->post('/admin/reject', array_filter([
            'action_id' => $actionId,
            'reason' => $reason,
        ], fn ($value) => $value !== ''));
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
