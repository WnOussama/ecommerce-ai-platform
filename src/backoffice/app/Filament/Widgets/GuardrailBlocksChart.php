<?php

namespace App\Filament\Widgets;

use App\Exceptions\AiCoreException;
use App\Services\AiCoreClient;
use Filament\Widgets\ChartWidget;

class GuardrailBlocksChart extends ChartWidget
{
    protected static ?string $heading = 'Blocages guardrails';

    protected static ?int $sort = 5;

    public ?string $filter = 'last_30_days';

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
            $result = app(AiCoreClient::class)->timeseries('guardrail_blocks', $this->filter ?? 'last_30_days');
        } catch (AiCoreException) {
            return ['datasets' => [], 'labels' => []];
        }

        $points = $result['points'] ?? [];

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
