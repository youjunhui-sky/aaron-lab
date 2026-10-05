"""Push abstraction: format an inquiry into channel messages, deliver via webhook.

Channels: feishu (custom bot, preferred) / telegram / slack. All fail -> queue retry.
"""

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("inquiry.push")

PUSH_TIMEOUT_SECONDS = 10.0


@dataclass
class PushTarget:
    channel: str  # "feishu" | "telegram" | "slack"
    webhook: str  # feishu/slack webhook URL; telegram: chat id
    bot_token: str = ""  # telegram only


def build_targets(settings) -> list[PushTarget]:
    """All configured channels, in priority order."""
    targets: list[PushTarget] = []
    if getattr(settings, "feishu_webhook_url", None):
        targets.append(PushTarget(channel="feishu", webhook=settings.feishu_webhook_url))
    if getattr(settings, "telegram_bot_token", "") and getattr(settings, "telegram_chat_id", ""):
        targets.append(
            PushTarget(
                channel="telegram",
                webhook=settings.telegram_chat_id,
                bot_token=settings.telegram_bot_token,
            )
        )
    if getattr(settings, "slack_webhook_url", None):
        targets.append(PushTarget(channel="slack", webhook=settings.slack_webhook_url))
    return targets


def format_message(channel: str, inquiry: dict) -> dict:
    """Channel-specific request body. inquiry: dict with canonical fields."""
    title = f"📩 New inquiry · {inquiry.get('lang') or '-'}"
    lines = [
        title,
        f"name: {inquiry.get('name') or '-'}",
        f"email: {inquiry.get('email')}",
        f"page: {inquiry.get('source_page') or '-'}",
        "",
        inquiry.get("message", ""),
    ]
    text = "\n".join(lines)
    if channel == "feishu":
        return {
            "msg_type": "text",
            "content": {"text": text},
        }
    if channel == "telegram":
        return {"chat_id": inquiry.pop("_chat_id", ""), "text": text}
    if channel == "slack":
        return {"text": text}
    raise ValueError(f"unknown channel: {channel}")


def success(channel: str, status_code: int, body: str) -> bool:
    """Channel-specific success rule."""
    if channel == "telegram":
        return status_code == 200 and '"ok":true' in body.replace(" ", "")
    return status_code in (200, 201, 202, 204)


async def push_one(client: httpx.AsyncClient, target: PushTarget, inquiry: dict) -> bool:
    """Deliver one inquiry to one channel. Returns True only on confirmed success."""
    try:
        if target.channel == "telegram":
            url = f"https://api.telegram.org/bot{target.bot_token}/sendMessage"
            body = format_message("telegram", {**inquiry, "_chat_id": target.webhook})
        else:
            url = target.webhook
            body = format_message(target.channel, inquiry)
        resp = await client.post(url, json=body)
        ok = success(target.channel, resp.status_code, resp.text[:500])
        if not ok:
            log.warning("push %s non-success: %s %s", target.channel, resp.status_code, resp.text[:200])
        return ok
    except Exception:
        log.exception("push %s failed", target.channel)
        return False
