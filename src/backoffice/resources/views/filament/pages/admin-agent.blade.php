<x-filament-panels::page>
    <form wire:submit="sendCommand" class="space-y-4">
        {{ $this->form }}

        <x-filament::button type="submit">
            Envoyer à l'agent IA
        </x-filament::button>
    </form>

    @if ($result)
        <div class="mt-6 space-y-4">
            <x-filament::section :heading="'Action: ' . ($result['action_name'] ?? 'inconnue')">
                <div class="flex items-center gap-2 mb-3">
                    <x-filament::badge :color="match ($result['status'] ?? null) {
                        'completed' => 'success',
                        'pending_confirmation', 'pending_human_approval' => 'warning',
                        'rejected', 'expired' => 'gray',
                        default => 'danger',
                    }">
                        {{ $result['status'] ?? 'échec' }}
                    </x-filament::badge>
                </div>

                @if (!empty($result['error']))
                    <p class="text-sm text-danger-600 dark:text-danger-400">{{ $result['error'] }}</p>
                @endif

                @if (!empty($result['dry_run']))
                    <div class="mt-3 rounded-lg border border-gray-200 dark:border-white/10 p-3 space-y-1">
                        <p class="text-xs font-medium uppercase text-gray-500">Simulation (dry run)</p>
                        <p class="text-sm">Éléments affectés : {{ $result['dry_run']['affected_items_count'] ?? 0 }}</p>
                        @foreach (($result['dry_run']['warnings'] ?? []) as $warning)
                            <p class="text-sm text-warning-600 dark:text-warning-400">⚠ {{ $warning }}</p>
                        @endforeach
                    </div>
                @endif

                @if (($result['status'] ?? null) === 'pending_confirmation' && !empty($result['confirmation_token']))
                    <div class="mt-3 flex gap-2">
                        <x-filament::button
                            size="sm"
                            wire:click="confirmAction('{{ $result['action_id'] }}', '{{ $result['confirmation_token'] }}')"
                        >
                            Confirmer
                        </x-filament::button>
                        <x-filament::button
                            size="sm"
                            color="danger"
                            wire:click="rejectAction('{{ $result['action_id'] }}')"
                        >
                            Rejeter
                        </x-filament::button>
                    </div>
                @endif

                @if (!empty($result['data']['analysis']))
                    <div class="mt-3 whitespace-pre-line text-sm">{{ $result['data']['analysis'] }}</div>
                @elseif (!empty($result['data']))
                    <div class="mt-3">
                        <x-filament::badge color="gray">Données</x-filament::badge>
                        <pre class="mt-2 text-xs overflow-x-auto rounded-lg bg-gray-50 dark:bg-white/5 p-3">{{ json_encode($result['data'], JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE) }}</pre>
                    </div>
                @endif
            </x-filament::section>
        </div>
    @endif
</x-filament-panels::page>
