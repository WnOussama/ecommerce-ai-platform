<?php

namespace Tests\Feature;

use App\Filament\Pages\AdminAgent;
use Illuminate\Support\Facades\Http;
use Livewire\Livewire;
use Tests\TestCase;

class AdminAgentPageTest extends TestCase
{
    public function test_sending_a_command_appends_to_the_transcript_not_replaces_it(): void
    {
        Http::fake([
            '*/admin/command' => Http::response([
                'action_id' => 'a1',
                'action_name' => 'get_analytics',
                'status' => 'completed',
                'success' => true,
                'data' => ['most_requested_products' => []],
            ]),
        ]);

        $component = Livewire::test(AdminAgent::class)
            ->set('data.command', 'Analyse les demandes clients')
            ->call('sendCommand')
            ->assertSet('data.command', null);

        $messages = $component->get('messages');
        $this->assertCount(2, $messages);
        $this->assertSame('user', $messages[0]['role']);
        $this->assertSame('Analyse les demandes clients', $messages[0]['text']);
        $this->assertSame('agent', $messages[1]['role']);
        $this->assertSame('completed', $messages[1]['result']['status']);

        // A second turn must be appended, not overwrite the first.
        Http::fake([
            '*/admin/command' => Http::response([
                'action_id' => 'a2',
                'action_name' => 'get_analytics',
                'status' => 'completed',
                'success' => true,
                'data' => [],
            ]),
        ]);

        $component->set('data.command', 'Autre question')->call('sendCommand');

        $this->assertCount(4, $component->get('messages'));
    }

    public function test_confirming_a_pending_action_appends_a_new_turn(): void
    {
        Http::fake([
            '*/admin/command' => Http::response([
                'action_id' => 'a1',
                'action_name' => 'suggest_marketing_strategy',
                'status' => 'pending_confirmation',
                'success' => true,
                'requires_confirmation' => true,
                'confirmation_token' => 'tok_1',
                'data' => [],
            ]),
        ]);

        $component = Livewire::test(AdminAgent::class)
            ->set('data.action_name', 'suggest_marketing_strategy')
            ->call('sendCommand');

        $this->assertCount(2, $component->get('messages'));

        Http::fake([
            '*/admin/confirm' => Http::response([
                'action_id' => 'a1',
                'action_name' => 'suggest_marketing_strategy',
                'status' => 'completed',
                'success' => true,
                'data' => ['analysis' => 'Faites une promotion.'],
            ]),
        ]);

        $component->call('confirmAction', 'a1', 'tok_1');

        $messages = $component->get('messages');
        $this->assertCount(4, $messages);
        $this->assertSame('→ Confirmer', $messages[2]['text']);
        $this->assertSame('completed', $messages[3]['result']['status']);
    }

    public function test_command_failure_appends_an_error_message_not_a_crash(): void
    {
        Http::fake([
            '*/admin/command' => Http::response('Server error', 500),
        ]);

        $component = Livewire::test(AdminAgent::class)
            ->set('data.command', 'test')
            ->call('sendCommand');

        $messages = $component->get('messages');
        $this->assertCount(2, $messages);
        $this->assertArrayHasKey('error', $messages[1]);
    }
}
