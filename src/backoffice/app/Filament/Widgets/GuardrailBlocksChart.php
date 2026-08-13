<?php

namespace App\Filament\Widgets;

use App\Filament\Widgets\Concerns\FetchesTimeseries;
use Filament\Widgets\ChartWidget;

class GuardrailBlocksChart extends ChartWidget
{
    use FetchesTimeseries;

    protected static ?string $heading = 'Blocages guardrails';

    protected static ?int $sort = 5;

    public ?string $filter = 'last_30_days';

    protected function metric(): string
    {
        return 'guardrail_blocks';
    }

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
        $points = $this->points();

        return [
            'datasets' => [
                [
                    'label' => 'Blocages',
                    'data' => array_column($points, 'value'),
                    'backgroundColor' => '#e34948',
                ],
            ],
            'labels' => array_column($points, 'label'),
        ];
    }

    protected function getType(): string
    {
        return 'bar';
    }
}
