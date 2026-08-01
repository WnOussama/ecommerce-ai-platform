<?php

namespace App\Filament\Pages;

use App\Exceptions\AiCoreException;
use App\Services\AiCoreClient;
use Filament\Pages\Page;

class Insights extends Page
{
    protected static ?string $navigationIcon = 'heroicon-o-light-bulb';

    protected static ?string $navigationLabel = 'Insights';

    protected static ?string $navigationGroup = 'Analytique';

    protected static ?int $navigationSort = 3;

    protected static ?string $title = 'Insights';

    protected static string $view = 'filament.pages.insights';

    public const TIME_RANGES = [
        'last_7_days' => '7 derniers jours',
        'last_30_days' => '30 derniers jours',
        'this_month' => 'Ce mois-ci',
    ];

    public string $timeRange = 'last_30_days';

    public ?array $summary = null;

    public ?string $loadError = null;

    public function mount(): void
    {
        $this->loadSummary();
    }

    public function updatedTimeRange(): void
    {
        $this->loadSummary();
    }

    protected function loadSummary(): void
    {
        try {
            $this->summary = app(AiCoreClient::class)->insightsSummary($this->timeRange);
            $this->loadError = null;
        } catch (AiCoreException $e) {
            $this->summary = null;
            $this->loadError = $e->getMessage();
        }
    }
}
