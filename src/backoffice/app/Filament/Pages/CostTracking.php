<?php

namespace App\Filament\Pages;

use App\Exceptions\AiCoreException;
use App\Services\AiCoreClient;
use Filament\Pages\Page;

class CostTracking extends Page
{
    protected static ?string $navigationIcon = 'heroicon-o-currency-dollar';

    protected static ?string $navigationLabel = 'Coûts & Messages';

    protected static ?string $navigationGroup = 'Analytique';

    protected static ?int $navigationSort = 1;

    protected static ?string $title = 'Coûts & Messages';

    protected static string $view = 'filament.pages.cost-tracking';

    public const TIME_RANGES = [
        'last_7_days' => '7 derniers jours',
        'last_30_days' => '30 derniers jours',
        'this_month' => 'Ce mois-ci',
        'last_month' => 'Mois dernier',
    ];

    public string $timeRange = 'last_30_days';

    public ?array $report = null;

    public ?string $loadError = null;

    public function mount(): void
    {
        $this->loadReport();
    }

    public function updatedTimeRange(): void
    {
        $this->loadReport();
    }

    protected function loadReport(): void
    {
        try {
            $this->report = app(AiCoreClient::class)->costReport($this->timeRange, 20);
            $this->loadError = null;
        } catch (AiCoreException $e) {
            $this->report = null;
            $this->loadError = $e->getMessage();
        }
    }
}
