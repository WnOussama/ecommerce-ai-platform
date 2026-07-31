<?php

namespace App\Filament\Widgets;

use App\Exceptions\AiCoreException;
use App\Services\AiCoreClient;
use Filament\Widgets\StatsOverviewWidget as BaseWidget;
use Filament\Widgets\StatsOverviewWidget\Stat;

class AiPerformanceOverview extends BaseWidget
{
    protected function getStats(): array
    {
        $client = app(AiCoreClient::class);

        try {
            $data = $client->aiPerformance();
        } catch (AiCoreException) {
            return [];
        }

        return [
            // Pas une "précision" au sens classique (aucune vérité terrain
            // étiquetée n'existe) - c'est la part des messages où le
            // classifieur a trouvé une intention spécifique. Le libellé le
            // dit explicitement pour ne pas surinterpréter le chiffre.
            Stat::make('Intentions reconnues', number_format(($data['intent_accuracy'] ?? 0) * 100, 1).'%')
                ->description('Part des messages hors catégorie "general"')
                ->color('success'),

            Stat::make('Taux d\'hallucination', number_format(($data['hallucination_rate'] ?? 0) * 100, 1).'%')
                ->color(($data['hallucination_rate'] ?? 0) > 0.05 ? 'danger' : 'success'),

            Stat::make('Blocages guardrails', (string) ($data['guardrail_blocks'] ?? 0))
                ->description('Prompt injection / contenu bloqué'),

            Stat::make('Coût LLM', '$'.number_format($data['llm_cost_usd'] ?? 0, 2))
                ->description($data['time_range'] ?? ''),
        ];
    }
}
