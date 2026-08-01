<?php

namespace App\Filament\Pages;

use App\Exceptions\AiCoreException;
use App\Services\AiCoreClient;
use Filament\Forms\Components\KeyValue;
use Filament\Forms\Components\Select;
use Filament\Forms\Components\Textarea;
use Filament\Forms\Components\TextInput;
use Filament\Forms\Concerns\InteractsWithForms;
use Filament\Forms\Contracts\HasForms;
use Filament\Forms\Form;
use Filament\Forms\Get;
use Filament\Notifications\Notification;
use Filament\Pages\Page;

class AdminAgent extends Page implements HasForms
{
    use InteractsWithForms;

    protected static ?string $navigationIcon = 'heroicon-o-chat-bubble-left-right';

    protected static ?string $navigationLabel = 'Agent IA Admin';

    protected static ?string $navigationGroup = 'Assistant IA';

    protected static ?int $navigationSort = 1;

    protected static ?string $title = 'Agent IA Admin';

    protected static string $view = 'filament.pages.admin-agent';

    /**
     * Predefined actions the AI Core will actually execute - see
     * ACTION_DEFINITIONS in admin_safety.py. Free text is classified
     * server-side into one of these; picking one directly here is required
     * for actions that need real parameters (e.g. generate_bulk_coupons).
     */
    public const ACTIONS = [
        'get_analytics' => 'Statistiques (accès direct, faible risque)',
        'generate_report' => 'Rapport (accès direct, faible risque)',
        'suggest_marketing_strategy' => 'Stratégie marketing (confirmation simple)',
        'segment_customers' => 'Segmentation clients (confirmation simple)',
        'generate_bulk_coupons' => 'Coupons en masse (double confirmation)',
        'update_product_prices' => 'Mise à jour des prix (double confirmation)',
        'delete_customer_data' => 'Suppression données client - RGPD (approbation humaine)',
        'bulk_order_modification' => 'Modification commandes en masse (approbation humaine)',
    ];

    public ?array $data = [];

    /**
     * The conversation transcript for this session - not persisted, mirrors
     * the previous page's single "$result" but keeps history instead of
     * overwriting it on every turn. Each entry is either
     * ['role' => 'user', 'text' => string] or
     * ['role' => 'agent', 'result' => array] / ['role' => 'agent', 'error' => string].
     */
    public array $messages = [];

    public function mount(): void
    {
        $this->form->fill();
    }

    public function form(Form $form): Form
    {
        return $form
            ->schema([
                Select::make('action_name')
                    ->label('Action')
                    ->options(self::ACTIONS)
                    ->placeholder('Texte libre (classifié automatiquement)')
                    ->live(),

                Textarea::make('command')
                    ->label('Commande en langage naturel')
                    ->placeholder('Ex: Analyse les demandes clients de la semaine')
                    ->rows(2)
                    ->required(fn (Get $get) => blank($get('action_name')))
                    ->visible(fn (Get $get) => blank($get('action_name'))),

                KeyValue::make('parameters')
                    ->label('Paramètres')
                    ->keyLabel('Clé')
                    ->valueLabel('Valeur')
                    ->visible(fn (Get $get) => filled($get('action_name'))),

                TextInput::make('reason')
                    ->label('Motif')
                    ->helperText('Requis pour les actions à double confirmation ou approbation humaine.')
                    ->visible(fn (Get $get) => filled($get('action_name'))),
            ])
            ->statePath('data');
    }

    public function sendCommand(): void
    {
        $state = $this->form->getState();

        $this->messages[] = [
            'role' => 'user',
            'text' => filled($state['action_name'] ?? null)
                ? (self::ACTIONS[$state['action_name']] ?? $state['action_name'])
                : ($state['command'] ?? ''),
        ];

        try {
            $result = app(AiCoreClient::class)->sendAdminCommand(
                command: $state['command'] ?? null,
                actionName: $state['action_name'] ?? null,
                parameters: $state['parameters'] ?? [],
                reason: $state['reason'] ?? '',
            );
        } catch (AiCoreException $e) {
            $this->messages[] = ['role' => 'agent', 'error' => $e->getMessage()];

            return;
        }

        $this->messages[] = ['role' => 'agent', 'result' => $result];
        $this->notifyForResult($result);

        $this->form->fill();
    }

    public function confirmAction(string $actionId, string $confirmationToken): void
    {
        $this->messages[] = ['role' => 'user', 'text' => '→ Confirmer'];

        try {
            $result = app(AiCoreClient::class)->confirmAdminAction($actionId, $confirmationToken);
        } catch (AiCoreException $e) {
            $this->messages[] = ['role' => 'agent', 'error' => $e->getMessage()];

            return;
        }

        $this->messages[] = ['role' => 'agent', 'result' => $result];
        $this->notifyForResult($result);
    }

    public function rejectAction(string $actionId): void
    {
        $this->messages[] = ['role' => 'user', 'text' => '→ Rejeter'];

        try {
            $result = app(AiCoreClient::class)->rejectAdminAction($actionId);
        } catch (AiCoreException $e) {
            $this->messages[] = ['role' => 'agent', 'error' => $e->getMessage()];

            return;
        }

        $this->messages[] = [
            'role' => 'agent',
            'result' => ['action_name' => '', 'status' => $result['status'] ?? 'rejected', 'success' => true, 'data' => []],
        ];

        Notification::make()->title('Action rejetée')->success()->send();
    }

    protected function notifyForResult(array $result): void
    {
        if (! ($result['success'] ?? false)) {
            Notification::make()
                ->title('Échec')
                ->body($result['error'] ?? 'Erreur inconnue')
                ->danger()
                ->send();

            return;
        }

        Notification::make()
            ->title(match ($result['status'] ?? null) {
                'completed' => 'Action exécutée',
                'pending_confirmation' => 'Confirmation requise',
                'pending_human_approval' => 'Approbation humaine requise',
                default => 'Commande traitée',
            })
            ->success()
            ->send();
    }
}
