<?php

namespace App\Filament\Pages;

use App\Exceptions\AiCoreException;
use App\Services\AiCoreClient;
use Filament\Forms\Components\Textarea;
use Filament\Forms\Concerns\InteractsWithForms;
use Filament\Forms\Contracts\HasForms;
use Filament\Forms\Form;
use Filament\Notifications\Notification;
use Filament\Pages\Page;

class AdminAgent extends Page implements HasForms
{
    use InteractsWithForms;

    protected static ?string $navigationIcon = 'heroicon-o-chat-bubble-left-right';

    protected static ?string $navigationLabel = 'Agent IA Admin';

    protected static ?string $title = 'Agent IA Admin';

    protected static string $view = 'filament.pages.admin-agent';

    public ?array $data = [];

    public ?array $result = null;

    public function mount(): void
    {
        $this->form->fill();
    }

    public function form(Form $form): Form
    {
        return $form
            ->schema([
                Textarea::make('command')
                    ->label('Commande en langage naturel')
                    ->placeholder('Ex: Analyse les ventes du mois dernier et suggère une stratégie marketing')
                    ->rows(3)
                    ->required(),
            ])
            ->statePath('data');
    }

    public function sendCommand(): void
    {
        $state = $this->form->getState();

        try {
            $this->result = app(AiCoreClient::class)->sendAdminCommand($state['command'] ?? '');
        } catch (AiCoreException $e) {
            Notification::make()
                ->title('Erreur')
                ->body($e->getMessage())
                ->danger()
                ->send();

            return;
        }

        Notification::make()
            ->title('Commande traitée')
            ->success()
            ->send();
    }

    public function confirmAction(string $actionId): void
    {
        try {
            app(AiCoreClient::class)->confirmAdminAction($actionId, confirmed: true);
        } catch (AiCoreException $e) {
            Notification::make()
                ->title('Erreur')
                ->body($e->getMessage())
                ->danger()
                ->send();

            return;
        }

        Notification::make()
            ->title('Action confirmée et exécutée')
            ->success()
            ->send();
    }
}
