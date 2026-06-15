# Classroom LLM Stack

A self-hosted stack for serving an LLM to a class **and** observing how students use
it — every prompt/response is captured, attributed per student, and grouped into
sessions, so usage can be analyzed and summarized.

- **Serving:** vLLM (Qwen3-Coder-30B) behind a LiteLLM proxy
- **Frontends:** Open WebUI (chat) + students' own coding harnesses via API keys
- **Observability:** self-hosted Langfuse (full I/O traces, per-user, per-session)
- **Privacy:** everything runs on-box; student data never leaves the machine

See [operations.md](operations.md) for bring-up, provisioning, and troubleshooting.

## Components

Two Docker Compose **projects** on one host, bridged by a shared external network
`llmnet`:

| Project | Service | Image | Host port | Purpose |
|---------|---------|-------|-----------|---------|
| `vllm` | `vllm-coder` | `nvcr.io/nvidia/vllm:25.09-py3` | `8001` | Serves Qwen3-Coder-30B (OpenAI API) |
| `vllm` | `litellm` | `ghcr.io/berriai/litellm:main-stable` | `4000` | Proxy / gateway / auth / logging |
| `vllm` | `postgres` | `postgres:16` | — (internal) | LiteLLM's DB (keys, spend) |
| `vllm` | `open-webui` | `ghcr.io/open-webui/open-webui:main` | `3000` | Student chat UI |
| `langfuse` | `langfuse-web` | `langfuse/langfuse:3` | `3001` | Observability UI + ingest API |
| `langfuse` | `langfuse-worker` | `langfuse/langfuse-worker:3` | — (localhost) | Async trace ingestion |
| `langfuse` | `postgres` | `postgres:17` | — (localhost) | Langfuse metadata DB |
| `langfuse` | `clickhouse` | `clickhouse/clickhouse-server` | — (localhost) | Langfuse trace store (OLAP) |
| `langfuse` | `redis` | `redis:7` | — (localhost) | Langfuse queue/cache |
| `langfuse` | `minio` | `chainguard/minio` | `9090` | Langfuse S3-compatible blob store |

> The two projects are deliberately separate because **both define a `postgres`
> service** — running them under one project name makes Langfuse's Postgres clobber
> LiteLLM's. The Langfuse compose pins `name: langfuse` to keep them isolated.

## Files

| File | What it is |
|------|------------|
| `docker-compose.yml` | The `vllm` project: vLLM, LiteLLM, its Postgres, Open WebUI |
| `docker-compose.langfuse.yml` | The `langfuse` project (official Langfuse v3 stack, lightly customized) |
| `litellm-config.yaml` | LiteLLM model list, Langfuse callback, user-header mapping, hook registration |
| `litellm_hooks.py` | Pre-call hook that attributes traffic to a **user** and a **session** |
| `.env` | Secrets for the `vllm` project (gitignored) |
| `.env.langfuse` | Secrets for the `langfuse` project (gitignored) |
| `.env.example`, `.env.langfuse.example` | Sanitized templates (committed) |

## Data flow

```
                    ┌─────────────┐
   student chat ───►│ Open WebUI  │──┐  (shared master key +
                    └─────────────┘  │   X-OpenWebUI-User-Email / -Chat-Id headers)
                                     ▼
 coding harness ───────────────►┌─────────┐    success_callback     ┌──────────┐
 (per-student API key)          │ LiteLLM │ ──────────────────────► │ Langfuse │
                                └────┬────┘    (full I/O + user +    └──────────┘
                                     │          session metadata)
                                     ▼
                                ┌─────────┐
                                │  vLLM   │  Qwen3-Coder-30B
                                └─────────┘
```

Both surfaces funnel through LiteLLM, so **Langfuse is the single corpus** of all
student activity.

## How attribution works (the important part)

LiteLLM tags each Langfuse trace from request **metadata**:
`metadata.trace_user_id` → trace `userId`, `metadata.session_id` → Langfuse session.
Neither is set automatically, so `litellm_hooks.py` (an `async_pre_call_hook`)
fills them in:

**User** (unified on the student's email):
- **Open WebUI**: forwards `X-OpenWebUI-User-Email`; the hook (and
  `user_header_mappings`) use it as the identity.
- **Coding harness**: authenticates with a LiteLLM virtual key created with
  `user_id=<email>`; the hook copies that into `metadata.trace_user_id`.
- The hook never stamps the master key's owner (`default_user_id`), which would
  otherwise clobber the real student on Open WebUI traffic.

**Session**:
- **Open WebUI**: `session_id = webui-<X-OpenWebUI-Chat-Id>` → one Langfuse session
  per conversation thread (requires Open WebUI with PR #15813).
- **Coding harness**: no conversation id, so `session_id = harness-<email>-<UTC-date>`
  groups a student's daily harness activity.

Result: in Langfuse you can browse **Users** (all of a student's traffic, both
surfaces) and **Sessions** (full conversations).

## Privacy posture

- The serving + enrichment models are **local** (Qwen via vLLM); raw student
  content stays on-box.
- Langfuse is self-hosted — traces never go to a third party.
- Secrets live only in `.env` / `.env.langfuse` (gitignored). `*.example` templates
  are committed for reproducibility.

## Roadmap (analytics / AI summaries)

Planned on top of this foundation (not yet built):

1. Per-student LiteLLM key provisioning from a roster (optionally via SSO).
2. An **enrichment job**: read Langfuse sessions, classify each with the local model
   into structured dimensions (topics, usage_mode, struggle_signals, reliance_level,
   summary, …), write to a `session_insights` table.
3. Per-student and class-level **rollups** (model choice configurable:
   `ENRICH_MODEL` local-only; `SYNTH_MODEL` swappable over de-identified summaries).
4. An **insight chat** to query the class corpus in natural language.
