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
        toggle.setAttribute('aria-label', 'Open chat');
        toggle.textContent = '💬';

        var panel = document.createElement('div');
        panel.className = 'ai-assistant-panel';
        panel.innerHTML =
            '<div class="ai-assistant-header">AI Assistant</div>' +
            '<div class="ai-assistant-messages"></div>' +
            '<div class="ai-assistant-input-row">' +
            '<input type="text" placeholder="Type your message..." aria-label="Message" />' +
            '<button type="button">Send</button>' +
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
            return el;
        }

        // Offers a one-click "Apply to cart" button for a coupon action the
        // backend just marked `redeemable` (see
        // controllers/front/chat.php::withRedeemableCoupons() - it only sets
        // this once the code has been materialized into a real PrestaShop
        // CartRule, so this button is never offered for a code that
        // couldn't actually be redeemed).
        function appendCouponActions(actions) {
            if (!Array.isArray(actions)) {
                return;
            }

            actions.forEach(function (action) {
                if (!action || action.type !== 'generate_coupon' || !action.data || !action.data.redeemable) {
                    return;
                }

                var wrapper = document.createElement('div');
                wrapper.className = 'ai-assistant-message bot ai-assistant-coupon';

                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'ai-assistant-coupon-apply';
                btn.textContent = 'Apply ' + action.data.code + ' to my cart';
                btn.addEventListener('click', function () {
                    applyCoupon(action.data.code, btn);
                });

                wrapper.appendChild(btn);
                messagesEl.appendChild(wrapper);
                messagesEl.scrollTop = messagesEl.scrollHeight;
            });
        }

        function applyCoupon(code, btn) {
            btn.disabled = true;
            btn.textContent = 'Applying...';

            fetch(aiAssistantConfig.chatUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ apply_coupon_code: code }),
            })
                .then(function (response) {
                    return response.json().then(function (data) {
                        return { ok: response.ok, data: data };
                    });
                })
                .then(function (result) {
                    if (!result.ok) {
                        var reason =
                            result.data && result.data.error === 'empty_cart'
                                ? 'Add a product to your cart first, then apply the code.'
                                : "Sorry, that code couldn't be applied.";
                        appendMessage(reason, 'bot error');
                        btn.disabled = false;
                        btn.textContent = 'Apply ' + code + ' to my cart';
                        return;
                    }

                    btn.textContent = result.data.already_applied
                        ? 'Already applied'
                        : 'Applied to cart ✓';
                    appendMessage('Discount applied - your cart total has been updated.', 'bot');
                })
                .catch(function () {
                    appendMessage('Connection error. Please try again.', 'error');
                    btn.disabled = false;
                    btn.textContent = 'Apply ' + code + ' to my cart';
                });
        }

        // `navigate_to` is only set by controllers/front/chat.php when the AI
        // found a real product match (see withNavigation() there) - never
        // fabricated client-side. The delay lets the shopper read the
        // assistant's reply before the page changes instead of yanking them
        // away mid-sentence; the notice makes the redirect legible rather
        // than a surprise tab change.
        var NAVIGATE_DELAY_MS = 1800;

        function maybeNavigate(data) {
            if (!data || !data.navigate_to) {
                return;
            }

            appendMessage('Taking you to the product page...', 'bot nav-notice');

            window.setTimeout(function () {
                window.location.href = data.navigate_to;
            }, NAVIGATE_DELAY_MS);
        }

        // The widget's visible transcript only ever lived in the DOM, wiped
        // on every page load/navigation even though the conversation itself
        // survives server-side (see chat.php::proxyConversationHistory()) and
        // its id survives in sessionStorage - so the assistant looked like it
        // forgot the whole conversation every time the shopper got navigated
        // to a product page. Replays the real persisted history instead of
        // re-greeting from scratch.
        function loadHistory(conversationId) {
            return fetch(aiAssistantConfig.chatUrl + '?conversation_id=' + encodeURIComponent(conversationId))
                .then(function (response) {
                    return response.ok ? response.json() : null;
                })
                .then(function (data) {
                    if (data && Array.isArray(data.messages)) {
                        data.messages.forEach(function (m) {
                            appendMessage(m.content, m.role === 'user' ? 'user' : 'bot');
                        });
                    }
                })
                .catch(function () {});
        }

        toggle.addEventListener('click', function () {
            panel.classList.toggle('open');

            if (!panel.classList.contains('open') || messagesEl.children.length > 0) {
                return;
            }

            var conversationId = sessionStorage.getItem(STORAGE_KEY);

            if (!conversationId) {
                appendMessage(aiAssistantConfig.welcomeMessage || 'Hello!', 'bot');
                return;
            }

            loadHistory(conversationId).then(function () {
                if (messagesEl.children.length === 0) {
                    appendMessage(aiAssistantConfig.welcomeMessage || 'Hello!', 'bot');
                }
            });
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
                            'Sorry, the assistant is temporarily unavailable.',
                            'error'
                        );
                        return;
                    }

                    if (result.data.conversation_id) {
                        sessionStorage.setItem(STORAGE_KEY, result.data.conversation_id);
                    }

                    appendMessage(result.data.response || '...', 'bot');
                    appendCouponActions(result.data.actions);
                    maybeNavigate(result.data);
                })
                .catch(function () {
                    appendMessage(
                        'Connection error. Please try again.',
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
