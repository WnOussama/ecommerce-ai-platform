<?php

namespace Tests\Feature;

use App\Filament\Pages\CostTracking;
use Illuminate\Support\Facades\Http;
use Livewire\Livewire;
use Tests\TestCase;

class CostTrackingPageTest extends TestCase
{
    public function test_renders_real_cost_totals(): void
    {
        Http::fake([
            '*/analytics/cost-report*' => Http::response([
                'time_range' => 'last_30_days',
                'total_messages' => 3,
                'total_tokens_input' => 3500,
                'total_tokens_output' => 1750,
                'total_cost_usd' => 0.0022,
                'avg_latency_ms' => 233.3,
                'conversation_count' => 2,
                'cost_per_conversation_usd' => 0.0011,
                'recent_expensive_messages' => [
                    [
                        'message_id' => 'm1',
                        'conversation_id' => 'c1',
                        'content_preview' => 'reply 2',
                        'tokens_input' => 2000,
                        'tokens_output' => 1000,
                        'cost_usd' => 0.0014,
                        'latency_ms' => 400,
                        'created_at' => now()->toIso8601String(),
                    ],
                ],
            ]),
        ]);

        Livewire::test(CostTracking::class)
            ->assertOk()
            ->assertSee('reply 2')
            ->assertSee('0.0022');
    }

    public function test_changing_time_range_refetches_the_report(): void
    {
        Http::fake([
            '*/analytics/cost-report*' => Http::response([
                'total_messages' => 0,
                'total_tokens_input' => 0,
                'total_tokens_output' => 0,
                'total_cost_usd' => 0.0,
                'avg_latency_ms' => 0.0,
                'conversation_count' => 0,
                'cost_per_conversation_usd' => 0.0,
                'recent_expensive_messages' => [],
            ]),
        ]);

        Livewire::test(CostTracking::class)
            ->set('timeRange', 'last_7_days')
            ->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), '/analytics/cost-report')
            && $request['time_range'] === 'last_7_days');
    }

    public function test_degrades_gracefully_when_ai_core_is_unreachable(): void
    {
        Http::fake([
            '*/analytics/cost-report*' => Http::response('', 500),
        ]);

        Livewire::test(CostTracking::class)
            ->assertOk()
            ->assertSet('report', null);
    }
}
