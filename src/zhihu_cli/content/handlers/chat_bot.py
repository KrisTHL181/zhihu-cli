"""LLM-driven auto-reply bot for Zhihu private chat.

Watches the real-time MQTT channel for incoming messages, builds a
conversation context from recent chat history, asks the configured LLM
for a reply (using a user-supplied system prompt), and sends the reply
back on the user's behalf.

This module imports ``aiomqtt`` transitively (via ``imchat.py``), so it
must only be imported lazily — never at module top-level from the CLI
command modules.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any

from zhihu_cli.content.handlers.chat import iter_chat_history, send_text_message
from zhihu_cli.content.handlers.imchat import IMCHAT_TOPIC, ZhihuMessageListener, get_pm_mqtt_topic
from zhihu_cli.output import error

# Token budget for a single generated reply.
_MAX_REPLY_TOKENS = 1000

# Reconnect delay (seconds) after an unexpected MQTT failure.
_RECONNECT_DELAY = 5.0


def run_bot(
    *,
    url_token: str,
    system_prompt: str,
    senders: tuple[str, ...] = (),
    history_limit: int = 20,
    api_base: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    dry_run: bool = False,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Run the auto-reply bot until interrupted (Ctrl+C).

    Resolves the LLM configuration up front (failing fast when the API key
    is missing), then drives the async MQTT watch loop.

    :param url_token: The logged-in user's URL token (MQTT identity).
    :param system_prompt: System prompt controlling the bot's behaviour.
    :param senders: Optional url_tokens or 32-char user hashes to restrict
        replies to.  Empty tuple means reply to every incoming text message.
    :param history_limit: Recent messages to fetch per conversation as LLM
        context (0 = all).
    :param api_base: Optional LLM API base override.
    :param api_key: Optional LLM API key override.
    :param model: Optional LLM model override.
    :param dry_run: Generate replies but do not send them.
    :param on_event: Optional callback invoked with one event dict per
        incoming message (``type`` in ``{"started", "reply", "error"}``).
    """
    if _resolve_llm_config(api_base=api_base, api_key=api_key, model=model) is None:
        return  # error already printed by the resolver

    try:
        asyncio.run(
            _bot_loop(
                url_token=url_token,
                system_prompt=system_prompt,
                senders=senders,
                history_limit=history_limit,
                api_base=api_base,
                api_key=api_key,
                model=model,
                dry_run=dry_run,
                on_event=on_event,
            )
        )
    except KeyboardInterrupt:
        pass  # clean shutdown on Ctrl+C


async def _bot_loop(
    *,
    url_token: str,
    system_prompt: str,
    senders: tuple[str, ...],
    history_limit: int,
    api_base: str | None,
    api_key: str | None,
    model: str | None,
    dry_run: bool,
    on_event: Callable[[dict[str, Any]], None] | None,
) -> None:
    """Async MQTT watch loop — listens forever, reconnecting on failure.

    Incoming messages are processed one at a time via :func:`asyncio.to_thread`
    so the MQTT keepalive stays alive during slow LLM calls.
    """
    sender_hashes = _resolve_senders(senders)

    listener = ZhihuMessageListener(url_token, IMCHAT_TOPIC)
    seen: set[str] = set()

    def emit(event: dict[str, Any]) -> None:
        if on_event:
            on_event(event)

    emit({"type": "started", "senders": sorted(sender_hashes) or ["*"], "dry_run": dry_run})

    while True:
        try:
            async for data in listener.iter_messages():
                event = await asyncio.to_thread(
                    _process_message,
                    data,
                    system_prompt=system_prompt,
                    sender_hashes=sender_hashes,
                    seen=seen,
                    history_limit=history_limit,
                    api_base=api_base,
                    api_key=api_key,
                    model=model,
                    dry_run=dry_run,
                )
                if event:
                    emit(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            emit({"type": "error", "sender": "", "message": "", "reason": f"MQTT listener error: {exc}"})
            await asyncio.sleep(_RECONNECT_DELAY)


def _resolve_senders(senders: tuple[str, ...]) -> set[str]:
    """Resolve url_tokens to 32-char user hashes for sender filtering.

    Values that already look like a 32-char hex hash are kept as-is; anything
    else is treated as a url_token and resolved via the profile page.  Invalid
    values are reported and skipped.

    :param senders: Raw ``--sender`` values.
    :returns: Set of resolved hashes (empty when *senders* is empty).
    """
    hashes: set[str] = set()
    for s in senders:
        if len(s) == 32 and all(c in "0123456789abcdef" for c in s):
            hashes.add(s)
        else:
            try:
                hashes.add(get_pm_mqtt_topic(s))
            except Exception as exc:
                error(f"Could not resolve sender '{s}': {exc}")
    return hashes


def _process_message(
    data: dict[str, Any],
    *,
    system_prompt: str,
    sender_hashes: set[str],
    seen: set[str],
    history_limit: int,
    api_base: str | None,
    api_key: str | None,
    model: str | None,
    dry_run: bool,
) -> dict[str, Any] | None:
    """Turn one incoming MQTT message into an LLM reply event (or skip it).

    Messages are skipped when they are images, risk tips, re-delivered
    duplicates, from a filtered-out sender, or carry no text.  For the rest,
    the recent conversation history is folded into the LLM context and the
    generated reply is sent back (unless *dry_run*).

    :param data: Raw MQTT payload dict.
    :returns: An event dict for the caller to emit, or ``None`` to skip.
    """
    meta = data.get("meta", {}) or {}
    content = data.get("content", {}) or {}
    content_type = meta.get("content_type", "text")
    sender_id = meta.get("sender_id")

    # Dedupe re-delivered messages.
    msg_id = meta.get("id") or data.get("id")
    if msg_id:
        if msg_id in seen:
            return None
        seen.add(msg_id)

    # Only auto-reply to plain text from a known sender.
    if not sender_id or content_type == "image" or content_type == "risk_tip":
        return None
    if sender_hashes and sender_id not in sender_hashes:
        return None

    text = str(content.get("text", "") or "").strip()
    if not text:
        return None

    sender_name = str(sender_id)

    # Build the LLM context from recent history; fall back to the single
    # incoming message alone if history is unavailable.
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    my_name: str | None = None
    try:
        partner_info: list[str] = []
        history = list(iter_chat_history(sender_id, limit=history_limit, partner_info=partner_info))
        if len(partner_info) >= 2:
            sender_name = partner_info[0]
            my_name = partner_info[1]
        for msg in history:
            if msg.get("is_canceled") or msg.get("is_risk_tip"):
                continue
            role = "assistant" if my_name and msg["sender"] == my_name else "user"
            messages.append({"role": role, "content": msg["content"]})
    except Exception:
        pass  # proceed with just the incoming message

    messages.append({"role": "user", "content": text})

    reply = _call_llm(messages, api_base=api_base, api_key=api_key, model=model)
    if not reply:
        return {
            "type": "error",
            "sender": sender_name,
            "message": text,
            "reason": "LLM returned an empty response.",
        }

    if not dry_run:
        try:
            send_text_message(sender_id, reply)
        except Exception as exc:
            return {
                "type": "error",
                "sender": sender_name,
                "message": text,
                "reason": f"Failed to send reply: {exc}",
            }

    return {"type": "reply", "sender": sender_name, "message": text, "reply": reply, "dry_run": dry_run}


def _resolve_llm_config(
    *,
    api_base: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> tuple[str, str, str] | None:
    """Resolve LLM config from args, env, and the cached config file.

    Mirrors ``commands.agora._resolve_llm_config`` — precedence is CLI args,
    then ``LLM_API_BASE`` / ``LLM_API_KEY`` / ``LLM_MODEL`` env vars, then the
    values cached by ``zhihu config llm``.

    :returns: ``(api_base, api_key, model)`` or ``None`` if the API key is missing.
    """
    try:
        from zhihu_cli.extensions.crank.archiver import load_llm_config
    except ImportError:
        error("Cannot import LLM config loader (crank extension not available).")
        return None

    cached = load_llm_config()

    _api_base = api_base or os.environ.get("LLM_API_BASE") or cached.get("api_base", "https://api.openai.com/v1")
    _api_key = api_key or os.environ.get("LLM_API_KEY") or cached.get("api_key", "")
    _model = model or os.environ.get("LLM_MODEL") or cached.get("model", "gpt-4o-mini")

    if not _api_key:
        error(
            "LLM API key not configured. Set it via:\n"
            "  zhihu config llm set --api-base <URL> --api-key <KEY> --model <NAME>\n"
            "Or set the LLM_API_KEY environment variable."
        )
        return None

    return _api_base, _api_key, _model


def _call_llm(
    messages: list[dict[str, str]],
    *,
    api_base: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> str | None:
    """Call the configured LLM chat endpoint and return the reply text.

    :param messages: Full message list (system + conversation turns).
    :returns: The model's reply text (stripped), or ``None`` on failure or
        an empty response.
    """
    resolved = _resolve_llm_config(api_base=api_base, api_key=api_key, model=model)
    if resolved is None:
        return None

    _api_base, _api_key, _model = resolved

    try:
        from openai import OpenAI  # type: ignore[import-untyped]
    except ImportError:
        error("The 'openai' package is required for the chat bot. Install with: pip install openai")
        return None

    client = OpenAI(base_url=_api_base, api_key=_api_key)

    try:
        response = client.chat.completions.create(
            model=_model,
            messages=messages,
            temperature=0.7,
            max_tokens=_MAX_REPLY_TOKENS,
            extra_body={"thinking": {"type": "disabled"}},
        )
        raw = response.choices[0].message.content
        return raw.strip() if raw else None
    except Exception as exc:
        error(f"LLM call failed: {exc}")
        return None
