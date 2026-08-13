<x-filament-panels::page>
    <div class="flex justify-end">
        <select
            wire:model.live="statusFilter"
            class="fi-select-input block rounded-lg border-gray-300 bg-white text-sm dark:border-white/10 dark:bg-white/5 dark:text-white"
        >
            @foreach (\App\Filament\Pages\Conversations::STATUSES as $value => $label)
                <option value="{{ $value }}">{{ $label }}</option>
            @endforeach
        </select>
    </div>

    @if ($selectedConversationId)
        <x-filament::section :heading="'Conversation ' . $selectedConversationId">
            <x-slot name="headerEnd">
                <x-filament::button size="sm" color="gray" wire:click="closeDetail">
                    Fermer
                </x-filament::button>
            </x-slot>

            @if ($historyError)
                <p class="text-sm text-danger-600 dark:text-danger-400">{{ $historyError }}</p>
            @elseif ($selectedHistory)
                <div class="space-y-3 max-h-[28rem] overflow-y-auto">
                    @forelse ($selectedHistory['messages'] ?? [] as $message)
                        <div class="flex {{ $message['role'] === 'user' ? 'justify-end' : 'justify-start' }}">
                            <div
                                @class([
                                    'max-w-[80%] rounded-2xl px-4 py-2 text-sm',
                                    'bg-primary-600 text-white rounded-tr-sm' => $message['role'] === 'user',
                                    'bg-gray-100 dark:bg-white/10 rounded-tl-sm' => $message['role'] !== 'user',
                                ])
                            >
                                <p class="text-xs uppercase opacity-60 mb-1">{{ $message['role'] }}</p>
                                <p>{{ $message['content'] }}</p>
                            </div>
                        </div>
                    @empty
                        <p class="text-sm text-gray-500">Aucun message.</p>
                    @endforelse
                </div>
            @endif
        </x-filament::section>
    @endif

    @if ($loadError)
        <x-filament::section>
            <p class="text-sm text-danger-600 dark:text-danger-400">{{ $loadError }}</p>
        </x-filament::section>
    @else
        <x-filament::section>
            @if (empty($conversations))
                <p class="text-sm text-gray-500">Aucune conversation pour ce tenant.</p>
            @else
                <div class="overflow-x-auto">
                    <table class="w-full text-sm">
                        <thead>
                            <tr class="text-left text-xs uppercase text-gray-500 border-b border-gray-200 dark:border-white/10">
                                <th class="py-2 pr-4">Utilisateur</th>
                                <th class="py-2 pr-4">Statut</th>
                                <th class="py-2 pr-4">Messages</th>
                                <th class="py-2 pr-4">Dernier message</th>
                                <th class="py-2 pr-4">Créée</th>
                                <th class="py-2 pr-4"></th>
                            </tr>
                        </thead>
                        <tbody>
                            @foreach ($conversations as $conversation)
                                <tr class="border-b border-gray-100 dark:border-white/5">
                                    <td class="py-2 pr-4">{{ $conversation['user_identifier'] }}</td>
                                    <td class="py-2 pr-4">
                                        <x-filament::badge :color="match ($conversation['status']) {
                                            'resolved' => 'success',
                                            'escalated' => 'danger',
                                            'abandoned' => 'gray',
                                            default => 'warning',
                                        }">
                                            {{ $conversation['status'] }}
                                        </x-filament::badge>
                                    </td>
                                    <td class="py-2 pr-4">{{ $conversation['message_count'] }}</td>
                                    <td class="py-2 pr-4 whitespace-nowrap text-gray-500">
                                        {{ \Illuminate\Support\Carbon::parse($conversation['last_message_at'])->diffForHumans() }}
                                    </td>
                                    <td class="py-2 pr-4 whitespace-nowrap text-gray-500">
                                        {{ \Illuminate\Support\Carbon::parse($conversation['created_at'])->format('d/m/Y H:i') }}
                                    </td>
                                    <td class="py-2 pr-4">
                                        <x-filament::button
                                            size="sm"
                                            wire:click="viewConversation('{{ $conversation['conversation_id'] }}')"
                                        >
                                            Voir
                                        </x-filament::button>
                                    </td>
                                </tr>
                            @endforeach
                        </tbody>
                    </table>
                </div>

                @if ($this->totalPages > 1)
                    <div class="flex items-center justify-between mt-4 text-sm">
                        <span class="text-gray-500">
                            Page {{ $page }} / {{ $this->totalPages }} ({{ $total }} conversations)
                        </span>
                        <div class="flex gap-2">
                            <x-filament::button
                                size="sm"
                                color="gray"
                                :disabled="$page <= 1"
                                wire:click="goToPage({{ $page - 1 }})"
                            >
                                Précédent
                            </x-filament::button>
                            <x-filament::button
                                size="sm"
                                color="gray"
                                :disabled="$page >= $this->totalPages"
                                wire:click="goToPage({{ $page + 1 }})"
                            >
                                Suivant
                            </x-filament::button>
                        </div>
                    </div>
                @endif
            @endif
        </x-filament::section>
    @endif
</x-filament-panels::page>
