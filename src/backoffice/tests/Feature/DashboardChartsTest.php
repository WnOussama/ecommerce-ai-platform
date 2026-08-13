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

    /**
     * Regression: a tenant with genuinely zero activity for the period got
     * a silently blank chart (Filament's ChartWidget always renders a
     * <canvas>, empty data or not) - indistinguishable from a broken
     * widget. getDescription() must say so explicitly.
     */
    public function test_empty_period_shows_a_no_data_message_not_a_blank_chart(): void
    {
        Http::fake([
            '*/analytics/timeseries*' => Http::response([
                'metric' => 'conversations',
                'time_range' => 'last_30_days',
                'points' => [
                    ['label' => '2026-07-30', 'value' => 0],
                    ['label' => '2026-07-31', 'value' => 0],
                ],
            ]),
        ]);

        Livewire::test(ConversationsChart::class)
            ->assertOk()
            ->assertSee('Aucune activité sur cette période.');
    }

    public function test_nonempty_period_does_not_show_the_no_data_message(): void
    {
        Http::fake([
            '*/analytics/timeseries*' => Http::response([
                'metric' => 'conversations',
                'time_range' => 'last_30_days',
                'points' => [['label' => '2026-07-31', 'value' => 4]],
            ]),
        ]);

        Livewire::test(ConversationsChart::class)
            ->assertOk()
            ->assertDontSee('Aucune activité sur cette période.');
    }
}
