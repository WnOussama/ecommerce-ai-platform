<?php

namespace Tests\Feature;

use App\Filament\Pages\Conversations;
use Illuminate\Support\Facades\Http;
use Livewire\Livewire;
use Tests\TestCase;

class ConversationsPageTest extends TestCase
{
    public function test_renders_real_conversation_list(): void
    {
        Http::fake([
            '*/chat/conversations*' => Http::response([
                'conversations' => [
                    [
                        'conversation_id' => 'c1',
                        'user_identifier' => 'anon_abc123',
                        'status' => 'active',
                        'message_count' => 4,
                        'last_message_at' => now()->toIso8601String(),
                        'created_at' => now()->toIso8601String(),
                    ],
                ],
                'total' => 1,
                'limit' => 20,
                'offset' => 0,
            ]),
        ]);

        Livewire::test(Conversations::class)
            ->assertOk()
            ->assertSee('anon_abc123')
            ->assertSee('4');
    }

    public function test_viewing_a_conversation_loads_its_history(): void
    {
        Http::fake([
            '*/chat/conversations*' => Http::response(['conversations' => [], 'total' => 0]),
            '*/chat/history/c1*' => Http::response([
                'conversation_id' => 'c1',
                'messages' => [
                    ['id' => 'm1', 'role' => 'user', 'content' => 'Bonjour', 'created_at' => now()->toIso8601String()],
                    ['id' => 'm2', 'role' => 'assistant', 'content' => 'Salut !', 'created_at' => now()->toIso8601String()],
                ],
                'started_at' => now()->toIso8601String(),
                'last_message_at' => now()->toIso8601String(),
                'status' => 'active',
            ]),
        ]);

        Livewire::test(Conversations::class)
            ->call('viewConversation', 'c1')
            ->assertSet('selectedConversationId', 'c1')
            ->assertSee('Bonjour')
            ->assertSee('Salut !');
    }

    public function test_closing_detail_clears_selection(): void
    {
        Http::fake([
            '*/chat/conversations*' => Http::response(['conversations' => [], 'total' => 0]),
            '*/chat/history/c1*' => Http::response([
                'conversation_id' => 'c1',
                'messages' => [],
                'started_at' => now()->toIso8601String(),
                'last_message_at' => now()->toIso8601String(),
                'status' => 'active',
            ]),
        ]);

        Livewire::test(Conversations::class)
            ->call('viewConversation', 'c1')
            ->assertSet('selectedConversationId', 'c1')
            ->call('closeDetail')
            ->assertSet('selectedConversationId', null);
    }

    public function test_degrades_gracefully_when_ai_core_is_unreachable(): void
    {
        Http::fake([
            '*/chat/conversations*' => Http::response('', 500),
        ]);

        Livewire::test(Conversations::class)
            ->assertOk()
            ->assertSet('conversations', []);
    }
}
