<?php

namespace App\Filament\Widgets\Concerns;

use App\Exceptions\AiCoreException;
use App\Services\AiCoreClient;
use Illuminate\Contracts\Support\Htmlable;

/**
 * Shared fetch + empty-state handling for the four /analytics/timeseries
 * chart widgets. A tenant with no chat activity yet gets a real, empty
 * `points` array back from the AI Core - without this, Filament's
 * ChartWidget still renders a bare <canvas> for that (correctly empty)
 * data, which just looks blank/broken rather than "no data yet".
 *
 * getDescription() is called by Filament's chart-widget view before
 * getData() - it must trigger the fetch itself (not rely on getData()
 * having already cached it) for the empty-state message to be correct
 * on first render.
 */
trait FetchesTimeseries
{
    protected ?array $resolvedPoints = null;

    abstract protected function metric(): string;

    protected function points(): array
    {
        if ($this->resolvedPoints === null) {
            try {
                $result = app(AiCoreClient::class)->timeseries($this->metric(), $this->filter ?? 'last_30_days');
                $this->resolvedPoints = $result['points'] ?? [];
            } catch (AiCoreException) {
                $this->resolvedPoints = [];
            }
        }

        return $this->resolvedPoints;
    }

    protected function hasData(): bool
    {
        return collect($this->points())->sum('value') > 0;
    }

    public function getDescription(): string|Htmlable|null
    {
        return $this->hasData() ? static::$description : 'Aucune activité sur cette période.';
    }
}
