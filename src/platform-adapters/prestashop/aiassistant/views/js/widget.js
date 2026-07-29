/**
 * Widget de chat IA - vanilla JS, sans dépendance de framework pour
 * rester compatible avec n'importe quel thème PrestaShop.
 *
 * N'a accès qu'à `aiAssistantConfig.chatUrl` (le controller front local
 * de ce module) - jamais à l'URL ni à l'identifiant tenant de l'AI Core,
 * qui restent côté serveur (voir controllers/front/chat.php).
 */
(function () {
    'use strict';

    if (typeof aiAssistantConfig === 'undefined') {
        return;
    }

    var STORAGE_KEY = 'ai_assistant_conversation_id';

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function buildWidget() {
        var container = document.getElementById('ai-assistant-widget');
        if (!container) {
            return;
        }

        var toggle = document.createElement('button');
        toggle.className = 'ai-assistant-toggle';
        toggle.setAttribute('aria-label', 'Ouvrir le chat');
        toggle.textContent = '💬';

        var panel = document.createElement('div');
        panel.className = 'ai-assistant-panel';
        panel.innerHTML =
            '<div class="ai-assistant-header">Assistant IA</div>' +
            '<div class="ai-assistant-messages"></div>' +
            '<div class="ai-assistant-input-row">' +
            '<input type="text" placeholder="Écrivez votre message..." aria-label="Message" />' +
            '<button type="button">Envoyer</button>' +
            '</div>';

        container.appendChild(panel);
        container.appendChild(toggle);

        var messagesEl = panel.querySelector('.ai-assistant-messages');
        var input = panel.querySelector('input');
        var sendBtn = panel.querySelector('.ai-assistant-input-row button');

        function appendMessage(text, cssClass) {
            var el = document.createElement('div');
            el.className = 'ai-assistant-message ' + cssClass;
            el.innerHTML = escapeHtml(text);
            messagesEl.appendChild(el);
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }

        toggle.addEventListener('click', function () {
            panel.classList.toggle('open');

            if (panel.classList.contains('open') && messagesEl.children.length === 0) {
                appendMessage(aiAssistantConfig.welcomeMessage || 'Bonjour !', 'bot');
            }
        });

        function sendMessage() {
            var text = input.value.trim();

            if (text === '') {
                return;
            }

            appendMessage(text, 'user');
            input.value = '';
            input.disabled = true;
            sendBtn.disabled = true;

            var conversationId = sessionStorage.getItem(STORAGE_KEY);

            fetch(aiAssistantConfig.chatUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    message: text,
                    conversation_id: conversationId || undefined,
                }),
            })
                .then(function (response) {
                    return response.json().then(function (data) {
                        return { ok: response.ok, data: data };
                    });
                })
                .then(function (result) {
                    if (!result.ok) {
                        appendMessage(
                            "Désolé, l'assistant est momentanément indisponible.",
                            'error'
                        );
                        return;
                    }

                    if (result.data.conversation_id) {
                        sessionStorage.setItem(STORAGE_KEY, result.data.conversation_id);
                    }

                    appendMessage(result.data.response || '...', 'bot');
                })
                .catch(function () {
                    appendMessage(
                        'Erreur de connexion. Veuillez réessayer.',
                        'error'
                    );
                })
                .finally(function () {
                    input.disabled = false;
                    sendBtn.disabled = false;
                    input.focus();
                });
        }

        sendBtn.addEventListener('click', sendMessage);
        input.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') {
                sendMessage();
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', buildWidget);
    } else {
        buildWidget();
    }
})();
