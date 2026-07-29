<x-filament-panels::page>
    <form wire:submit="sendCommand" class="space-y-4">
        {{ $this->form }}

        <x-filament::button type="submit">
            Envoyer à l'agent IA
        </x-filament::button>
    </form>

    @if ($result)
        <div class="mt-6 space-y-4">
            <x-filament::section heading="Réponse de l'agent">
                <p class="text-sm">{{ $result['response'] ?? '' }}</p>
            </x-filament::section>

            @if (!empty($result['insights']))
                <x-filament::section heading="Insights">
                    <div class="space-y-3">
                        @foreach ($result['insights'] as $insight)
                            <div class="rounded-lg border border-gray-200 dark:border-gray-700 p-3">
                                <p class="font-medium">{{ $insight['title'] ?? '' }}</p>
                                @if (!empty($insight['value']))
                                    <p class="text-lg font-bold">{{ $insight['value'] }}</p>
                                @endif
                                <p class="text-sm text-gray-500">{{ $insight['description'] ?? '' }}</p>
                            </div>
                        @endforeach
                    </div>
                </x-filament::section>
            @endif

            @if (!empty($result['proposed_actions']))
                <x-filament::section heading="Actions proposées">
                    <div class="space-y-3">
                        @foreach ($result['proposed_actions'] as $action)
                            <div class="rounded-lg border border-gray-200 dark:border-gray-700 p-3 flex items-center justify-between gap-4">
                                <div>
                                    <p class="font-medium">{{ $action['description'] ?? '' }}</p>
                                    <p class="text-xs uppercase text-gray-500">
                                        Risque: {{ $action['risk_level'] ?? 'inconnu' }}
                                    </p>
                                </div>
                                <x-filament::button
                                    size="sm"
                                    wire:click="confirmAction('{{ $action['action_id'] }}')"
                                >
                                    Confirmer
                                </x-filament::button>
                            </div>
                        @endforeach
                    </div>
                </x-filament::section>
            @else
                <p class="text-sm text-gray-500">Aucune action proposée pour cette commande.</p>
            @endif
        </div>
    @endif
</x-filament-panels::page>
