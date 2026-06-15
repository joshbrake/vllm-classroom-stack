# Operations runbook

> Docker on this host runs as root:docker. The operator's shell may not have the
> `docker` group active in-session; commands below use plain `docker`. If you hit
> `permission denied ... docker.sock`, prefix with `sg docker -c "<command>"` (or
> `sudo`).

## First-time bring-up

The Langfuse stack uses an **external** network that must exist first, or Compose
aborts with "network llmnet ... not found".

```bash
# 1. shared network (once)
docker network create llmnet

# 2. observability stack (separate project; pulls a few GB on first run)
docker compose --env-file .env.langfuse -f docker-compose.langfuse.yml up -d

# 3. serving stack
docker compose up -d
```

Verify:

```bash
docker compose -f docker-compose.langfuse.yml ps      # langfuse-web Up on :3001
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:3001/api/public/health
docker compose ps                                     # litellm Up on :4000
```

## URLs

| URL | What |
|-----|------|
| `http://<host>:3000` | Open WebUI (students) |
| `http://<host>:4000/ui` | LiteLLM Admin UI (keys, users, spend) |
| `http://<host>:3001` | Langfuse (traces, users, sessions) |

Langfuse admin login is seeded from `LANGFUSE_INIT_USER_EMAIL` /
`LANGFUSE_INIT_USER_PASSWORD` in `.env.langfuse` (first boot only).

## Provisioning a student (coding-harness access)

Attribution depends on the key carrying `user_id=<the student's Open WebUI email>`.

```bash
MK=$LITELLM_MASTER_KEY   # from .env
curl -s http://localhost:4000/key/generate \
  -H "Authorization: Bearer $MK" -H "Content-Type: application/json" \
  -d '{"user_id":"jane@hmc.edu","key_alias":"jane","max_budget":20,"metadata":{"cohort":"2026"}}'
```

Hand the returned `key` to the student for their harness (Claude Code, aider, etc.),
pointed at `http://<host>:4000`. Open WebUI needs no per-student key — it forwards
identity via headers.

> Alternative: enable students as `internal_user`s (optionally via SSO) so they
> self-provision keys in the LiteLLM UI; keys auto-inherit their `user_id`.

## Viewing usage

```bash
PK=$LANGFUSE_PUBLIC_KEY; SK=$LANGFUSE_SECRET_KEY     # from .env
# all of a student's traces (full prompt/response)
curl -s -u "$PK:$SK" "http://localhost:3001/api/public/traces?userId=jane@hmc.edu"
# sessions
curl -s -u "$PK:$SK" "http://localhost:3001/api/public/sessions"
```

Or use the Langfuse UI → **Users** / **Sessions**.

## Config knobs

- **Models** — `docker-compose.yml` (vLLM `command`) + `litellm-config.yaml`
  (`model_list`). Add a model to `model_list` to make it routable by name.
- **Langfuse callback** — `litellm-config.yaml` → `litellm_settings.success_callback`.
- **Attribution hook** — `litellm_hooks.py`, registered via
  `litellm_settings.callbacks: litellm_hooks.inject_user_instance` (must be a bare
  `module.instance` string, **not** a list).
- **Open WebUI identity forwarding** — `ENABLE_FORWARD_USER_INFO_HEADERS=True` in
  `docker-compose.yml`.

## Applying config changes

`litellm-config.yaml` and `litellm_hooks.py` are bind-mounted. Editing them does
**not** restart the process, and `docker compose up -d litellm` is a no-op when only
mounted files changed. Force a reload:

```bash
docker restart vllm-litellm-1
# confirm clean start (no Traceback, callbacks initialized):
docker logs vllm-litellm-1 2>&1 | tail -20
```

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `network llmnet ... not found` | Run `docker network create llmnet` before the Langfuse stack |
| LiteLLM Postgres got recreated / lost its DB | Both projects have a `postgres` service. Keep Langfuse under its own project (`name: langfuse` is set); never run both files under one project name |
| Langfuse unreachable from LiteLLM (`connection refused` on `llmnet`) | `langfuse-web` is multi-homed; `HOSTNAME=0.0.0.0` is set so Next.js binds all interfaces. If reintroduced, traces silently fail |
| Trace `userId` is null for harness traffic | Key missing `user_id`, or hook not loaded. Check `docker logs vllm-litellm-1` for the callback init; ensure `callbacks` is a bare string |
| Open WebUI traffic shows `userId=default_user_id` | The master key's owner leaked through; the hook prefers `X-OpenWebUI-User-Email` and ignores `default_user_id` |
| No Langfuse session for Open WebUI chats | Open WebUI image predates PR #15813 (no `X-OpenWebUI-Chat-Id`); `docker compose pull open-webui && docker compose up -d open-webui` |
| Config edit not taking effect | Bind-mounted; `docker restart vllm-litellm-1` |

## Backups

State lives in Docker named volumes. The ones that matter:

- `vllm_litellm-pg` — LiteLLM keys/users/spend
- `langfuse_langfuse_postgres_data` — Langfuse metadata (orgs, projects, users)
- `langfuse_langfuse_clickhouse_data` — the traces themselves
- `langfuse_langfuse_minio_data` — large/blob payloads
- `vllm_open-webui` — Open WebUI accounts + chat history

Back these up with `docker run --rm -v <vol>:/data -v $PWD:/backup alpine tar czf
/backup/<vol>.tgz -C /data .` (stop the stack first for a consistent dump).
