<x-filament-panels::page>
    @if ($loadError)
        <x-filament::section>
            <p class="text-danger-600">Impossible de contacter l'AI Core: {{ $loadError }}</p>
        </x-filament::section>
    @endif

    @if ($tenant)
        <x-filament::section heading="Boutique">
            <div class="grid grid-cols-2 gap-4 text-sm">
                <div><span class="text-gray-500">Nom:</span> {{ $tenant['name'] ?? '' }}</div>
                <div><span class="text-gray-500">Domaine:</span> {{ $tenant['domain'] ?? '' }}</div>
                <div><span class="text-gray-500">Plateforme:</span> {{ $tenant['platform'] ?? '' }}</div>
                <div><span class="text-gray-500">Plan:</span> {{ $tenant['plan'] ?? '' }}</div>
                <div><span class="text-gray-500">Statut:</span> {{ $tenant['status'] ?? '' }}</div>
                <div><span class="text-gray-500">Fonctionnalités:</span> {{ implode(', ', $tenant['features'] ?? []) }}</div>
            </div>
        </x-filament::section>
    @endif

    @if ($usage)
        <x-filament::section heading="Utilisation ({{ $usage['period'] ?? '' }})">
            <div class="grid grid-cols-3 gap-4 text-sm">
                @foreach (($usage['usage'] ?? []) as $key => $value)
                    <div>
                        <span class="text-gray-500">{{ str_replace('_', ' ', $key) }}:</span>
                        <span class="font-semibold">{{ $value }}</span>
                    </div>
                @endforeach
            </div>
        </x-filament::section>
    @endif

    <form wire:submit="save">
        <x-filament::section heading="Paramètres">
            {{ $this->form }}

            <div class="mt-4">
                <x-filament::button type="submit">
                    Enregistrer
                </x-filament::button>
            </div>
        </x-filament::section>
    </form>

    <x-filament::section heading="Clé API">
        <p class="text-sm text-gray-500 mb-3">
            Régénère la clé API du tenant. La clé actuelle sera invalidée immédiatement.
        </p>
        <x-filament::button color="danger" wire:click="rotateApiKey" wire:confirm="Régénérer la clé API ? La clé actuelle sera invalidée immédiatement.">
            Régénérer la clé API
        </x-filament::button>
    </x-filament::section>
</x-filament-panels::page>
