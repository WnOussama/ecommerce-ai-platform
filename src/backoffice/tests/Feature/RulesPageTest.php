<?php

namespace Tests\Feature;

use App\Filament\Pages\Rules;
use Illuminate\Http\Client\Request;
use Illuminate\Support\Facades\Http;
use Livewire\Livewire;
use Tests\TestCase;

class RulesPageTest extends TestCase
{
    private function welcomeRule(): array
    {
        return [
            'id' => 'rule-1',
            'name' => 'Coupon de bienvenue',
            'description' => null,
            'conditions' => ['first_message' => true],
            'action' => [
                'type' => 'generate_coupon',
                'reason' => 'welcome',
                'discount_percent' => 10,
                'validity_days' => 7,
                'max_per_hour' => 25,
            ],
            'priority' => 0,
            'is_active' => true,
            'usage_count' => 3,
        ];
    }

    public function test_list_shows_the_new_condition_types(): void
    {
        Http::fake(['*/rules' => Http::response([
            $this->welcomeRule(),
            array_merge($this->welcomeRule(), [
                'id' => 'rule-2',
                'name' => 'Palier panier',
                'conditions' => ['min_cart_total' => 100],
            ]),
        ])]);

        Livewire::test(Rules::class)
            ->assertOk()
            ->assertSee('premier message')
            ->assertSee('panier ≥ 100 €');
    }

    public function test_editing_a_rule_keeps_conditions_and_action_keys_the_form_does_not_manage(): void
    {
        Http::fake([
            '*/rules/rule-1' => Http::response($this->welcomeRule()),
            '*/rules' => Http::response([$this->welcomeRule()]),
        ]);

        Livewire::test(Rules::class)
            ->call('editRule', 'rule-1')
            ->assertSet('data.first_message', true)
            ->set('data.discount_percent', 12)
            ->call('save');

        Http::assertSent(function (Request $request) {
            if ($request->method() !== 'PUT' || ! str_ends_with($request->url(), '/rules/rule-1')) {
                return false;
            }

            $body = $request->data();

            return $body['conditions'] === ['first_message' => true]
                && $body['action']['discount_percent'] === 12
                && $body['action']['max_per_hour'] === 25
                && $body['action']['reason'] === 'welcome';
        });
    }

    public function test_creating_a_threshold_rule_sends_min_cart_total_and_cooldown(): void
    {
        Http::fake(['*/rules' => Http::response([])]);

        Livewire::test(Rules::class)
            ->set('data.name', 'Palier panier 100 €')
            ->set('data.min_cart_total', '100')
            ->set('data.action_type', 'generate_coupon')
            ->set('data.discount_percent', 15)
            ->set('data.validity_days', 7)
            ->set('data.cooldown_days', '30')
            ->call('save');

        Http::assertSent(function (Request $request) {
            if ($request->method() !== 'POST' || ! str_ends_with($request->url(), '/rules')) {
                return false;
            }

            $body = $request->data();

            return $body['conditions'] === ['min_cart_total' => 100]
                && $body['action']['type'] === 'generate_coupon'
                && $body['action']['discount_percent'] === 15
                && $body['action']['cooldown_days'] === 30;
        });
    }

    public function test_a_blank_cart_total_and_cooldown_are_not_sent(): void
    {
        Http::fake(['*/rules' => Http::response([])]);

        Livewire::test(Rules::class)
            ->set('data.name', 'Bienvenue')
            ->set('data.first_message', true)
            ->set('data.action_type', 'generate_coupon')
            ->set('data.discount_percent', 10)
            ->call('save');

        Http::assertSent(function (Request $request) {
            if ($request->method() !== 'POST') {
                return false;
            }

            $body = $request->data();

            return $body['conditions'] === ['first_message' => true]
                && ! array_key_exists('cooldown_days', $body['action']);
        });
    }
}
