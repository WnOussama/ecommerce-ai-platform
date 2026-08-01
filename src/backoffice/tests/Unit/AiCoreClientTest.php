<?php

namespace Tests\Unit;

use App\Services\AiCoreClient;
use Illuminate\Support\Facades\Http;
use Tests\TestCase;

class AiCoreClientTest extends TestCase
{
    /**
     * Regression: PHP has no distinct empty-object type, so an empty array
     * serializes to JSON `[]`. The AI Core's Pydantic model expects
     * `parameters` to be a dict (`{}`) or omitted - sending `[]` triggers a
     * 422 "Input should be a valid dictionary" (discovered by actually
     * running this against the real AI Core, not by mocking).
     */
    public function test_send_admin_command_omits_empty_parameters(): void
    {
        Http::fake([
            '*/admin/command' => Http::response(['action_id' => 'a1', 'action_name' => 'get_analytics', 'status' => 'completed', 'success' => true]),
        ]);

        app(AiCoreClient::class)->sendAdminCommand(command: 'test command');

        Http::assertSent(function ($request) {
            $body = $request->data();

            return $request->url() === 'http://localhost:8000/api/v1/admin/command'
                && $body['command'] === 'test command'
                && ! array_key_exists('parameters', $body)
                && ! array_key_exists('action_name', $body);
        });
    }

    public function test_send_admin_command_with_structured_action_and_parameters(): void
    {
        Http::fake([
            '*/admin/command' => Http::response(['action_id' => 'a1', 'action_name' => 'generate_bulk_coupons', 'status' => 'pending_confirmation', 'success' => true]),
        ]);

        app(AiCoreClient::class)->sendAdminCommand(
            actionName: 'generate_bulk_coupons',
            parameters: ['customer_ids' => ['c1'], 'discount_percent' => 10],
            reason: 'winback campaign'
        );

        Http::assertSent(function ($request) {
            $body = $request->data();

            return $body['action_name'] === 'generate_bulk_coupons'
                && $body['parameters'] === ['customer_ids' => ['c1'], 'discount_percent' => 10]
                && $body['reason'] === 'winback campaign'
                && ! array_key_exists('command', $body);
        });
    }

    public function test_confirm_admin_action_sends_the_confirmation_token(): void
    {
        Http::fake([
            '*/admin/confirm' => Http::response(['action_id' => 'act_1', 'status' => 'completed']),
        ]);

        app(AiCoreClient::class)->confirmAdminAction('act_1', 'tok_abc123');

        Http::assertSent(function ($request) {
            $body = $request->data();

            return $body['action_id'] === 'act_1'
                && $body['confirmation_token'] === 'tok_abc123';
        });
    }

    public function test_reject_admin_action_sends_reason_when_present(): void
    {
        Http::fake([
            '*/admin/reject' => Http::response(['action_id' => 'act_1', 'status' => 'rejected']),
        ]);

        app(AiCoreClient::class)->rejectAdminAction('act_1', 'not needed');

        Http::assertSent(fn ($request) => $request->data() === ['action_id' => 'act_1', 'reason' => 'not needed']);
    }

    public function test_reject_admin_action_omits_empty_reason(): void
    {
        Http::fake([
            '*/admin/reject' => Http::response(['action_id' => 'act_1', 'status' => 'rejected']),
        ]);

        app(AiCoreClient::class)->rejectAdminAction('act_1');

        Http::assertSent(fn ($request) => $request->data() === ['action_id' => 'act_1']);
    }

    public function test_create_rule_posts_to_rules_endpoint(): void
    {
        Http::fake([
            '*/rules' => Http::response(['id' => 'rule_1', 'name' => 'Test rule']),
        ]);

        app(AiCoreClient::class)->createRule([
            'name' => 'Test rule',
            'conditions' => ['intent' => 'greeting'],
            'action' => ['type' => 'canned_response', 'text' => 'Hi'],
        ]);

        Http::assertSent(function ($request) {
            return $request->url() === 'http://localhost:8000/api/v1/rules'
                && $request->method() === 'POST'
                && $request->data()['name'] === 'Test rule';
        });
    }

    public function test_update_rule_puts_to_rule_endpoint(): void
    {
        Http::fake([
            '*/rules/rule_1' => Http::response(['id' => 'rule_1', 'name' => 'Updated']),
        ]);

        app(AiCoreClient::class)->updateRule('rule_1', ['name' => 'Updated']);

        Http::assertSent(fn ($request) => $request->method() === 'PUT'
            && $request->url() === 'http://localhost:8000/api/v1/rules/rule_1');
    }

    public function test_delete_rule_sends_delete_request(): void
    {
        Http::fake([
            '*/rules/rule_1' => Http::response('', 204),
        ]);

        app(AiCoreClient::class)->deleteRule('rule_1');

        Http::assertSent(fn ($request) => $request->method() === 'DELETE'
            && $request->url() === 'http://localhost:8000/api/v1/rules/rule_1');
    }

    public function test_timeseries_sends_metric_and_time_range(): void
    {
        Http::fake([
            '*/analytics/timeseries*' => Http::response(['metric' => 'conversations', 'points' => []]),
        ]);

        app(AiCoreClient::class)->timeseries('conversations', 'last_7_days');

        Http::assertSent(function ($request) {
            return str_contains($request->url(), '/analytics/timeseries')
                && $request['metric'] === 'conversations'
                && $request['time_range'] === 'last_7_days';
        });
    }

    public function test_cost_report_sends_time_range_and_limit(): void
    {
        Http::fake([
            '*/analytics/cost-report*' => Http::response(['total_messages' => 0]),
        ]);

        app(AiCoreClient::class)->costReport('last_7_days', 5);

        Http::assertSent(function ($request) {
            return str_contains($request->url(), '/analytics/cost-report')
                && $request['time_range'] === 'last_7_days'
                && $request['limit'] === 5;
        });
    }

    public function test_list_conversations_omits_status_when_not_given(): void
    {
        Http::fake([
            '*/chat/conversations*' => Http::response(['conversations' => [], 'total' => 0]),
        ]);

        app(AiCoreClient::class)->listConversations(20, 0);

        Http::assertSent(function ($request) {
            $query = $request->data();

            return str_contains($request->url(), '/chat/conversations')
                && $query['limit'] === 20
                && $query['offset'] === 0
                && ! array_key_exists('status', $query);
        });
    }

    public function test_list_conversations_includes_status_when_given(): void
    {
        Http::fake([
            '*/chat/conversations*' => Http::response(['conversations' => [], 'total' => 0]),
        ]);

        app(AiCoreClient::class)->listConversations(20, 0, 'resolved');

        Http::assertSent(fn ($request) => $request['status'] === 'resolved');
    }

    public function test_insights_summary_sends_time_range(): void
    {
        Http::fake([
            '*/insights/summary*' => Http::response(['most_requested_products' => []]),
        ]);

        app(AiCoreClient::class)->insightsSummary('last_7_days');

        Http::assertSent(fn ($request) => str_contains($request->url(), '/insights/summary')
            && $request['time_range'] === 'last_7_days');
    }
}
