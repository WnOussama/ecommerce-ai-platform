<?php

namespace Tests\Unit;

use App\Services\AiCoreClient;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

class AiCoreClientTest extends TestCase
{
    /**
     * Regression: PHP has no distinct empty-object type, so an empty array
     * serializes to JSON `[]`. The AI Core's Pydantic model expects `context`
     * to be a dict (`{}`) or omitted - sending `[]` triggers a 422
     * "Input should be a valid dictionary" (discovered by actually running
     * this against the real AI Core, not by mocking).
     */
    public function test_send_admin_command_omits_empty_context(): void
    {
        Http::fake([
            '*/admin/command' => Http::response(['command_id' => 'cmd_1', 'status' => 'completed', 'response' => 'ok']),
        ]);

        app(AiCoreClient::class)->sendAdminCommand('test command');

        Http::assertSent(function ($request) {
            $body = $request->data();

            return $request->url() === 'http://localhost:8000/api/v1/admin/command'
                && $body['command'] === 'test command'
                && ! array_key_exists('context', $body);
        });
    }

    public function test_send_admin_command_includes_non_empty_context(): void
    {
        Http::fake([
            '*/admin/command' => Http::response(['command_id' => 'cmd_1', 'status' => 'completed', 'response' => 'ok']),
        ]);

        app(AiCoreClient::class)->sendAdminCommand('test command', ['key' => 'value']);

        Http::assertSent(fn ($request) => $request->data()['context'] === ['key' => 'value']);
    }

    /**
     * Regression: array_filter()'s default callback drops falsy values,
     * including `false`. confirmAdminAction(confirmed: false) - rejecting an
     * action - was being silently dropped from the request body, which would
     * default to `confirmed: true` server-side and invert the decision.
     */
    public function test_confirm_admin_action_sends_false_explicitly(): void
    {
        Http::fake([
            '*/admin/confirm' => Http::response(['action_id' => 'act_1', 'status' => 'cancelled']),
        ]);

        app(AiCoreClient::class)->confirmAdminAction('act_1', confirmed: false);

        Http::assertSent(function ($request) {
            $body = $request->data();

            return $body['action_id'] === 'act_1'
                && array_key_exists('confirmed', $body)
                && $body['confirmed'] === false;
        });
    }

    public function test_confirm_admin_action_sends_true(): void
    {
        Http::fake([
            '*/admin/confirm' => Http::response(['action_id' => 'act_1', 'status' => 'executed']),
        ]);

        app(AiCoreClient::class)->confirmAdminAction('act_1', confirmed: true);

        Http::assertSent(fn ($request) => $request->data()['confirmed'] === true);
    }
}
