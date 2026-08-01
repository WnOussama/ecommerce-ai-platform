<?php

namespace App\Filament\Pages;

use App\Exceptions\AiCoreException;
use App\Services\AiCoreClient;
use App\Support\Intents;
use Filament\Forms\Components\Select;
use Filament\Forms\Components\Textarea;
use Filament\Forms\Components\TextInput;
use Filament\Forms\Components\Toggle;
use Filament\Forms\Concerns\InteractsWithForms;
use Filament\Forms\Contracts\HasForms;
use Filament\Forms\Form;
use Filament\Forms\Get;
use Filament\Notifications\Notification;
use Filament\Pages\Page;

class Rules extends Page implements HasForms
{
    use InteractsWithForms;

    protected static ?string $navigationIcon = 'heroicon-o-adjustments-horizontal';

    protected static ?string $navigationLabel = 'Règles';

    protected static ?string $navigationGroup = 'Assistant IA';

    protected static ?int $navigationSort = 2;

    protected static ?string $title = 'Règles du chatbot';

    protected static string $view = 'filament.pages.rules';

    public const ACTION_TYPES = [
        'canned_response' => 'Réponse toute faite (court-circuite le LLM)',
        'inject_instruction' => 'Instruction injectée dans le prompt système',
        'generate_coupon' => 'Génère un coupon',
    ];

    public ?array $data = [];

    public array $rules = [];

    public ?string $editingRuleId = null;

    public ?string $loadError = null;

    public function mount(): void
    {
        $this->loadRules();
        $this->form->fill(['action_type' => 'canned_response', 'priority' => 0, 'is_active' => true]);
    }

    protected function loadRules(): void
    {
        try {
            $this->rules = app(AiCoreClient::class)->listRules();
            $this->loadError = null;
        } catch (AiCoreException $e) {
            $this->rules = [];
            $this->loadError = $e->getMessage();
        }
    }

    public function form(Form $form): Form
    {
        return $form
            ->schema([
                TextInput::make('name')
                    ->label('Nom')
                    ->required()
                    ->maxLength(255),

                Textarea::make('description')
                    ->label('Description')
                    ->rows(2),

                Select::make('intent')
                    ->label('Intention (optionnel)')
                    ->options(collect(Intents::LABELS)->except('general')->all())
                    ->placeholder('N\'importe quelle intention'),

                TextInput::make('keywords_any')
                    ->label('Mots-clés - au moins un (séparés par des virgules)')
                    ->helperText('La règle matche si le message contient au moins un de ces mots.'),

                TextInput::make('keywords_all')
                    ->label('Mots-clés - tous requis (séparés par des virgules)')
                    ->helperText('La règle matche seulement si le message contient tous ces mots.'),

                Select::make('action_type')
                    ->label('Action')
                    ->options(self::ACTION_TYPES)
                    ->required()
                    ->live(),

                Textarea::make('text')
                    ->label('Texte de la réponse')
                    ->rows(3)
                    ->required(fn (Get $get) => $get('action_type') === 'canned_response')
                    ->visible(fn (Get $get) => $get('action_type') === 'canned_response'),

                Textarea::make('instruction')
                    ->label('Instruction pour le LLM')
                    ->rows(3)
                    ->helperText('Ajoutée au prompt système - ex: "Mentionne toujours notre programme de fidélité."')
                    ->required(fn (Get $get) => $get('action_type') === 'inject_instruction')
                    ->visible(fn (Get $get) => $get('action_type') === 'inject_instruction'),

                TextInput::make('discount_percent')
                    ->label('Remise (%)')
                    ->numeric()
                    ->minValue(1)
                    ->maxValue(100)
                    ->required(fn (Get $get) => $get('action_type') === 'generate_coupon')
                    ->visible(fn (Get $get) => $get('action_type') === 'generate_coupon'),

                TextInput::make('validity_days')
                    ->label('Validité (jours)')
                    ->numeric()
                    ->minValue(1)
                    ->default(7)
                    ->visible(fn (Get $get) => $get('action_type') === 'generate_coupon'),

                TextInput::make('reason')
                    ->label('Raison (optionnel)')
                    ->helperText('Ex: "cart_abandonment", "loyalty", "winback" - relie cette règle à la politique de remise utilisée par /coupons/generate.')
                    ->visible(fn (Get $get) => $get('action_type') === 'generate_coupon'),

                TextInput::make('priority')
                    ->label('Priorité')
                    ->numeric()
                    ->default(0)
                    ->helperText('Les règles sont évaluées par priorité croissante - 0 en premier.'),

                Toggle::make('is_active')
                    ->label('Active')
                    ->default(true),
            ])
            ->statePath('data');
    }

    public function save(): void
    {
        $state = $this->form->getState();

        $conditions = array_filter([
            'intent' => $state['intent'] ?? null,
            'keywords_any' => $this->splitKeywords($state['keywords_any'] ?? null),
            'keywords_all' => $this->splitKeywords($state['keywords_all'] ?? null),
        ], fn ($value) => filled($value));

        $action = $this->buildAction($state);

        $payload = [
            'name' => $state['name'],
            'description' => $state['description'] ?: null,
            'conditions' => $conditions,
            'action' => $action,
            'priority' => (int) ($state['priority'] ?? 0),
            'is_active' => (bool) ($state['is_active'] ?? true),
        ];

        try {
            if ($this->editingRuleId) {
                app(AiCoreClient::class)->updateRule($this->editingRuleId, $payload);
            } else {
                app(AiCoreClient::class)->createRule($payload);
            }
        } catch (AiCoreException $e) {
            Notification::make()->title('Erreur')->body($e->getMessage())->danger()->send();

            return;
        }

        Notification::make()->title($this->editingRuleId ? 'Règle mise à jour' : 'Règle créée')->success()->send();

        $this->cancelEdit();
        $this->loadRules();
    }

    public function editRule(string $ruleId): void
    {
        $rule = collect($this->rules)->firstWhere('id', $ruleId);

        if (! $rule) {
            return;
        }

        $conditions = $rule['conditions'] ?? [];
        $action = $rule['action'] ?? [];

        $this->editingRuleId = $ruleId;

        $this->form->fill([
            'name' => $rule['name'] ?? '',
            'description' => $rule['description'] ?? '',
            'intent' => $conditions['intent'] ?? null,
            'keywords_any' => implode(', ', $conditions['keywords_any'] ?? []),
            'keywords_all' => implode(', ', $conditions['keywords_all'] ?? []),
            'action_type' => $action['type'] ?? 'canned_response',
            'text' => $action['text'] ?? '',
            'instruction' => $action['instruction'] ?? '',
            'discount_percent' => $action['discount_percent'] ?? null,
            'validity_days' => $action['validity_days'] ?? 7,
            'reason' => $action['reason'] ?? '',
            'priority' => $rule['priority'] ?? 0,
            'is_active' => $rule['is_active'] ?? true,
        ]);
    }

    public function cancelEdit(): void
    {
        $this->editingRuleId = null;
        $this->form->fill(['action_type' => 'canned_response', 'priority' => 0, 'is_active' => true]);
    }

    public function deleteRule(string $ruleId): void
    {
        try {
            app(AiCoreClient::class)->deleteRule($ruleId);
        } catch (AiCoreException $e) {
            Notification::make()->title('Erreur')->body($e->getMessage())->danger()->send();

            return;
        }

        Notification::make()->title('Règle supprimée')->success()->send();

        if ($this->editingRuleId === $ruleId) {
            $this->cancelEdit();
        }

        $this->loadRules();
    }

    protected function buildAction(array $state): array
    {
        return match ($state['action_type']) {
            'canned_response' => [
                'type' => 'canned_response',
                'text' => $state['text'] ?? '',
            ],
            'inject_instruction' => [
                'type' => 'inject_instruction',
                'instruction' => $state['instruction'] ?? '',
            ],
            'generate_coupon' => array_filter([
                'type' => 'generate_coupon',
                'discount_percent' => (int) ($state['discount_percent'] ?? 10),
                'validity_days' => (int) ($state['validity_days'] ?? 7),
                'reason' => $state['reason'] ?: null,
            ], fn ($value) => $value !== null),
            default => ['type' => $state['action_type']],
        };
    }

    protected function splitKeywords(?string $raw): array
    {
        if (blank($raw)) {
            return [];
        }

        return collect(explode(',', $raw))
            ->map(fn ($keyword) => trim($keyword))
            ->filter()
            ->values()
            ->all();
    }
}
