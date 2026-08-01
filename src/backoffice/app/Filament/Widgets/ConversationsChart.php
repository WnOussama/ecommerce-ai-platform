<?php

namespace App\Filament\Widgets;

use App\Filament\Widgets\Concerns\FetchesTimeseries;
use Filament\Widgets\ChartWidget;

class ConversationsChart extends ChartWidget
{
    use FetchesTimeseries;

    protected static ?string $heading = 'Conversations';

    protected static ?int $sort = 3;

    public ?string $filter = 'last_30_days';

    protected function metric(): string
    {
        return 'conversations';
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
                    'label' => 'Conversations',
                    'data' => array_column($points, 'value'),
                    'borderColor' => '#2a78d6',
                    'backgroundColor' => 'rgba(42, 120, 214, 0.15)',
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
