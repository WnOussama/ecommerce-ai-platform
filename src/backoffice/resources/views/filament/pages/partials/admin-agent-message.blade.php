@php
    $result = $message['result'] ?? null;
    $error = $message['error'] ?? null;
    $data = $result['data'] ?? [];
    $isAnalyticsShape = array_key_exists('most_requested_products', $data);
@endphp

@if ($error)
    <p class="text-danger-600 dark:text-danger-400">{{ $error }}</p>
@else
    <div class="flex items-center gap-2 flex-wrap">
        @if (!empty($result['action_name']))
            <span class="text-xs font-medium uppercase text-gray-500">{{ $result['action_name'] }}</span>
        @endif
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
        <p class="text-danger-600 dark:text-danger-400">{{ $result['error'] }}</p>
    @endif

    @if (!empty($result['dry_run']))
        <div class="rounded-lg border border-gray-200 dark:border-white/10 p-2 text-xs space-y-1">
            <p class="font-medium uppercase text-gray-500">Simulation</p>
            <p>Éléments affectés : {{ $result['dry_run']['affected_items_count'] ?? 0 }}</p>
            @foreach (($result['dry_run']['warnings'] ?? []) as $warning)
                <p class="text-warning-600 dark:text-warning-400">⚠ {{ $warning }}</p>
            @endforeach
        </div>
    @endif

    @if (($result['status'] ?? null) === 'pending_confirmation' && !empty($result['confirmation_token']))
        <div class="flex gap-2">
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

    @if (!empty($data['analysis']))
        <div class="whitespace-pre-line">{{ $data['analysis'] }}</div>
    @elseif ($isAnalyticsShape)
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            @if (!empty($data['most_requested_products']))
                <div>
                    <p class="text-xs font-medium uppercase text-gray-500 mb-1">Produits demandés</p>
                    <ul class="space-y-0.5">
                        @foreach (array_slice($data['most_requested_products'], 0, 5) as $p)
                            <li>{{ $p['name'] ?? $p['external_id'] }} — {{ $p['request_count'] }}</li>
                        @endforeach
                    </ul>
                </div>
            @endif

            @if (!empty($data['unmet_demand']))
                <div>
                    <p class="text-xs font-medium uppercase text-gray-500 mb-1">Demandes non satisfaites</p>
                    <ul class="space-y-0.5">
                        @foreach (array_slice($data['unmet_demand'], 0, 5) as $u)
                            <li>« {{ $u['message'] }} » — {{ $u['occurrences'] }}×</li>
                        @endforeach
                    </ul>
                </div>
            @endif

            @if (!empty($data['intent_distribution']))
                <div>
                    <p class="text-xs font-medium uppercase text-gray-500 mb-1">Intentions</p>
                    <div class="flex flex-wrap gap-1">
                        @foreach ($data['intent_distribution'] as $intent => $count)
                            <x-filament::badge color="gray">{{ $intent }}: {{ $count }}</x-filament::badge>
                        @endforeach
                    </div>
                </div>
            @endif

            @if (!empty($data['coupon_conversion']))
                <div>
                    <p class="text-xs font-medium uppercase text-gray-500 mb-1">Coupons</p>
                    <p>
                        {{ $data['coupon_conversion']['used'] ?? 0 }}/{{ $data['coupon_conversion']['generated'] ?? 0 }} utilisés
                        ({{ round(($data['coupon_conversion']['conversion_rate'] ?? 0) * 100) }}%)
                    </p>
                </div>
            @endif

            @if (!empty($data['low_stock_products']))
                <div>
                    <p class="text-xs font-medium uppercase text-gray-500 mb-1">Stock faible</p>
                    <ul class="space-y-0.5">
                        @foreach (array_slice($data['low_stock_products'], 0, 5) as $p)
                            <li>{{ $p['name'] }} — {{ $p['quantity'] }}</li>
                        @endforeach
                    </ul>
                </div>
            @endif

            @if (empty($data['most_requested_products']) && empty($data['unmet_demand']) && empty($data['intent_distribution']) && empty($data['low_stock_products']))
                <p class="text-gray-500 sm:col-span-2">Aucune activité sur la période analysée.</p>
            @endif
        </div>
    @elseif (!empty($data['codes']))
        <div>
            <p class="text-xs font-medium uppercase text-gray-500 mb-1">
                Coupons générés ({{ $data['coupons_generated'] ?? count($data['codes']) }})
            </p>
            <div class="flex flex-wrap gap-1">
                @foreach ($data['codes'] as $code)
                    <x-filament::badge color="success">{{ $code }}</x-filament::badge>
                @endforeach
            </div>
            @if (empty($data['codes']))
                <p class="text-gray-500">Aucun coupon généré - vérifiez que des customer_ids ont été fournis.</p>
            @endif
            <p class="text-xs text-gray-500 mt-1">
                {{ $data['discount_percent'] ?? '' }}% · {{ $data['validity_days'] ?? '' }} jours
            </p>
        </div>
    @elseif (!empty($data))
        <pre class="text-xs overflow-x-auto rounded-lg bg-gray-50 dark:bg-white/5 p-2">{{ json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE) }}</pre>
    @endif
@endif
