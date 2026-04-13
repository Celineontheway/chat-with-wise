#!/usr/bin/env python3
"""
feishu_api.py — 飞书 API 封装层

支持多 Bot 身份：每个 FeishuClient 实例对应一个飞书应用（app_id/app_secret）。
提供：token 获取/缓存、发送卡片、发送文本、读取群消息历史。
"""

import json
import os
import ssl
import sys
import time
import urllib.request
import urllib.error

FEISHU_API = "https://open.feishu.cn"


def _ssl_context():
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except ImportError:
        pass
    return ctx


_SSL_CTX = _ssl_context()


def _api_call(method, url, headers=None, data=None, timeout=15):
    """Unified HTTP call with error handling."""
    if data is not None and isinstance(data, (dict, list)):
        data = json.dumps(data, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        print(f"ERROR: HTTP {e.code} from {url}: {body}", file=sys.stderr)
        return None
    except urllib.error.URLError as e:
        print(f"ERROR: URL error for {url}: {e}", file=sys.stderr)
        return None


class FeishuClient:
    """One instance per agent bot. Handles token caching and API calls."""

    def __init__(self, app_id: str, app_secret: str):
        self._app_id = app_id
        self._app_secret = app_secret
        self._token = None
        self._token_ts = 0

    def _ensure_token(self) -> str:
        # Cache for 90 minutes (tokens last 2h)
        if self._token and (time.time() - self._token_ts) < 5400:
            return self._token
        url = f"{FEISHU_API}/open-apis/auth/v3/tenant_access_token/internal"
        resp = _api_call("POST", url,
                         headers={"Content-Type": "application/json"},
                         data={"app_id": self._app_id, "app_secret": self._app_secret})
        if not resp or resp.get("code") != 0:
            print(f"ERROR: Failed to get token: {resp}", file=sys.stderr)
            return None
        self._token = resp["tenant_access_token"]
        self._token_ts = time.time()
        return self._token

    def _auth_headers(self):
        token = self._ensure_token()
        if not token:
            return None
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def send_card(self, chat_id: str, card: dict) -> dict:
        """Send interactive card message to a group chat."""
        headers = self._auth_headers()
        if not headers:
            return None
        url = f"{FEISHU_API}/open-apis/im/v1/messages?receive_id_type=chat_id"
        payload = {
            "receive_id": chat_id,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False),
        }
        return _api_call("POST", url, headers=headers, data=payload)

    def send_text(self, chat_id: str, text: str) -> dict:
        """Send plain text message to a group chat."""
        headers = self._auth_headers()
        if not headers:
            return None
        url = f"{FEISHU_API}/open-apis/im/v1/messages?receive_id_type=chat_id"
        payload = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        return _api_call("POST", url, headers=headers, data=payload)

    def get_messages(self, chat_id: str, start_time: str = None,
                     page_size: int = 50) -> list:
        """Read recent messages from a group chat.

        Args:
            chat_id: Group chat ID (oc_xxx)
            start_time: Unix timestamp in seconds (string). Only messages after this time.
            page_size: Number of messages to fetch (max 50).

        Returns:
            List of message dicts with keys: msg_type, sender_id, content, create_time
        """
        headers = self._auth_headers()
        if not headers:
            return []
        url = (f"{FEISHU_API}/open-apis/im/v1/messages"
               f"?container_id_type=chat&container_id={chat_id}"
               f"&page_size={page_size}")
        if start_time:
            url += f"&start_time={start_time}"
        resp = _api_call("GET", url, headers=headers)
        if not resp or resp.get("code") != 0:
            print(f"WARN: Failed to read messages: {resp}", file=sys.stderr)
            return []
        items = resp.get("data", {}).get("items", [])
        results = []
        for item in items:
            sender = item.get("sender", {})
            sender_type = sender.get("sender_type", "")
            sender_id = sender.get("id", "")
            body = item.get("body", {})
            content_str = body.get("content", "{}")
            try:
                content = json.loads(content_str)
            except json.JSONDecodeError:
                content = {"text": content_str}
            results.append({
                "message_id": item.get("message_id", ""),
                "msg_type": item.get("msg_type", ""),
                "sender_type": sender_type,
                "sender_id": sender_id,
                "content": content,
                "create_time": item.get("create_time", ""),
            })
        return results

    def get_bot_info(self) -> dict:
        """Get this bot's info (open_id, etc.) for filtering own messages."""
        headers = self._auth_headers()
        if not headers:
            return {}
        url = f"{FEISHU_API}/open-apis/bot/v3/info"
        resp = _api_call("GET", url, headers=headers)
        if not resp or resp.get("code") != 0:
            return {}
        return resp.get("bot", resp.get("data", {}))


def load_openclaw_feishu():
    """Load default feishu credentials from openclaw config."""
    config_path = os.path.expanduser("~/.openclaw/openclaw.json")
    if not os.path.exists(config_path):
        return None, None
    try:
        with open(config_path) as f:
            config = json.load(f)
        feishu = config.get("channels", {}).get("feishu", {})
        return feishu.get("appId"), feishu.get("appSecret")
    except Exception:
        return None, None
