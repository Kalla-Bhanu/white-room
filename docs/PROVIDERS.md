# PROVIDERS

WHITE ROOM keeps provider secrets local, presence-only, and reloadable.

## Secrets storage

- The app reads secrets in this order:
  - process environment
  - `.env`
  - `secrets.local.json`
  - none
- Settings writes must go to `secrets.local.json`.
- `.env` is read-only for runtime use and is never rewritten by settings flows.
- `core.secrets.reload()` makes updated keys visible without restarting the server.
- Secret values are never rendered in HTML, logs, packet text, or database dumps. Fingerprints are shown instead.

## Provider base URLs

- A custom gateway uses an OpenAI-compatible API base URL, not a dashboard URL.
- The base URL must be normalized with no double `/v1`.
- Groq Cloud uses `https://api.groq.com/openai/v1`.

## Approval and trust

- Live provider lanes stay behind approval gates unless the current plan explicitly enables a safer mode.
- Trusted-session grants are revocable and should be treated as temporary operator convenience, not a bypass.
- The phrase trusted-session grants means temporary, scoped approval windows for provider calls.
- Manual Claude flows remain export/import only.

## Cooldown and fallback

- Rate-limited providers should surface cooldown state and respect `Retry-After` when present.
- When a provider is unavailable, WHITE ROOM should fall back to a quieter lane instead of failing open.

## Cost posture

- Usage and pricing signals are estimates, not bills.
- UI and logs should label them clearly as estimated values.
- Use the label `est-not-bill` anywhere a value is a rough cost estimate rather than a provider bill.

## Key removal

- Removing a key from Settings must delete the local secret entry, reload state, and make the lane disappear or gate immediately.
- The key removal flow must never leave a provider lane active after the secret is gone.

## Manual live checklist

- Custom gateway: save the key in Settings, test the connection, sync models, and confirm an execute turn still pauses behind the approval gate until explicitly approved.
- Groq Cloud: save the key in Settings, test the connection, sync models, and confirm chat/route previews show the Groq lane only when the lane is actually available.
- For both lanes, remove the key afterward and confirm the lane reverts to missing or gated state immediately.
