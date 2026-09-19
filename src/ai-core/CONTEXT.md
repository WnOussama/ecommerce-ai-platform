# ai-core

The FastAPI backend that owns Postgres exclusively and serves the chat, rules, admin-agent, insights, analytics and recommendations APIs. Scoped to `src/ai-core` — the Laravel backoffice and platform adapters are separate contexts, not yet documented here.

## Language

**Turn**:
One request/response exchange with the chatbot — the input message plus everything decided in response to it (intent, reply text, actions, suggestions), before any of it is persisted.
_Avoid_: Message (see below), Chat, Request

**Conversation**:
The persisted thread a Turn belongs to — a `conversations` row grouping every Message exchanged with one user session for one tenant.
_Avoid_: Session, Chat, Thread

**Message**:
One persisted row in the `messages` table — either the user's input or the assistant's reply to it, already written to Postgres. A Turn produces the content of (at least) two Messages but is not itself stored.
_Avoid_: Turn, Exchange
