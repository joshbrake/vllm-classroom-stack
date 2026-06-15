"""
LiteLLM proxy pre-call hook: attribute traffic to a user AND a session so both
Open WebUI chats and API-key (coding-harness) traffic show up correctly in
Langfuse, grouped by student and by conversation.

Why this is needed:
- LiteLLM sets the Langfuse trace user_id from `metadata.trace_user_id` and the
  session from `metadata.session_id`. Neither is populated automatically for
  virtual-key traffic, and harnesses never send a `user`/session field.
- Open WebUI forwards X-OpenWebUI-User-Email (-> end-user via user_header_mappings)
  and X-OpenWebUI-Chat-Id (the conversation id) as request headers.

This hook fills the gaps: trace_user_id from the key's user_id, and session_id
from the Open WebUI chat id (real conversation threads) or a per-user-per-day
bucket for harness traffic (which has no conversation id).
"""
from datetime import datetime, timezone

from litellm.integrations.custom_logger import CustomLogger


def _header(headers, name):
    if not isinstance(headers, dict):
        return None
    name_l = name.lower()
    for k, v in headers.items():
        if k.lower() == name_l:
            return v
    return None


class InjectUserFromKey(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        user_id = getattr(user_api_key_dict, "user_id", None)
        headers = (data.get("proxy_server_request") or {}).get("headers") or {}
        md = data.get("metadata")
        if not isinstance(md, dict):
            md = {}
            data["metadata"] = md

        # --- user attribution ---
        # Resolve identity with priority: Open WebUI email header (chat traffic)
        # > the virtual key's user_id (harness traffic). Never stamp the master
        # key's owner ("default_user_id"), which would clobber the real student.
        email = _header(headers, "x-openwebui-user-email")
        identity = email or (user_id if user_id and user_id != "default_user_id" else None)
        if identity:
            data["user"] = identity
            md["trace_user_id"] = identity

        # --- session attribution (Langfuse Sessions view) ---
        if not md.get("session_id"):
            chat_id = _header(headers, "x-openwebui-chat-id")
            if chat_id:
                # Open WebUI conversation -> one Langfuse session per chat thread
                md["session_id"] = f"webui-{chat_id}"
            elif user_id:
                # Harness traffic has no conversation id; group a user's harness
                # activity per UTC day so it's still browsable as a session.
                day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                md["session_id"] = f"harness-{user_id}-{day}"
        return data


# Referenced from config.yaml as: litellm_hooks.inject_user_instance
inject_user_instance = InjectUserFromKey()
