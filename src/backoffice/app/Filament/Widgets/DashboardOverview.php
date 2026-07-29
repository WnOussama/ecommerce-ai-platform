<?php

namespace App\Filament\Widgets;

use App\Exceptions\AiCoreException;
use App\Services\AiCoreClient;
use Filament\Widgets\StatsOverviewWidget as BaseWidget;
use Filament\Widgets\StatsOverviewWidget\Stat;

class DashboardOverview extends BaseWidget
{
    protected function getStats(): array
    {
        $client = app(AiCoreClient::class);

        try {
            $data = $client->dashboardMetrics();
        } catch (AiCoreException) {
            return [
                Stat::make('AI Core', 'Indisponible')
                    ->description('Impossible de contacter l\'API - vérifier que le serveur tourne et que AICORE_TENANT_ID est configuré')
                    ->color('danger'),
            ];
        }

        return collect($data['metrics'] ?? [])
            ->map(function (array $metric) {
                $trendIcon = match ($metric['trend'] ?? null) {
                    'up' => 'heroicon-m-arrow-trending-up',
                    'down' => 'heroicon-m-arrow-trending-down',
                    default => null,
                };

                $trendColor = match (true) {
                    ($metric['change'] ?? 0) > 0 => 'success',
                    ($metric['change'] ?? 0) < 0 => 'danger',
                    default => 'gray',
                };

                $value = $metric['unit'] === 'percent'
                    ? number_format($metric['value'], 1).'%'
                    : number_format($metric['value'], $metric['unit'] === 'count' ? 0 : 1);

                return Stat::make($metric['name'], $value)
                    ->description(($metric['change'] ?? 0) >= 0 ? '+'.$metric['change'].' vs période précédente' : $metric['change'].' vs période précédente')
                    ->descriptionIcon($trendIcon)
                    ->color($trendColor);
            })
            ->toArray();
    }
}
