<?php

namespace App\Filament\Widgets;

use App\Exceptions\AiCoreException;
use App\Services\AiCoreClient;
use App\Support\Intents;
use Filament\Widgets\ChartWidget;

class IntentDistributionChart extends ChartWidget
{
    protected static ?string $heading = 'Répartition des intentions';

    protected static ?int $sort = 6;

    public ?string $filter = 'last_30_days';

    /**
     * Fixed categorical order (validated for colorblind-safe adjacent
     * contrast) - never reassigned per-render, so a given slice keeps its
     * color across refreshes. Beyond 7 slices, the rest fold into "Autres"
     * rather than cycling back through the palette.
     */
    protected const PALETTE = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7'];

    protected function getFilters(): ?array
    {
        return [
            'last_7_days' => '7 derniers jours',
            'last_30_days' => '30 derniers jours',
            'this_month' => 'Ce mois-ci',
        ];
    }

    protected function getData(): array
    {
        try {
            $result = app(AiCoreClient::class)->timeseries('intent_distribution', $this->filter ?? 'last_30_days');
        } catch (AiCoreException) {
            return ['datasets' => [], 'labels' => []];
        }

        $points = collect($result['points'] ?? [])->sortByDesc('value')->values();
        $total = $points->sum('value');

        if ($total <= 0) {
            return ['datasets' => [], 'labels' => []];
        }

        $top = $points->take(count(self::PALETTE));
        $rest = $points->slice(count(self::PALETTE));

        $entries = $top->map(fn ($point) => [
            'label' => Intents::label($point['label']),
            'value' => $point['value'],
        ]);

        if ($rest->isNotEmpty()) {
            $entries->push(['label' => 'Autres', 'value' => $rest->sum('value')]);
        }

        // Baked directly into the legend text (not just a tooltip-on-hover)
        // so the distribution reads at a glance without hovering each slice -
        // Chart.js's doughnut legend only ever displays raw labels/data.
        $labels = $entries->map(
            fn ($e) => sprintf('%s — %d (%d%%)', $e['label'], $e['value'], round($e['value'] / $total * 100))
        )->all();

        return [
            'datasets' => [
                [
                    'data' => $entries->pluck('value')->all(),
                    'backgroundColor' => array_slice([...self::PALETTE, '#8a8a86'], 0, $entries->count()),
                ],
            ],
            'labels' => $labels,
        ];
    }

    protected function getType(): string
    {
        return 'doughnut';
    }

    protected function getOptions(): array
    {
        // A doughnut has no cartesian axes - without this Chart.js still
        // draws a default linear grid behind the slices.
        return [
            'scales' => [
                'x' => ['display' => false],
                'y' => ['display' => false],
            ],
        ];
    }
}
