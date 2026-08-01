<x-filament-panels::page>
    <div class="flex items-center justify-between">
        <p class="text-sm text-gray-500">
            Pour une recommandation stratégique en langage naturel à partir de ces mêmes données,
            voir <a href="{{ \App\Filament\Pages\AdminAgent::getUrl() }}" class="text-primary-600 dark:text-primary-400 underline">Agent IA Admin</a>.
        </p>
        <select
            wire:model.live="timeRange"
            class="fi-select-input block rounded-lg border-gray-300 bg-white text-sm dark:border-white/10 dark:bg-white/5 dark:text-white"
        >
            @foreach (\App\Filament\Pages\Insights::TIME_RANGES as $value => $label)
                <option value="{{ $value }}">{{ $label }}</option>
            @endforeach
        </select>
    </div>

    @if ($loadError)
        <x-filament::section>
            <p class="text-sm text-danger-600 dark:text-danger-400">{{ $loadError }}</p>
        </x-filament::section>
    @elseif ($summary)
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <x-filament::section heading="Produits les plus demandés">
                @if (empty($summary['most_requested_products']))
                    <p class="text-sm text-gray-500">Aucune demande produit sur cette période.</p>
                @else
                    <ul class="space-y-1 text-sm">
                        @foreach ($summary['most_requested_products'] as $p)
                            <li class="flex justify-between">
                                <span>{{ $p['name'] ?? $p['external_id'] }}</span>
                                <span class="text-gray-500">{{ $p['request_count'] }} demande(s)</span>
                            </li>
                        @endforeach
                    </ul>
                @endif
            </x-filament::section>

            <x-filament::section heading="Demandes non satisfaites">
                <p class="text-xs text-gray-500 mb-2">Recherches produit où le RAG n'a rien trouvé - opportunités de catalogue.</p>
                @if (empty($summary['unmet_demand']))
                    <p class="text-sm text-gray-500">Aucune recherche infructueuse sur cette période.</p>
                @else
                    <ul class="space-y-1 text-sm">
                        @foreach ($summary['unmet_demand'] as $u)
                            <li class="flex justify-between gap-2">
                                <span class="truncate">« {{ $u['message'] }} »</span>
                                <span class="text-gray-500 whitespace-nowrap">{{ $u['occurrences'] }}×</span>
                            </li>
                        @endforeach
                    </ul>
                @endif
            </x-filament::section>

            <x-filament::section heading="Répartition des intentions">
                @if (empty($summary['intent_distribution']))
                    <p class="text-sm text-gray-500">Aucune activité sur cette période.</p>
                @else
                    <div class="flex flex-wrap gap-1">
                        @foreach ($summary['intent_distribution'] as $intent => $count)
                            <x-filament::badge color="gray">
                                {{ \App\Support\Intents::label($intent) }}: {{ $count }}
                            </x-filament::badge>
                        @endforeach
                    </div>
                @endif
            </x-filament::section>

            <x-filament::section heading="Heures de pointe">
                @php
                    $peakHours = collect($summary['peak_hours'] ?? []);
                    $maxCount = max(1, $peakHours->max('count') ?? 1);
                    $activeHours = $peakHours->where('count', '>', 0);
                @endphp
                @if ($activeHours->isEmpty())
                    <p class="text-sm text-gray-500">Aucune activité sur cette période.</p>
                @else
                    <div class="space-y-1">
                        @foreach ($activeHours as $hour)
                            <div class="flex items-center gap-2 text-xs">
                                <span class="w-10 text-gray-500">{{ str_pad($hour['hour'], 2, '0', STR_PAD_LEFT) }}h</span>
                                <div class="flex-1 bg-gray-100 dark:bg-white/10 rounded-full h-2">
                                    <div
                                        class="bg-primary-500 h-2 rounded-full"
                                        style="width: {{ max(4, ($hour['count'] / $maxCount) * 100) }}%"
                                    ></div>
                                </div>
                                <span class="w-6 text-right text-gray-500">{{ $hour['count'] }}</span>
                            </div>
                        @endforeach
                    </div>
                @endif
            </x-filament::section>

            <x-filament::section heading="Conversion coupons">
                @php $cc = $summary['coupon_conversion'] ?? []; @endphp
                <p class="text-2xl font-bold">
                    {{ $cc['used'] ?? 0 }} / {{ $cc['generated'] ?? 0 }}
                </p>
                <p class="text-sm text-gray-500">
                    utilisés ({{ round(($cc['conversion_rate'] ?? 0) * 100) }}%) ·
                    {{ $cc['generated_by_rule'] ?? 0 }} générés par une règle
                </p>
            </x-filament::section>

            <x-filament::section heading="Stock faible">
                @if (empty($summary['low_stock_products']))
                    <p class="text-sm text-gray-500">Aucun produit en stock faible.</p>
                @else
                    <ul class="space-y-1 text-sm">
                        @foreach ($summary['low_stock_products'] as $p)
                            <li class="flex justify-between">
                                <span>{{ $p['name'] }}</span>
                                <x-filament::badge color="warning">{{ $p['quantity'] }} restant(s)</x-filament::badge>
                            </li>
                        @endforeach
                    </ul>
                @endif
            </x-filament::section>
        </div>
    @endif
</x-filament-panels::page>
