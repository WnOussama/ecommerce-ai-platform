<?php

namespace App\Filament\Pages;

use App\Exceptions\AiCoreException;
use App\Services\AiCoreClient;
use Filament\Forms\Components\Select;
use Filament\Forms\Components\Textarea;
use Filament\Forms\Components\TextInput;
use Filament\Forms\Components\Toggle;
use Filament\Forms\Concerns\InteractsWithForms;
use Filament\Forms\Contracts\HasForms;
use Filament\Forms\Form;
use Filament\Notifications\Notification;
use Filament\Pages\Page;

class TenantSettings extends Page implements HasForms
{
    use InteractsWithForms;

    protected static ?string $navigationIcon = 'heroicon-o-cog-6-tooth';

    protected static ?string $navigationLabel = 'Tenant';

    protected static ?string $title = 'Configuration du tenant';

    protected static string $view = 'filament.pages.tenant-settings';

    public ?array $data = [];

    public ?array $tenant = null;

    public ?array $usage = null;

    public ?string $loadError = null;

    public function mount(): void
    {
        $client = app(AiCoreClient::class);

        try {
            $this->tenant = $client->currentTenant();
            $this->usage = $client->tenantUsage();
            $settings = $client->tenantSettings();
        } catch (AiCoreException $e) {
            $this->loadError = $e->getMessage();
            $settings = ['settings' => []];
        }

        $this->form->fill($settings['settings'] ?? []);
    }

    public function form(Form $form): Form
    {
        return $form
            ->schema([
                Toggle::make('chatbot_enabled')->label('Chatbot activé'),
                Toggle::make('recommendations_enabled')->label('Recommandations activées'),
                Toggle::make('coupons_enabled')->label('Coupons activés'),
                Select::make('default_language')
                    ->label('Langue par défaut')
                    ->options(['fr' => 'Français', 'en' => 'English']),
                Textarea::make('welcome_message')
                    ->label('Message d\'accueil')
                    ->rows(2),
                TextInput::make('llm_model')
                    ->label('Modèle LLM'),
                TextInput::make('max_response_tokens')
                    ->label('Tokens max par réponse')
                    ->numeric(),
            ])
            ->statePath('data');
    }

    public function save(): void
    {
        $settings = $this->form->getState();

        try {
            app(AiCoreClient::class)->updateTenantSettings($settings);
        } catch (AiCoreException $e) {
            Notification::make()->title('Erreur')->body($e->getMessage())->danger()->send();

            return;
        }

        Notification::make()->title('Paramètres enregistrés')->success()->send();
    }

    public function rotateApiKey(): void
    {
        try {
            $result = app(AiCoreClient::class)->rotateApiKey();
        } catch (AiCoreException $e) {
            Notification::make()->title('Erreur')->body($e->getMessage())->danger()->send();

            return;
        }

        Notification::make()
            ->title('Clé API régénérée')
            ->body($result['api_key'] ?? '')
            ->success()
            ->persistent()
            ->send();
    }
}
