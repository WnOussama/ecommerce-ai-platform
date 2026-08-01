<?php

namespace App\Filament\Widgets;

use App\Exceptions\AiCoreException;
use App\Services\AiCoreClient;
use Filament\Widgets\ChartWidget;

class LlmCostChart extends ChartWidget
{
    protected static ?string $heading = 'Coût LLM ($)';

    protected static ?int $sort = 4;

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
            $result = app(AiCoreClient::class)->timeseries('llm_cost', $this->filter ?? 'last_30_days');
        } catch (AiCoreException) {
            return ['datasets' => [], 'labels' => []];
        }

        $points = $result['points'] ?? [];

        return [
            'datasets' => [
                [
                    'label' => 'Coût LLM ($)',
                    'data' => array_column($points, 'value'),
                    'borderColor' => '#1baf7a',
                    'backgroundColor' => 'rgba(27, 175, 122, 0.15)',
                    'fill' => true,
                    'tension' => 0.3,
                ],
            ],
            'labels' => array_column($points, 'label'),
        ];
    }

    protected function getType(): string
    {
        return 'line';
    }
}
