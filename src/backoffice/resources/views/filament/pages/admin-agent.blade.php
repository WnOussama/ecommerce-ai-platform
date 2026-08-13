<x-filament-panels::page>
    <div class="space-y-4">
        <div
            x-data
            x-init="$nextTick(() => $el.scrollTop = $el.scrollHeight)"
            wire:key="admin-agent-transcript-{{ count($messages) }}"
            class="rounded-xl border border-gray-200 dark:border-white/10 bg-white dark:bg-white/5 max-h-[32rem] overflow-y-auto p-4 space-y-4"
        >
            @forelse ($messages as $i => $message)
                @if ($message['role'] === 'user')
                    <div class="flex justify-end">
                        <div class="max-w-[85%] rounded-2xl rounded-tr-sm bg-primary-600 text-white px-4 py-2 text-sm">
                            {{ $message['text'] }}
                        </div>
                    </div>
                @else
                    <div class="flex justify-start" wire:key="agent-message-{{ $i }}">
                        <div class="max-w-[90%] w-full sm:w-auto rounded-2xl rounded-tl-sm bg-gray-100 dark:bg-white/10 px-4 py-3 text-sm space-y-2">
                            @include('filament.pages.partials.admin-agent-message', ['message' => $message])
                        </div>
                    </div>
                @endif
            @empty
                <p class="text-sm text-gray-500 text-center py-12">
                    Posez une question en langage naturel ou choisissez une action pour commencer.
                </p>
            @endforelse
        </div>

        <form wire:submit="sendCommand" class="space-y-4">
            {{ $this->form }}

            <x-filament::button type="submit">
                Envoyer à l'agent IA
            </x-filament::button>
        </form>
    </div>
</x-filament-panels::page>
