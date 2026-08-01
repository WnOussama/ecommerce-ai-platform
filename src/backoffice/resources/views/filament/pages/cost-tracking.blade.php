<x-filament-panels::page>
    <div class="flex justify-end">
        <select
            wire:model.live="timeRange"
            class="fi-select-input block rounded-lg border-gray-300 bg-white text-sm dark:border-white/10 dark:bg-white/5 dark:text-white"
        >
            @foreach (\App\Filament\Pages\CostTracking::TIME_RANGES as $value => $label)
                <option value="{{ $value }}">{{ $label }}</option>
            @endforeach
        </select>
    </div>

    @if ($loadError)
        <x-filament::section>
            <p class="text-sm text-danger-600 dark:text-danger-400">{{ $loadError }}</p>
        </x-filament::section>
    @elseif ($report)
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <x-filament::section>
                <p class="text-sm text-gray-500">Coût total</p>
                <p class="text-2xl font-bold">${{ number_format($report['total_cost_usd'] ?? 0, 4) }}</p>
            </x-filament::section>

            <x-filament::section>
                <p class="text-sm text-gray-500">Coût / conversation</p>
                <p class="text-2xl font-bold">${{ number_format($report['cost_per_conversation_usd'] ?? 0, 4) }}</p>
            </x-filament::section>

            <x-filament::section>
                <p class="text-sm text-gray-500">Latence moyenne</p>
                <p class="text-2xl font-bold">{{ number_format($report['avg_latency_ms'] ?? 0) }} ms</p>
            </x-filament::section>

            <x-filament::section>
                <p class="text-sm text-gray-500">Messages / conversations</p>
                <p class="text-2xl font-bold">{{ $report['total_messages'] ?? 0 }} / {{ $report['conversation_count'] ?? 0 }}</p>
            </x-filament::section>

            <x-filament::section>
                <p class="text-sm text-gray-500">Tokens en entrée</p>
                <p class="text-2xl font-bold">{{ number_format($report['total_tokens_input'] ?? 0) }}</p>
            </x-filament::section>

            <x-filament::section>
                <p class="text-sm text-gray-500">Tokens en sortie</p>
                <p class="text-2xl font-bold">{{ number_format($report['total_tokens_output'] ?? 0) }}</p>
            </x-filament::section>
        </div>

        <x-filament::section heading="Messages les plus coûteux récemment">
            @if (empty($report['recent_expensive_messages']))
                <p class="text-sm text-gray-500">Aucune activité sur cette période.</p>
            @else
                <div class="overflow-x-auto">
                    <table class="w-full text-sm">
                        <thead>
                            <tr class="text-left text-xs uppercase text-gray-500 border-b border-gray-200 dark:border-white/10">
                                <th class="py-2 pr-4">Contenu</th>
                                <th class="py-2 pr-4">Tokens (in/out)</th>
                                <th class="py-2 pr-4">Coût</th>
                                <th class="py-2 pr-4">Latence</th>
                                <th class="py-2 pr-4">Date</th>
                            </tr>
                        </thead>
                        <tbody>
                            @foreach ($report['recent_expensive_messages'] as $message)
                                <tr class="border-b border-gray-100 dark:border-white/5">
                                    <td class="py-2 pr-4 max-w-xs truncate" title="{{ $message['content_preview'] }}">
                                        {{ $message['content_preview'] }}
                                    </td>
                                    <td class="py-2 pr-4 whitespace-nowrap">
                                        {{ number_format($message['tokens_input']) }} / {{ number_format($message['tokens_output']) }}
                                    </td>
                                    <td class="py-2 pr-4 whitespace-nowrap">${{ number_format($message['cost_usd'], 6) }}</td>
                                    <td class="py-2 pr-4 whitespace-nowrap">
                                        {{ $message['latency_ms'] !== null ? $message['latency_ms'] . ' ms' : '—' }}
                                    </td>
                                    <td class="py-2 pr-4 whitespace-nowrap text-gray-500">
                                        {{ \Illuminate\Support\Carbon::parse($message['created_at'])->format('d/m H:i') }}
                                    </td>
                                </tr>
                            @endforeach
                        </tbody>
                    </table>
                </div>
            @endif
        </x-filament::section>
    @endif
</x-filament-panels::page>
