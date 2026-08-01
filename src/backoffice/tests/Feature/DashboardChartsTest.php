<?php

namespace Tests\Feature;

use App\Filament\Widgets\ConversationsChart;
use App\Filament\Widgets\GuardrailBlocksChart;
use App\Filament\Widgets\IntentDistributionChart;
use App\Filament\Widgets\LlmCostChart;
use Illuminate\Support\Facades\Http;
use Livewire\Livewire;
use Tests\TestCase;

class DashboardChartsTest extends TestCase
{
    public function test_conversations_chart_renders_real_timeseries_data(): void
    {
        Http::fake([
            '*/analytics/timeseries*' => Http::response([
                'metric' => 'conversations',
                'time_range' => 'last_30_days',
                'points' => [
                    ['label' => '2026-07-30', 'value' => 3],
                    ['label' => '2026-07-31', 'value' => 5],
                ],
            ]),
        ]);

        Livewire::test(ConversationsChart::class)
            ->assertOk()
            ->assertSet('filter', 'last_30_days');
    }

    public function test_llm_cost_chart_renders_real_timeseries_data(): void
    {
        Http::fake([
            '*/analytics/timeseries*' => Http::response([
                'metric' => 'llm_cost',
                'time_range' => 'last_30_days',
                'points' => [['label' => '2026-07-31', 'value' => 0.42]],
            ]),
        ]);

        Livewire::test(LlmCostChart::class)->assertOk();
    }

    public function test_guardrail_blocks_chart_renders_real_timeseries_data(): void
    {
        Http::fake([
            '*/analytics/timeseries*' => Http::response([
                'metric' => 'guardrail_blocks',
                'time_range' => 'last_30_days',
                'points' => [['label' => '2026-07-31', 'value' => 1]],
            ]),
        ]);

        Livewire::test(GuardrailBlocksChart::class)->assertOk();
    }

    public function test_intent_distribution_chart_folds_extra_slices_into_autres(): void
    {
        $points = collect(range(1, 9))
            ->map(fn ($i) => ['label' => "intent_{$i}", 'value' => $i])
            ->all();

        Http::fake([
            '*/analytics/timeseries*' => Http::response([
                'metric' => 'intent_distribution',
                'time_range' => 'last_30_days',
                'points' => $points,
            ]),
        ]);

        Livewire::test(IntentDistributionChart::class)->assertOk();
    }

    public function test_charts_degrade_gracefully_when_ai_core_is_unreachable(): void
    {
        Http::fake([
            '*/analytics/timeseries*' => Http::response('', 500),
        ]);

        Livewire::test(ConversationsChart::class)->assertOk();
    }
}
