<?php

namespace Tests\Feature;

use App\Filament\Pages\Insights;
use Illuminate\Support\Facades\Http;
use Livewire\Livewire;
use Tests\TestCase;

class InsightsPageTest extends TestCase
{
    private function fakeSummary(array $overrides = []): array
    {
        return array_merge([
            'time_range' => 'last_30_days',
            'since' => now()->toIso8601String(),
            'most_requested_products' => [],
            'unmet_demand' => [],
            'intent_distribution' => [],
            'peak_hours' => [],
            'coupon_conversion' => [],
            'low_stock_products' => [],
        ], $overrides);
    }

    public function test_renders_real_demand_data_with_human_readable_intent_labels(): void
    {
        Http::fake([
            '*/insights/summary*' => Http::response($this->fakeSummary([
                'most_requested_products' => [
                    ['external_id' => 'p1', 'name' => 'Chaussure de trail', 'request_count' => 5],
                ],
                'intent_distribution' => ['product_search' => 3],
            ])),
        ]);

        Livewire::test(Insights::class)
            ->assertOk()
            ->assertSee('Chaussure de trail')
            ->assertSee('Recherche produit'); // translated label, not the raw "product_search" code
    }

    public function test_low_stock_products_are_highlighted(): void
    {
        Http::fake([
            '*/insights/summary*' => Http::response($this->fakeSummary([
                'low_stock_products' => [
                    ['external_id' => 'p2', 'name' => 'Montre GPS', 'quantity' => 1],
                ],
            ])),
        ]);

        Livewire::test(Insights::class)
            ->assertOk()
            ->assertSee('Montre GPS')
            ->assertSee('1 restant');
    }

    public function test_changing_time_range_refetches_the_summary(): void
    {
        Http::fake([
            '*/insights/summary*' => Http::response($this->fakeSummary()),
        ]);

        Livewire::test(Insights::class)
            ->set('timeRange', 'last_7_days')
            ->assertOk();

        Http::assertSent(fn ($request) => str_contains($request->url(), '/insights/summary')
            && $request['time_range'] === 'last_7_days');
    }

    public function test_degrades_gracefully_when_ai_core_is_unreachable(): void
    {
        Http::fake([
            '*/insights/summary*' => Http::response('', 500),
        ]);

        Livewire::test(Insights::class)
            ->assertOk()
            ->assertSet('summary', null);
    }
}
