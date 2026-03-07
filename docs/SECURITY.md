# WaggleDance Security

*Turvallisuus — Threat model and mitigations*

## Threat Model

WaggleDance is designed as a **localhost-only, single-user** system. It is NOT designed for:
- Multi-tenant deployment
- Public internet exposure
- Handling sensitive/regulated data

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| **No authentication** | Single-user, localhost-only. Adding auth would add complexity without benefit for the target use case. |
| **No TLS** | Localhost traffic only. Use a reverse proxy (nginx, Caddy) if exposing to network. |
| **No multi-tenant isolation** | All agents share one memory space by design (collaborative learning). |

---

## Implemented Mitigations

### Input Validation (H1)
- Chat messages: max 10,000 characters (Pydantic `Field(max_length=)`)
- Voice text: max 5,000 characters
- Voice audio: max 10MB base64
- Path parameters: max 128-256 characters
- Settings toggle: Pydantic model validation

### Rate Limiting (H2)
- `/api/chat`: 20 requests/min per IP (in-process token bucket)
- No external dependencies required

### CORS (H3)
- Methods restricted to `GET` and `POST`
- Headers restricted to `Content-Type`
- Credentials disabled
- Origins configurable via `CORS_ORIGINS` env var

### Error Handling (H4)
- All API errors return generic `{"error": "Internal error"}`
- Stack traces logged server-side only, never leaked to clients

### SQL Injection Prevention
- All SQL queries use parameterized statements
- LIKE patterns escape `%` and `_` wildcards

### Atomic Writes
- `cognitive_graph.json`: temp file + `os.replace()` + fsync
- `settings.yaml`: temp file + `os.replace()` to prevent corruption
- `replay_store.jsonl`: per-line try/except for corruption resilience

---

## Prompt Injection

WaggleDance uses local LLMs (no external API calls), which limits the attack surface. Additional mitigations:

- **Round Table consensus**: Up to 6 agents cross-validate answers, making single-agent manipulation harder
- **Hallucination detection**: Contrastive + keyword detection flags suspicious outputs
- **Agent trust levels**: New agents start as NOVICE with memory-only access; LLM access requires earning trust through correct responses
- **Seasonal Guard**: Rejects out-of-season claims regardless of LLM output

---

## Secrets Management

All secrets are stored in `.env` (git-ignored, never committed):

```env
GITHUB_PAT=               # GitHub access
WAGGLEDANCE_TELEGRAM_BOT_TOKEN=  # Telegram alerts
WAGGLEDANCE_TELEGRAM_CHAT_ID=    # Telegram chat
WAGGLEDANCE_HA_TOKEN=            # Home Assistant
WAGGLEDANCE_DISTILLATION_API_KEY= # Distillation (disabled)
```

Secrets are read via `os.environ.get()` with env var fallback in all integration modules.

---

## Recommendations for Network Deployment

If you need to expose WaggleDance beyond localhost:

1. Put it behind a reverse proxy (nginx, Caddy) with TLS
2. Add HTTP Basic Auth or OAuth2 at the proxy level
3. Restrict `CORS_ORIGINS` to your domain
4. Consider network-level firewall rules
5. Monitor `/health` and `/ready` endpoints externally
