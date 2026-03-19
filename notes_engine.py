from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
import threading
import time
import uuid

LINE_WIDTH = 20
LINES_PER_PAGE = 2
MAX_ACTIVE_NOTES = 10
MAX_ACTIVE_REMINDERS = 50
MAX_HISTORY_ITEMS = 200
DEFAULT_NOTE_INTERVAL = 30
DEFAULT_PAGE_DURATION = 2.4
DEFAULT_ALERT_PAGE_DURATION = 3.2
WEEKDAY_LABELS = [
    "Пн",
    "Вт",
    "Ср",
    "Чт",
    "Пт",
    "Сб",
    "Вс",
]


DEFAULT_STATE = {
    "notes_reminders": {
        "settings": {
            "limits": {
                "max_active_notes": MAX_ACTIVE_NOTES,
                "max_active_reminders": MAX_ACTIVE_REMINDERS,
                "max_history_items": MAX_HISTORY_ITEMS,
            },
            "display": {
                "note_page_duration_seconds": DEFAULT_PAGE_DURATION,
                "reminder_page_duration_seconds": DEFAULT_ALERT_PAGE_DURATION,
            },
            "defaults": {
                "note_interval_seconds": DEFAULT_NOTE_INTERVAL,
            },
        },
        "notes": [],
        "reminders": [],
        "history": [],
        "ui": {
            "selected_tab": "notes",
        },
    }
}


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def parse_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def normalize_time_value(value: str | None, fallback: str = "09:00") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        parsed = datetime.strptime(text, "%H:%M")
        return parsed.strftime("%H:%M")
    except ValueError:
        return fallback


def normalize_date_value(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = parse_date(text)
    return parsed.isoformat() if parsed else ""


def normalize_weekdays(values) -> list[int]:
    result = []
    if not isinstance(values, list):
        return result
    for item in values:
        try:
            day = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6 and day not in result:
            result.append(day)
    return sorted(result)


def normalize_repeat_mode(value: str | None) -> str:
    mode = str(value or "once").strip().lower()
    if mode not in {"once", "daily", "weekly"}:
        return "once"
    return mode


def normalize_status(value: str | None, default: str = "active") -> str:
    status = str(value or default).strip().lower()
    if status not in {"active", "hidden", "done", "archived", "deleted"}:
        return default
    return status


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def sanitize_settings(settings: dict | None) -> dict:
    raw = settings if isinstance(settings, dict) else {}
    limits = raw.get("limits", {}) if isinstance(raw.get("limits"), dict) else {}
    display = raw.get("display", {}) if isinstance(raw.get("display"), dict) else {}
    defaults = raw.get("defaults", {}) if isinstance(raw.get("defaults"), dict) else {}

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

    return {
        "limits": {
            "max_active_notes": clamp_int(limits.get("max_active_notes"), MAX_ACTIVE_NOTES, 1, 100),
            "max_active_reminders": clamp_int(limits.get("max_active_reminders"), MAX_ACTIVE_REMINDERS, 1, 500),
            "max_history_items": clamp_int(limits.get("max_history_items"), MAX_HISTORY_ITEMS, 10, 2000),
        },
        "display": {
            "note_page_duration_seconds": clamp_float(display.get("note_page_duration_seconds"), DEFAULT_PAGE_DURATION, 0.5, 30.0),
            "reminder_page_duration_seconds": clamp_float(display.get("reminder_page_duration_seconds"), DEFAULT_ALERT_PAGE_DURATION, 0.5, 30.0),
        },
        "defaults": {
            "note_interval_seconds": clamp_int(defaults.get("note_interval_seconds"), DEFAULT_NOTE_INTERVAL, 5, 86400),
        },
    }


def sanitize_note(note: dict | None) -> dict:
    data = note if isinstance(note, dict) else {}
    interval_seconds = data.get("interval_seconds", DEFAULT_NOTE_INTERVAL)
    try:
        interval_seconds = int(interval_seconds)
    except (TypeError, ValueError):
        interval_seconds = DEFAULT_NOTE_INTERVAL
    interval_seconds = max(5, min(86400, interval_seconds))

    text = str(data.get("text", "")).strip()
    return {
        "id": str(data.get("id") or new_id("note")),
        "text": text,
        "enabled": bool(data.get("enabled", True)),
        "interval_seconds": interval_seconds,
        "status": normalize_status(data.get("status"), "active"),
        "created_at": str(data.get("created_at") or now_iso()),
        "updated_at": str(data.get("updated_at") or now_iso()),
        "last_shown_at": str(data.get("last_shown_at") or ""),
        "sort_order": int(data.get("sort_order") or 0),
    }



def sanitize_reminder(reminder: dict | None) -> dict:
    data = reminder if isinstance(reminder, dict) else {}
    title = str(data.get("title", "")).strip()
    text = str(data.get("text", "")).strip()
    reminder_type = str(data.get("reminder_type") or "once").strip().lower()
    if reminder_type not in {"once", "date_time", "daily", "weekly"}:
        reminder_type = "once"
    repeat_mode = normalize_repeat_mode(data.get("repeat_mode") or ("weekly" if reminder_type == "weekly" else "daily" if reminder_type == "daily" else "once"))
    return {
        "id": str(data.get("id") or new_id("reminder")),
        "title": title,
        "text": text,
        "enabled": bool(data.get("enabled", True)),
        "status": normalize_status(data.get("status"), "active"),
        "reminder_type": reminder_type,
        "repeat_mode": repeat_mode,
        "date": normalize_date_value(data.get("date")),
        "time": normalize_time_value(data.get("time"), "09:00"),
        "weekdays": normalize_weekdays(data.get("weekdays")),
        "last_trigger_key": str(data.get("last_trigger_key") or ""),
        "triggered_at": str(data.get("triggered_at") or ""),
        "created_at": str(data.get("created_at") or now_iso()),
        "updated_at": str(data.get("updated_at") or now_iso()),
        "sort_order": int(data.get("sort_order") or 0),
    }



def sanitize_history_entry(entry: dict | None) -> dict:
    data = entry if isinstance(entry, dict) else {}
    return {
        "id": str(data.get("id") or new_id("history")),
        "source_type": str(data.get("source_type") or "note"),
        "source_id": str(data.get("source_id") or ""),
        "title": str(data.get("title") or "").strip(),
        "text": str(data.get("text") or "").strip(),
        "action": str(data.get("action") or "archived").strip().lower(),
        "created_at": str(data.get("created_at") or now_iso()),
        "payload": deepcopy(data.get("payload") or {}),
    }



def sanitize_notes_reminders_state(section: dict | None) -> dict:
    raw = section if isinstance(section, dict) else {}
    settings = sanitize_settings(raw.get("settings"))
    notes = [sanitize_note(item) for item in raw.get("notes", []) if isinstance(item, dict)]
    reminders = [sanitize_reminder(item) for item in raw.get("reminders", []) if isinstance(item, dict)]
    history = [sanitize_history_entry(item) for item in raw.get("history", []) if isinstance(item, dict)]
    ui = raw.get("ui", {}) if isinstance(raw.get("ui"), dict) else {}
    notes.sort(key=lambda item: (item.get("sort_order", 0), item.get("created_at", ""), item["id"]))
    reminders.sort(key=lambda item: (item.get("sort_order", 0), item.get("created_at", ""), item["id"]))

    max_history = settings["limits"]["max_history_items"]
    max_active_notes = settings["limits"]["max_active_notes"]
    max_active_reminders = settings["limits"]["max_active_reminders"]

    history = history[:max_history]

    active_notes = [item for item in notes if item.get("enabled") and item.get("status") == "active"]
    if len(active_notes) > max_active_notes:
        overflow = active_notes[max_active_notes:]
        overflow_ids = {item["id"] for item in overflow}
        for note in notes:
            if note["id"] in overflow_ids:
                note["enabled"] = False
                note["updated_at"] = now_iso()

    active_reminders = [item for item in reminders if item.get("enabled") and item.get("status") == "active"]
    if len(active_reminders) > max_active_reminders:
        overflow = active_reminders[max_active_reminders:]
        overflow_ids = {item["id"] for item in overflow}
        for reminder in reminders:
            if reminder["id"] in overflow_ids:
                reminder["enabled"] = False
                reminder["updated_at"] = now_iso()

    return {
        "settings": settings,
        "notes": notes,
        "reminders": reminders,
        "history": history,
        "ui": {
            "selected_tab": "reminders" if str(ui.get("selected_tab")) == "reminders" else "notes",
        },
    }



def merge_config(defaults: dict, user_config: dict) -> dict:
    merged = deepcopy(defaults)
    merged.update(user_config)
    return merged



def default_config_fragment() -> dict:
    return deepcopy(DEFAULT_STATE)



def limit_width(text: str, width: int, width_fn=None) -> str:
    width_fn = width_fn or len
    result = []
    current = 0
    for char in str(text):
        char_width = width_fn(char)
        if current + char_width > width:
            break
        current += char_width
        result.append(char)
    return "".join(result)



def split_line_chunks(text: str, width: int = LINE_WIDTH, width_fn=None) -> list[str]:
    width_fn = width_fn or len
    source = str(text or "")
    if not source:
        return [""]
    chunks = []
    current = []
    current_width = 0
    for char in source:
        char_width = width_fn(char)
        if current and current_width + char_width > width:
            chunks.append("".join(current))
            current = [char]
            current_width = char_width
        else:
            current.append(char)
            current_width += char_width
    if current or not chunks:
        chunks.append("".join(current))
    return chunks or [""]



def paginate_text(text: str, width: int = LINE_WIDTH, lines_per_page: int = LINES_PER_PAGE, width_fn=None) -> list[tuple[str, str]]:
    width_fn = width_fn or len
    raw_lines = str(text or "").replace("\r\n", "\n").split("\n")
    chunks = []
    for line in raw_lines:
        chunks.extend(split_line_chunks(line, width=width, width_fn=width_fn))
        if line == "":
            chunks.append("")
    if not chunks:
        chunks = [""]
    pages = []
    for index in range(0, len(chunks), lines_per_page):
        page = chunks[index:index + lines_per_page]
        if len(page) < lines_per_page:
            page.extend([""] * (lines_per_page - len(page)))
        pages.append(tuple(page[:lines_per_page]))
    return pages or [("", "")]



def build_note_display_text(note: dict) -> str:
    return str(note.get("text") or "")



def describe_reminder(reminder: dict) -> str:
    title = str(reminder.get("title") or "Напоминание").strip() or "Напоминание"
    body = str(reminder.get("text") or "").strip()
    schedule_parts = []
    if reminder.get("date"):
        schedule_parts.append(reminder["date"])
    if reminder.get("time"):
        schedule_parts.append(reminder["time"])
    if reminder.get("weekdays"):
        schedule_parts.append(" ".join(WEEKDAY_LABELS[idx] for idx in reminder["weekdays"]))
    schedule = " ".join(schedule_parts).strip()
    text = title
    if schedule:
        text += f"\n{schedule}"
    if body:
        text += f"\n{body}"
    return text



def reminder_due_key(reminder: dict, now: datetime) -> str | None:
    reminder_type = reminder.get("reminder_type", "once")
    reminder_time = normalize_time_value(reminder.get("time"), "09:00")
    slot = now.strftime("%H:%M")
    if slot != reminder_time:
        return None

    if reminder_type in {"once", "date_time"}:
        if not reminder.get("date"):
            return None
        if reminder.get("date") != now.date().isoformat():
            return None
        return f"once:{reminder['date']}:{reminder_time}"

    if reminder_type == "daily":
        return f"daily:{now.date().isoformat()}:{reminder_time}"

    if reminder_type == "weekly":
        weekdays = reminder.get("weekdays") or []
        if now.weekday() not in weekdays:
            return None
        return f"weekly:{now.date().isoformat()}:{reminder_time}"

    return None


@dataclass
class DisplayItem:
    kind: str
    item_id: str
    line1: str
    line2: str
    title: str
    page_index: int
    page_count: int
    source: dict


class NotesReminderManager:
    def __init__(self, config_getter, config_saver, width_fn=None, width: int = LINE_WIDTH):
        self._config_getter = config_getter
        self._config_saver = config_saver
        self.width_fn = width_fn or len
        self.width = width
        self.lock = threading.RLock()
        self.page_duration = DEFAULT_PAGE_DURATION
        self.alert_page_duration = DEFAULT_ALERT_PAGE_DURATION
        self.settings = sanitize_settings(None)
        self.notes: list[dict] = []
        self.reminders: list[dict] = []
        self.history: list[dict] = []
        self.ui_state = {"selected_tab": "notes"}
        self.note_cursor = 0
        self.reminder_cursor = 0
        self.active_item_key: tuple[str, str] | None = None
        self.active_page_index = 0
        self.active_started_at = 0.0
        self.is_suspended = False
        self.suspended_at = 0.0
        self.load_from_config()

    def _load_section(self) -> dict:
        cfg = self._config_getter()
        return sanitize_notes_reminders_state((cfg or {}).get("notes_reminders"))

    def load_from_config(self):
        with self.lock:
            section = self._load_section()
            self.settings = section["settings"]
            self.page_duration = self.settings["display"]["note_page_duration_seconds"]
            self.alert_page_duration = self.settings["display"]["reminder_page_duration_seconds"]
            self.notes = section["notes"]
            self.reminders = section["reminders"]
            self.history = section["history"]
            self.ui_state = section["ui"]

    def save_to_config(self):
        with self.lock:
            cfg = self._config_getter()
            cfg["notes_reminders"] = sanitize_notes_reminders_state({
                "settings": self.settings,
                "notes": self.notes,
                "reminders": self.reminders,
                "history": self.history,
                "ui": self.ui_state,
            })
            self._config_saver(cfg)

    def get_state_snapshot(self) -> dict:
        with self.lock:
            return {
                "settings": deepcopy(self.settings),
                "notes": deepcopy(self.notes),
                "reminders": deepcopy(self.reminders),
                "history": deepcopy(self.history),
                "ui": deepcopy(self.ui_state),
                "active_note_count": len(self.get_active_notes()),
                "active_reminder_count": len(self.get_active_reminders()),
            }

    def get_active_notes(self) -> list[dict]:
        return [item for item in self.notes if item.get("status") == "active" and item.get("enabled")]

    def get_active_reminders(self) -> list[dict]:
        return [item for item in self.reminders if item.get("status") == "active" and item.get("enabled")]

    def get_max_active_notes(self) -> int:
        return int(self.settings["limits"]["max_active_notes"])

    def get_max_active_reminders(self) -> int:
        return int(self.settings["limits"]["max_active_reminders"])

    def get_max_history_items(self) -> int:
        return int(self.settings["limits"]["max_history_items"])

    def get_triggered_reminders(self) -> list[dict]:
        reminders = [item for item in self.get_active_reminders() if item.get("triggered_at")]
        reminders.sort(key=lambda item: (item.get("triggered_at", ""), item.get("sort_order", 0), item.get("created_at", ""), item["id"]))
        return reminders

    def _find_note(self, note_id: str) -> dict | None:
        for note in self.notes:
            if note["id"] == note_id:
                return note
        return None

    def _find_reminder(self, reminder_id: str) -> dict | None:
        for reminder in self.reminders:
            if reminder["id"] == reminder_id:
                return reminder
        return None

    def _sort_notes(self):
        self.notes.sort(key=lambda item: (item.get("sort_order", 0), item.get("created_at", ""), item["id"]))

    def _sort_reminders(self):
        self.reminders.sort(key=lambda item: (item.get("sort_order", 0), item.get("created_at", ""), item["id"]))

    def add_note(self, text: str, interval_seconds: int, enabled: bool = True) -> dict:
        with self.lock:
            max_active_notes = self.get_max_active_notes()
            if enabled and len(self.get_active_notes()) >= max_active_notes:
                raise ValueError(f"Можно включить одновременно не более {max_active_notes} заметок.")
            note = sanitize_note({
                "text": text,
                "interval_seconds": interval_seconds or self.settings["defaults"]["note_interval_seconds"],
                "enabled": enabled,
                "status": "active",
                "sort_order": len(self.notes),
            })
            self.notes.append(note)
            self._sort_notes()
            self.save_to_config()
            return deepcopy(note)

    def update_note(self, note_id: str, text: str, interval_seconds: int, enabled: bool) -> dict:
        with self.lock:
            note = self._find_note(note_id)
            if note is None:
                raise ValueError("Заметка не найдена.")
            max_active_notes = self.get_max_active_notes()
            if enabled and (not note.get("enabled")) and len(self.get_active_notes()) >= max_active_notes:
                raise ValueError(f"Можно включить одновременно не более {max_active_notes} заметок.")
            note.update({
                "text": str(text).strip(),
                "interval_seconds": max(5, int(interval_seconds)),
                "enabled": bool(enabled),
                "updated_at": now_iso(),
            })
            self.save_to_config()
            return deepcopy(note)

    def delete_note(self, note_id: str):
        with self.lock:
            note = self._find_note(note_id)
            if note is None:
                raise ValueError("Заметка не найдена.")
            self._archive("note", note, "deleted")
            self.notes = [item for item in self.notes if item["id"] != note_id]
            self.save_to_config()

    def add_reminder(self, title: str, text: str, reminder_type: str, date_value: str, time_value: str, weekdays: list[int], enabled: bool = True) -> dict:
        with self.lock:
            max_active_reminders = self.get_max_active_reminders()
            if enabled and len(self.get_active_reminders()) >= max_active_reminders:
                raise ValueError(f"Можно включить одновременно не более {max_active_reminders} напоминаний.")
            reminder = sanitize_reminder({
                "title": title,
                "text": text,
                "reminder_type": reminder_type,
                "repeat_mode": "weekly" if reminder_type == "weekly" else "daily" if reminder_type == "daily" else "once",
                "date": date_value,
                "time": time_value,
                "weekdays": weekdays,
                "enabled": enabled,
                "status": "active",
                "sort_order": len(self.reminders),
            })
            self.reminders.append(reminder)
            self._sort_reminders()
            self.save_to_config()
            return deepcopy(reminder)

    def update_reminder(self, reminder_id: str, title: str, text: str, reminder_type: str, date_value: str, time_value: str, weekdays: list[int], enabled: bool) -> dict:
        with self.lock:
            reminder = self._find_reminder(reminder_id)
            if reminder is None:
                raise ValueError("Напоминание не найдено.")
            max_active_reminders = self.get_max_active_reminders()
            if enabled and (not reminder.get("enabled")) and len(self.get_active_reminders()) >= max_active_reminders:
                raise ValueError(f"Можно включить одновременно не более {max_active_reminders} напоминаний.")
            reminder.update(sanitize_reminder({
                **reminder,
                "title": title,
                "text": text,
                "reminder_type": reminder_type,
                "repeat_mode": "weekly" if reminder_type == "weekly" else "daily" if reminder_type == "daily" else "once",
                "date": date_value,
                "time": time_value,
                "weekdays": weekdays,
                "enabled": enabled,
                "updated_at": now_iso(),
            }))
            self.save_to_config()
            return deepcopy(reminder)

    def delete_reminder(self, reminder_id: str):
        with self.lock:
            reminder = self._find_reminder(reminder_id)
            if reminder is None:
                raise ValueError("Напоминание не найдено.")
            self._archive("reminder", reminder, "deleted")
            self.reminders = [item for item in self.reminders if item["id"] != reminder_id]
            self.save_to_config()

    def _archive(self, source_type: str, item: dict, action: str):
        title = item.get("title") or ("Заметка" if source_type == "note" else "Напоминание")
        text = item.get("text") or describe_reminder(item) if source_type == "reminder" else item.get("text")
        self.history.insert(0, sanitize_history_entry({
            "source_type": source_type,
            "source_id": item.get("id", ""),
            "title": title,
            "text": text,
            "action": action,
            "payload": deepcopy(item),
        }))
        self.history = self.history[:self.get_max_history_items()]

    def mark_item(self, kind: str, item_id: str, action: str):
        with self.lock:
            if kind == "note":
                item = self._find_note(item_id)
                collection_name = "notes"
            else:
                item = self._find_reminder(item_id)
                collection_name = "reminders"
            if item is None:
                raise ValueError("Элемент не найден.")
            item["status"] = "done" if action == "done" else "hidden"
            item["enabled"] = False
            item["updated_at"] = now_iso()
            if kind == "reminder":
                item["triggered_at"] = ""
            self._archive(kind, item, action)
            setattr(self, collection_name, [entry for entry in getattr(self, collection_name) if entry["id"] != item_id])
            if self.active_item_key == (kind, item_id):
                self.active_item_key = None
                self.active_page_index = 0
                self.active_started_at = 0.0
            self.save_to_config()

    def activate_due_reminders(self, now: datetime | None = None) -> list[dict]:
        with self.lock:
            now = now or datetime.now()
            activated = []
            for reminder in self.get_active_reminders():
                due_key = reminder_due_key(reminder, now)
                if not due_key:
                    continue
                if reminder.get("last_trigger_key") == due_key and reminder.get("triggered_at"):
                    continue
                reminder["last_trigger_key"] = due_key
                reminder["triggered_at"] = now_iso()
                reminder["updated_at"] = now_iso()
                activated.append(deepcopy(reminder))
            if activated:
                self.save_to_config()
            return activated

    def _build_pages_for_note(self, note: dict) -> list[tuple[str, str]]:
        return paginate_text(build_note_display_text(note), width=self.width, width_fn=self.width_fn)

    def _build_pages_for_reminder(self, reminder: dict) -> list[tuple[str, str]]:
        return paginate_text(describe_reminder(reminder), width=self.width, width_fn=self.width_fn)

    def _get_due_notes(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now()
        due = []
        for note in self.get_active_notes():
            last_shown = parse_iso_datetime(note.get("last_shown_at"))
            if last_shown is None or now - last_shown >= timedelta(seconds=int(note.get("interval_seconds", DEFAULT_NOTE_INTERVAL))):
                due.append(note)
        due.sort(key=lambda item: (item.get("sort_order", 0), item.get("created_at", ""), item["id"]))
        return due

    def _select_due_note(self, due_notes: list[dict]) -> dict | None:
        if not due_notes:
            return None
        if self.note_cursor >= len(due_notes):
            self.note_cursor = 0
        note = due_notes[self.note_cursor]
        self.note_cursor = (self.note_cursor + 1) % len(due_notes)
        return note

    def _select_triggered_reminder(self, reminders: list[dict]) -> dict | None:
        if not reminders:
            return None
        if self.reminder_cursor >= len(reminders):
            self.reminder_cursor = 0
        reminder = reminders[self.reminder_cursor]
        return reminder

    def _set_active_item(self, kind: str, item_id: str):
        if self.active_item_key != (kind, item_id):
            self.active_item_key = (kind, item_id)
            self.active_page_index = 0
            self.active_started_at = time.time()

    def set_suspended(self, suspended: bool):
        with self.lock:
            if suspended:
                if not self.is_suspended:
                    self.is_suspended = True
                    self.suspended_at = time.time()
                return
            if not self.is_suspended:
                return
            paused_for = max(0.0, time.time() - self.suspended_at)
            if self.active_started_at:
                self.active_started_at += paused_for
            self.is_suspended = False
            self.suspended_at = 0.0

    def manual_cycle(self, direction: int):
        with self.lock:
            reminders = self.get_triggered_reminders()
            if reminders:
                self.reminder_cursor = (self.reminder_cursor + direction) % len(reminders)
                selected = reminders[self.reminder_cursor]
                self._set_active_item("reminder", selected["id"])
                return deepcopy(selected)

            due_notes = self._get_due_notes()
            if due_notes:
                self.note_cursor = (self.note_cursor + direction) % len(due_notes)
                selected = due_notes[self.note_cursor]
                self._set_active_item("note", selected["id"])
                return deepcopy(selected)
        return None

    def get_current_display(self, now: datetime | None = None) -> DisplayItem | None:
        with self.lock:
            now = now or datetime.now()
            self.activate_due_reminders(now)
            reminders = self.get_triggered_reminders()
            if reminders:
                reminder = self._select_triggered_reminder(reminders)
                if reminder is None:
                    return None
                self._set_active_item("reminder", reminder["id"])
                pages = self._build_pages_for_reminder(reminder)
                elapsed = time.time() - self.active_started_at
                if pages:
                    self.active_page_index = int(elapsed // self.alert_page_duration) % len(pages)
                line1, line2 = pages[self.active_page_index]
                return DisplayItem(
                    kind="reminder",
                    item_id=reminder["id"],
                    line1=line1,
                    line2=line2,
                    title=reminder.get("title") or "Напоминание",
                    page_index=self.active_page_index,
                    page_count=len(pages),
                    source=deepcopy(reminder),
                )

            due_note = self._select_due_note(self._get_due_notes(now))
            if due_note is None:
                if self.active_item_key and self.active_item_key[0] == "note":
                    self.active_item_key = None
                    self.active_page_index = 0
                    self.active_started_at = 0.0
                return None
            self._set_active_item("note", due_note["id"])
            pages = self._build_pages_for_note(due_note)
            if not pages:
                return None
            elapsed = time.time() - self.active_started_at
            page_index = int(elapsed // self.page_duration)
            if page_index >= len(pages):
                due_note["last_shown_at"] = now_iso()
                due_note["updated_at"] = now_iso()
                self.active_item_key = None
                self.active_page_index = 0
                self.active_started_at = 0.0
                self.save_to_config()
                return None
            self.active_page_index = page_index
            line1, line2 = pages[page_index]
            return DisplayItem(
                kind="note",
                item_id=due_note["id"],
                line1=line1,
                line2=line2,
                title="Заметка",
                page_index=page_index,
                page_count=len(pages),
                source=deepcopy(due_note),
            )
