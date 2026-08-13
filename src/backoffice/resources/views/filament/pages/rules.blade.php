<x-filament-panels::page>
    @if ($loadError)
        <x-filament::section>
            <p class="text-sm text-danger-600 dark:text-danger-400">{{ $loadError }}</p>
        </x-filament::section>
    @endif

    <x-filament::section :heading="$editingRuleId ? 'Modifier la règle' : 'Nouvelle règle'">
        <form wire:submit="save" class="space-y-4">
            {{ $this->form }}

            <div class="flex gap-2">
                <x-filament::button type="submit">
                    {{ $editingRuleId ? 'Enregistrer' : 'Créer la règle' }}
                </x-filament::button>

                @if ($editingRuleId)
                    <x-filament::button type="button" color="gray" wire:click="cancelEdit">
                        Annuler
                    </x-filament::button>
                @endif
            </div>
        </form>
    </x-filament::section>

    <x-filament::section heading="Règles existantes">
        @if (empty($rules))
            <p class="text-sm text-gray-500">Aucune règle pour ce tenant.</p>
        @else
            <div class="overflow-x-auto">
                <table class="w-full text-sm">
                    <thead>
                        <tr class="text-left text-xs uppercase text-gray-500 border-b border-gray-200 dark:border-white/10">
                            <th class="py-2 pr-4">Nom</th>
                            <th class="py-2 pr-4">Condition</th>
                            <th class="py-2 pr-4">Action</th>
                            <th class="py-2 pr-4">Priorité</th>
                            <th class="py-2 pr-4">Statut</th>
                            <th class="py-2 pr-4">Déclenchements</th>
                            <th class="py-2 pr-4"></th>
                        </tr>
                    </thead>
                    <tbody>
                        @foreach ($rules as $rule)
                            @php
                                $conditions = $rule['conditions'] ?? [];
                                $action = $rule['action'] ?? [];
                                $conditionParts = array_filter([
                                    isset($conditions['intent']) ? 'intent: '.$conditions['intent'] : null,
                                    !empty($conditions['keywords_any']) ? 'mots-clés (un): '.implode(', ', $conditions['keywords_any']) : null,
                                    !empty($conditions['keywords_all']) ? 'mots-clés (tous): '.implode(', ', $conditions['keywords_all']) : null,
                                ]);
                            @endphp
                            <tr class="border-b border-gray-100 dark:border-white/5">
                                <td class="py-2 pr-4 font-medium">{{ $rule['name'] }}</td>
                                <td class="py-2 pr-4 text-gray-500">
                                    {{ !empty($conditionParts) ? implode(' · ', $conditionParts) : '—' }}
                                </td>
                                <td class="py-2 pr-4">
                                    <x-filament::badge color="gray">{{ $action['type'] ?? '—' }}</x-filament::badge>
                                </td>
                                <td class="py-2 pr-4">{{ $rule['priority'] }}</td>
                                <td class="py-2 pr-4">
                                    <x-filament::badge :color="$rule['is_active'] ? 'success' : 'gray'">
                                        {{ $rule['is_active'] ? 'active' : 'inactive' }}
                                    </x-filament::badge>
                                </td>
                                <td class="py-2 pr-4">{{ $rule['usage_count'] }}</td>
                                <td class="py-2 pr-4 whitespace-nowrap">
                                    <x-filament::button size="sm" color="gray" wire:click="editRule('{{ $rule['id'] }}')">
                                        Modifier
                                    </x-filament::button>
                                    <x-filament::button
                                        size="sm"
                                        color="danger"
                                        wire:click="deleteRule('{{ $rule['id'] }}')"
                                        wire:confirm="Supprimer la règle « {{ $rule['name'] }} » ?"
                                    >
                                        Supprimer
                                    </x-filament::button>
                                </td>
                            </tr>
                        @endforeach
                    </tbody>
                </table>
            </div>
        @endif
    </x-filament::section>
</x-filament-panels::page>
