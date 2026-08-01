<?php

namespace App\Filament\Pages;

use App\Exceptions\AiCoreException;
use App\Services\AiCoreClient;
use Filament\Pages\Page;

class Conversations extends Page
{
    protected static ?string $navigationIcon = 'heroicon-o-inbox-stack';

    protected static ?string $navigationLabel = 'Conversations';

    protected static ?string $navigationGroup = 'Analytique';

    protected static ?int $navigationSort = 2;

    protected static ?string $title = 'Conversations';

    protected static string $view = 'filament.pages.conversations';

    public const STATUSES = [
        '' => 'Tous les statuts',
        'active' => 'Active',
        'resolved' => 'Résolue',
        'escalated' => 'Escaladée',
        'abandoned' => 'Abandonnée',
    ];

    public const PER_PAGE = 20;

    public string $statusFilter = '';

    public int $page = 1;

    public array $conversations = [];

    public int $total = 0;

    public ?string $loadError = null;

    public ?string $selectedConversationId = null;

    public ?array $selectedHistory = null;

    public ?string $historyError = null;

    public function mount(): void
    {
        $this->loadConversations();
    }

    public function updatedStatusFilter(): void
    {
        $this->page = 1;
        $this->loadConversations();
    }

    public function goToPage(int $page): void
    {
        $this->page = max(1, $page);
        $this->loadConversations();
    }

    protected function loadConversations(): void
    {
        try {
            $result = app(AiCoreClient::class)->listConversations(
                limit: self::PER_PAGE,
                offset: ($this->page - 1) * self::PER_PAGE,
                status: $this->statusFilter ?: null,
            );
            $this->conversations = $result['conversations'] ?? [];
            $this->total = $result['total'] ?? 0;
            $this->loadError = null;
        } catch (AiCoreException $e) {
            $this->conversations = [];
            $this->total = 0;
            $this->loadError = $e->getMessage();
        }
    }

    public function viewConversation(string $conversationId): void
    {
        $this->selectedConversationId = $conversationId;

        try {
            $this->selectedHistory = app(AiCoreClient::class)->conversationHistory($conversationId);
            $this->historyError = null;
        } catch (AiCoreException $e) {
            $this->selectedHistory = null;
            $this->historyError = $e->getMessage();
        }
    }

    public function closeDetail(): void
    {
        $this->selectedConversationId = null;
        $this->selectedHistory = null;
        $this->historyError = null;
    }

    public function getTotalPagesProperty(): int
    {
        return max(1, (int) ceil($this->total / self::PER_PAGE));
    }
}
