from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import threading
import time
import uuid

from notes_engine import LINE_WIDTH, LINES_PER_PAGE, now_iso, paginate_text

DEFAULT_ALERT_PAGE_DURATION = 3.2
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_HISTORY_LIMIT = 500
DEFAULT_ACTIVE_ALERT_LIMIT = 100


DEFAULT_STATE = {
    "telegram_alerts": {
        "enabled": False,
        "bot_token": "",
        "bot_username": "",
        "poll_interval_seconds": DEFAULT_POLL_INTERVAL,
        "display_page_duration_seconds": DEFAULT_ALERT_PAGE_DURATION,
        "use_whitelist": False,
        "allowed_users": [],
        "history_limit": DEFAULT_HISTORY_LIMIT,
        "max_active_alerts": DEFAULT_ACTIVE_ALERT_LIMIT,
        "last_update_id": 0,
        "last_error": "",
        "active_alerts": [],
        "history": [],
    }
}


def clamp_int(value, fallback, min_value, max_value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = fallback
    return max(min_value, min(max_value, value))



def clamp_float(value, fallback, min_value, max_value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = fallback
    return max(min_value, min(max_value, value))



def normalize_user_key(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("@"):
        text = text[1:]
    return text.lower()



def sanitize_allowed_users(values) -> list[str]:
    if isinstance(values, str):
        raw_items = values.replace(",", "\n").splitlines()
    elif isinstance(values, list):
        raw_items = values
    else:
        raw_items = []
    result = []
    for item in raw_items:
        normalized = normalize_user_key(item)
        if normalized and normalized not in result:
            result.append(normalized)
    return result



def sanitize_alert(alert: dict | None) -> dict:
    data = alert if isinstance(alert, dict) else {}
    text = str(data.get("text") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    author_name = str(data.get("author_name") or "").strip()
    author_username = normalize_user_key(data.get("author_username"))
    author_id = str(data.get("author_id") or "").strip()
    if not author_name:
        if author_username:
            author_name = f"@{author_username}"
        elif author_id:
            author_name = f"ID {author_id}"
        else:
            author_name = "Telegram"
    return {
        "id": str(data.get("id") or f"alert_{uuid.uuid4().hex[:10]}"),
        "update_id": clamp_int(data.get("update_id"), 0, 0, 2_147_483_647),
        "chat_id": str(data.get("chat_id") or "").strip(),
        "author_id": author_id,
        "author_name": author_name,
        "author_username": author_username,
        "text": text,
        "received_at": str(data.get("received_at") or now_iso()),
        "status": str(data.get("status") or "active").strip().lower(),
        "raw_message": deepcopy(data.get("raw_message") or {}),
    }



def sanitize_history_entry(entry: dict | None) -> dict:
    data = entry if isinstance(entry, dict) else {}
    alert = sanitize_alert(data.get("alert") or data)
    return {
        "id": str(data.get("id") or f"history_{uuid.uuid4().hex[:10]}"),
        "action": str(data.get("action") or "received").strip().lower(),
        "created_at": str(data.get("created_at") or alert.get("received_at") or now_iso()),
        "alert": alert,
    }



def sanitize_alerts_state(section: dict | None) -> dict:
    raw = section if isinstance(section, dict) else {}
    active_alerts = [sanitize_alert(item) for item in raw.get("active_alerts", []) if isinstance(item, dict)]
    history = [sanitize_history_entry(item) for item in raw.get("history", []) if isinstance(item, dict)]
    history_limit = clamp_int(raw.get("history_limit"), DEFAULT_HISTORY_LIMIT, 10, 5000)
    max_active_alerts = clamp_int(raw.get("max_active_alerts"), DEFAULT_ACTIVE_ALERT_LIMIT, 1, 500)
    active_alerts = active_alerts[:max_active_alerts]
    history = history[:history_limit]
    return {
        "enabled": bool(raw.get("enabled", False)),
        "bot_token": str(raw.get("bot_token") or "").strip(),
        "bot_username": str(raw.get("bot_username") or "").strip(),
        "poll_interval_seconds": clamp_float(raw.get("poll_interval_seconds"), DEFAULT_POLL_INTERVAL, 1.0, 60.0),
        "display_page_duration_seconds": clamp_float(raw.get("display_page_duration_seconds"), DEFAULT_ALERT_PAGE_DURATION, 0.5, 30.0),
        "use_whitelist": bool(raw.get("use_whitelist", False)),
        "allowed_users": sanitize_allowed_users(raw.get("allowed_users")),
        "history_limit": history_limit,
        "max_active_alerts": max_active_alerts,
        "last_update_id": clamp_int(raw.get("last_update_id"), 0, 0, 2_147_483_647),
        "last_error": str(raw.get("last_error") or "").strip(),
        "active_alerts": active_alerts,
        "history": history,
    }



def default_config_fragment() -> dict:
    return deepcopy(DEFAULT_STATE)



def build_alert_display_text(alert: dict) -> str:
    author = str(alert.get("author_name") or "Telegram").strip() or "Telegram"
    text = str(alert.get("text") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if text:
        return f"{author}\n{text}"
    return author



def build_author_label(message: dict) -> tuple[str, str, str]:
    sender = message.get("from") or {}
    username = normalize_user_key(sender.get("username"))
    author_id = str(sender.get("id") or "").strip()
    first_name = str(sender.get("first_name") or "").strip()
    last_name = str(sender.get("last_name") or "").strip()
    full_name = " ".join(part for part in (first_name, last_name) if part).strip()
    if username:
        author_name = f"@{username}"
    elif full_name:
        author_name = full_name
    elif author_id:
        author_name = f"ID {author_id}"
    else:
        author_name = "Telegram"
    return author_name, username, author_id



def build_alert_from_telegram_message(message: dict, update_id: int = 0) -> dict | None:
    if not isinstance(message, dict):
        return None
    text = message.get("text")
    if text is None:
        text = message.get("caption")
    if text is None:
        return None
    text = str(text).replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not text.strip():
        return None
    author_name, username, author_id = build_author_label(message)
    chat = message.get("chat") or {}
    return sanitize_alert({
        "update_id": update_id,
        "chat_id": str(chat.get("id") or "").strip(),
        "author_id": author_id,
        "author_name": author_name,
        "author_username": username,
        "text": text,
        "received_at": now_iso(),
        "status": "active",
        "raw_message": deepcopy(message),
    })


@dataclass
class AlertDisplayItem:
    alert_id: str
    line1: str
    line2: str
    title: str
    page_index: int
    page_count: int
    source: dict


class TelegramAlertManager:
    def __init__(self, config_getter, config_saver, width_fn=None, width: int = LINE_WIDTH):
        self._config_getter = config_getter
        self._config_saver = config_saver
        self.width_fn = width_fn or len
        self.width = width
        self.lock = threading.RLock()
        self.enabled = False
        self.bot_token = ""
        self.bot_username = ""
        self.poll_interval = DEFAULT_POLL_INTERVAL
        self.display_page_duration = DEFAULT_ALERT_PAGE_DURATION
        self.use_whitelist = False
        self.allowed_users: list[str] = []
        self.history_limit = DEFAULT_HISTORY_LIMIT
        self.max_active_alerts = DEFAULT_ACTIVE_ALERT_LIMIT
        self.last_update_id = 0
        self.last_error = ""
        self.active_alerts: list[dict] = []
        self.history: list[dict] = []
        self.alert_cursor = 0
        self.active_alert_id = ""
        self.active_started_at = 0.0
        self.load_from_config()

    def _load_section(self) -> dict:
        cfg = self._config_getter() or {}
        return sanitize_alerts_state(cfg.get("telegram_alerts"))

    def load_from_config(self):
        with self.lock:
            section = self._load_section()
            self.enabled = section["enabled"]
            self.bot_token = section["bot_token"]
            self.bot_username = section["bot_username"]
            self.poll_interval = section["poll_interval_seconds"]
            self.display_page_duration = section["display_page_duration_seconds"]
            self.use_whitelist = section["use_whitelist"]
            self.allowed_users = section["allowed_users"]
            self.history_limit = section["history_limit"]
            self.max_active_alerts = section["max_active_alerts"]
            self.last_update_id = section["last_update_id"]
            self.last_error = section["last_error"]
            self.active_alerts = section["active_alerts"]
            self.history = section["history"]
            self._normalize_runtime_state()

    def _normalize_runtime_state(self):
        if self.alert_cursor >= len(self.active_alerts):
            self.alert_cursor = 0
        if self.active_alert_id and not any(item["id"] == self.active_alert_id for item in self.active_alerts):
            self.active_alert_id = ""
            self.active_started_at = 0.0

    def _serialize_state(self) -> dict:
        return sanitize_alerts_state({
            "enabled": self.enabled,
            "bot_token": self.bot_token,
            "bot_username": self.bot_username,
            "poll_interval_seconds": self.poll_interval,
            "display_page_duration_seconds": self.display_page_duration,
            "use_whitelist": self.use_whitelist,
            "allowed_users": self.allowed_users,
            "history_limit": self.history_limit,
            "max_active_alerts": self.max_active_alerts,
            "last_update_id": self.last_update_id,
            "last_error": self.last_error,
            "active_alerts": self.active_alerts,
            "history": self.history,
        })

    def save_to_config(self):
        with self.lock:
            cfg = self._config_getter() or {}
            cfg["telegram_alerts"] = self._serialize_state()
            self._config_saver(cfg)

    def get_state_snapshot(self) -> dict:
        with self.lock:
            return deepcopy(self._serialize_state())

    def is_configured(self) -> bool:
        with self.lock:
            return bool(self.bot_token)

    def is_sender_allowed(self, username: str = "", user_id: str = "") -> bool:
        with self.lock:
            if not self.use_whitelist:
                return True
            normalized_username = normalize_user_key(username)
            normalized_id = normalize_user_key(user_id)
            allowed = set(self.allowed_users)
            return bool(normalized_username and normalized_username in allowed) or bool(normalized_id and normalized_id in allowed)

    def update_settings(self, *, enabled: bool | None = None, bot_token: str | None = None, bot_username: str | None = None, use_whitelist: bool | None = None, allowed_users=None, poll_interval=None, display_page_duration=None):
        with self.lock:
            if enabled is not None:
                self.enabled = bool(enabled)
            if bot_token is not None:
                self.bot_token = str(bot_token).strip()
            if bot_username is not None:
                self.bot_username = str(bot_username).strip()
            if use_whitelist is not None:
                self.use_whitelist = bool(use_whitelist)
            if allowed_users is not None:
                self.allowed_users = sanitize_allowed_users(allowed_users)
            if poll_interval is not None:
                self.poll_interval = clamp_float(poll_interval, self.poll_interval, 1.0, 60.0)
            if display_page_duration is not None:
                self.display_page_duration = clamp_float(display_page_duration, self.display_page_duration, 0.5, 30.0)
            self.save_to_config()

    def set_enabled(self, enabled: bool):
        self.update_settings(enabled=enabled)

    def set_last_error(self, message: str):
        with self.lock:
            normalized = str(message or "").strip()
            if normalized == self.last_error:
                return
            self.last_error = normalized
            self.save_to_config()

    def clear_last_error(self):
        self.set_last_error("")

    def set_last_update_id(self, update_id: int):
        with self.lock:
            self.last_update_id = clamp_int(update_id, self.last_update_id, 0, 2_147_483_647)
            self.save_to_config()

    def set_bot_username(self, username: str):
        with self.lock:
            username = str(username or "").strip()
            if username == self.bot_username:
                return
            self.bot_username = username
            self.save_to_config()

    def _push_history(self, alert: dict, action: str = "received"):
        self.history.insert(0, sanitize_history_entry({
            "alert": alert,
            "action": action,
            "created_at": now_iso(),
        }))
        self.history = self.history[:self.history_limit]

    def enqueue_alert(self, alert: dict) -> dict:
        with self.lock:
            normalized = sanitize_alert(alert)
            self.active_alerts.insert(0, normalized)
            self.active_alerts = self.active_alerts[:self.max_active_alerts]
            self._push_history(normalized, "received")
            self.active_alert_id = normalized["id"]
            self.alert_cursor = 0
            self.active_started_at = time.time()
            self.save_to_config()
            return deepcopy(normalized)

    def get_active_alerts(self) -> list[dict]:
        with self.lock:
            return deepcopy(self.active_alerts)

    def get_history(self) -> list[dict]:
        with self.lock:
            return deepcopy(self.history)

    def _find_active_index(self, alert_id: str) -> int:
        for index, item in enumerate(self.active_alerts):
            if item["id"] == alert_id:
                return index
        return -1

    def _set_active_alert(self, index: int):
        if not self.active_alerts:
            self.alert_cursor = 0
            self.active_alert_id = ""
            self.active_started_at = 0.0
            return
        self.alert_cursor = index % len(self.active_alerts)
        selected = self.active_alerts[self.alert_cursor]
        if self.active_alert_id != selected["id"]:
            self.active_alert_id = selected["id"]
            self.active_started_at = time.time()

    def manual_cycle(self, direction: int) -> dict | None:
        with self.lock:
            if not self.active_alerts:
                return None
            self._set_active_alert((self.alert_cursor + direction) % len(self.active_alerts))
            return deepcopy(self.active_alerts[self.alert_cursor])

    def dismiss_alert(self, alert_id: str, action: str = "done"):
        with self.lock:
            index = self._find_active_index(alert_id)
            if index < 0:
                raise ValueError("Алерт не найден.")
            alert = self.active_alerts.pop(index)
            self._push_history(alert, action)
            if not self.active_alerts:
                self.alert_cursor = 0
                self.active_alert_id = ""
                self.active_started_at = 0.0
            else:
                self._set_active_alert(min(index, len(self.active_alerts) - 1))
            self.save_to_config()

    def get_current_display(self) -> AlertDisplayItem | None:
        with self.lock:
            if not self.active_alerts:
                self.active_alert_id = ""
                self.active_started_at = 0.0
                return None
            current_index = self._find_active_index(self.active_alert_id)
            if current_index < 0:
                current_index = self.alert_cursor if self.alert_cursor < len(self.active_alerts) else 0
                self._set_active_alert(current_index)
            current = self.active_alerts[current_index]
            pages = paginate_text(build_alert_display_text(current), width=self.width, lines_per_page=LINES_PER_PAGE, width_fn=self.width_fn)
            if not pages:
                return None
            elapsed = max(0.0, time.time() - self.active_started_at)
            absolute_page = int(elapsed // self.display_page_duration) if self.display_page_duration > 0 else 0
            if absolute_page >= len(pages):
                next_index = (current_index + 1) % len(self.active_alerts)
                self._set_active_alert(next_index)
                current = self.active_alerts[self.alert_cursor]
                pages = paginate_text(build_alert_display_text(current), width=self.width, lines_per_page=LINES_PER_PAGE, width_fn=self.width_fn)
                absolute_page = 0
            line1, line2 = pages[absolute_page]
            return AlertDisplayItem(
                alert_id=current["id"],
                line1=line1,
                line2=line2,
                title=current.get("author_name") or "Telegram",
                page_index=absolute_page,
                page_count=len(pages),
                source=deepcopy(current),
            )
