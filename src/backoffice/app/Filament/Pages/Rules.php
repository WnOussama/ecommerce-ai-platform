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
use Illuminate\Support\Arr;

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

    /** Clés de `conditions` éditées par le formulaire (les autres sont conservées). */
    public const MANAGED_CONDITION_KEYS = ['intent', 'keywords_any', 'keywords_all', 'first_message', 'min_cart_total'];

    /** Clés de `action` éditées par le formulaire (les autres, ex: max_per_hour, sont conservées). */
    public const MANAGED_ACTION_KEYS = ['type', 'text', 'instruction', 'discount_percent', 'validity_days', 'reason', 'cooldown_days'];

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

                Toggle::make('first_message')
                    ->label('Seulement au premier message de la conversation')
                    ->helperText('Ex: coupon de bienvenue. Combiné avec « une seule fois par visiteur » côté coupon.'),

                TextInput::make('min_cart_total')
                    ->label('Total du panier minimum (€)')
                    ->numeric()
                    ->minValue(0)
                    ->helperText('Le total du panier est fourni par la boutique. La règle ne matche que si le panier atteint ce montant (ex: coupon de palier).'),

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

                TextInput::make('cooldown_days')
                    ->label('Nouveau coupon possible après (jours)')
                    ->numeric()
                    ->minValue(1)
                    ->helperText('Vide = un seul coupon par visiteur, pour toujours (ex: bienvenue). Sinon le même visiteur peut en recevoir un autre après ce délai (ex: palier, 30).')
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

        $original = $this->editingRuleId
            ? (collect($this->rules)->firstWhere('id', $this->editingRuleId) ?? [])
            : [];

        // Les clés que ce formulaire ne gère pas sont conservées telles quelles:
        // sans ça, modifier une règle ici les effaçait en silence.
        $conditions = Arr::except($original['conditions'] ?? [], self::MANAGED_CONDITION_KEYS)
            + array_filter([
                'intent' => $state['intent'] ?? null,
                'keywords_any' => $this->splitKeywords($state['keywords_any'] ?? null),
                'keywords_all' => $this->splitKeywords($state['keywords_all'] ?? null),
                'first_message' => ! empty($state['first_message']) ? true : null,
                'min_cart_total' => $this->positiveNumber($state['min_cart_total'] ?? null),
            ], fn ($value) => filled($value));

        $action = $this->buildAction($state);

        if (($original['action']['type'] ?? null) === ($action['type'] ?? null)) {
            $action += Arr::except($original['action'] ?? [], self::MANAGED_ACTION_KEYS);
        }

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
            'first_message' => (bool) ($conditions['first_message'] ?? false),
            'min_cart_total' => $conditions['min_cart_total'] ?? null,
            'cooldown_days' => $action['cooldown_days'] ?? null,
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
                'cooldown_days' => $this->positiveNumber($state['cooldown_days'] ?? null),
            ], fn ($value) => $value !== null),
            default => ['type' => $state['action_type']],
        };
    }

    protected function positiveNumber(mixed $value): int|float|null
    {
        if (! is_numeric($value) || (float) $value <= 0) {
            return null;
        }

        return $value + 0;
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
