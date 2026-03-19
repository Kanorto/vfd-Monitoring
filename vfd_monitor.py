import getpass
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import winreg
from ctypes import Structure, WinDLL, byref, wintypes

import psutil
import pystray
import serial
import serial.tools.list_ports
import win32api
import wmi
from PIL import Image, ImageDraw
from py3nvml import py3nvml as nvml

import app_hotkeys
import vfd_display
from alerts_engine import DEFAULT_STATE as ALERTS_DEFAULT_STATE, TelegramAlertManager, build_alert_from_telegram_message
from alerts_gui import open_alerts_window
from metrics_engine import RuntimeMetricsSampler
from notes_engine import DEFAULT_STATE as NOTES_DEFAULT_STATE, NotesReminderManager
from notes_gui import open_notes_reminders_window

# --- НАСТРОЙКИ VFD ---
APP_VERSION = os.environ.get("VFD_MONITOR_VERSION", "1.1.0")
BAUD = 9600
CLR = b'\x0c'
HOME = b'\x0b'
INIT_RUS = b'\x1b\x74\x07'
CONFIG_NAME = "vfd_config.json"
LOG_NAME = "vfd_monitor.log"
LINE_WIDTH = 20
DEG_CHAR = "\u0001"
SPECIAL_CHARS = {
    DEG_CHAR: b"\xf8",
}
DISPLAY_FLAGS = {
    "show_cpu_usage": "CPU %",
    "show_cpu_temp": "CPU °C",
    "show_gpu_usage": "GPU %",
    "show_gpu_temp": "GPU °C",
    "show_ram": "RAM %",
    "show_disk": "Диск",
    "show_network": "Сеть",
}
TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"
TELEGRAM_LONG_POLL_TIMEOUT = 25
TELEGRAM_REQUEST_TIMEOUT = TELEGRAM_LONG_POLL_TIMEOUT + 10
METRICS_SAMPLE_INTERVAL = 1.0
DEFAULT_REPOSITORY = os.environ.get("VFD_MONITOR_GITHUB_REPO", "Kanorto/vfd-Monitoring")
UPDATE_API_BASE = "https://api.github.com/repos/{repo}/releases/latest"
UPDATE_HISTORY_API_BASE = "https://api.github.com/repos/{repo}/releases?per_page={limit}"
UPDATE_DOWNLOAD_CHUNK = 1024 * 128
UPDATE_TIMEOUT = 20
UPDATE_ATTEMPTS = 4
UPDATE_POLL_INTERVAL = 1800
UPDATE_ALERT_THRESHOLD = 3
CHANGELOG_HISTORY_LIMIT = 10
STATUS_FRAMES = ['|', '/', '-', '\\']
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312

DEFAULT_METRIC_FORMATS = {
    "cpu": [
        "CPU{usage:02d}% {temp:02d}" + DEG_CHAR,
        "C{usage:02d} {temp:02d}" + DEG_CHAR,
        "CPU{usage:02d}%",
        "C{usage:02d}%",
        "C{usage:02d}",
        "CPU{temp:02d}" + DEG_CHAR,
        "C{temp:02d}" + DEG_CHAR,
        "C{temp:02d}",
    ],
    "gpu": [
        "GPU{usage:02d}% {temp:02d}" + DEG_CHAR,
        "G{usage:02d} {temp:02d}" + DEG_CHAR,
        "GPU{usage:02d}%",
        "G{usage:02d}%",
        "G{usage:02d}",
        "GPU{temp:02d}" + DEG_CHAR,
        "G{temp:02d}" + DEG_CHAR,
        "G{temp:02d}",
    ],
    "ram": [
        "RAM{value:02d}%",
        "R{value:02d}%",
        "R{value:02d}",
    ],
    "disk": [
        "disk:{read}/{write}",
        "d:{read}/{write}",
        "disk{read}/{write}",
        "d{read}/{write}",
    ],
    "network": [
        "net:{recv}/{send}",
        "n:{recv}/{send}",
        "net{recv}/{send}",
        "n{recv}/{send}",
    ],
}
DEFAULT_LINE_SPACING = {
    "primary": " ",
    "secondary": " ",
    "primary_compact": "",
    "secondary_compact": "",
}
DEFAULT_HOTKEYS = app_hotkeys.DEFAULT_HOTKEYS
HOTKEY_LABELS = app_hotkeys.HOTKEY_LABELS
HOTKEY_CALLBACKS = {}
MODIFIER_ALIASES = {
    "CTRL": "Ctrl",
    "CONTROL": "Ctrl",
    "ALT": "Alt",
    "SHIFT": "Shift",
    "WIN": "Win",
    "WINDOWS": "Win",
}
VK_NAME_MAP = {
    "SPACE": 0x20,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "DEL": 0x2E,
    "INSERT": 0x2D,
    "INS": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PRIOR": 0x21,
    "PAGEDOWN": 0x22,
    "NEXT": 0x22,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "PLUS": 0xBB,
    "MINUS": 0xBD,
}
for index in range(1, 25):
    VK_NAME_MAP[f"F{index}"] = 0x6F + index
for digit in "0123456789":
    VK_NAME_MAP[digit] = ord(digit)
for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    VK_NAME_MAP[letter] = ord(letter)


def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


os.chdir(get_base_path())
CONFIG_PATH = os.path.join(get_base_path(), CONFIG_NAME)
LOG_PATH = os.path.join(get_base_path(), LOG_NAME)
UPDATES_DIR = os.path.join(get_base_path(), "updates")

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
is_display_on = True
current_interval = 1.0
ser = None
app_running = True
tray_icon = None
serial_lock = threading.Lock()
display_override_lock = threading.Lock()
display_override = None
update_check_lock = threading.Lock()
update_download_lock = threading.Lock()
manual_update_feedback = {
    "status": "idle",
    "details": "",
}
update_alert_lock = threading.Lock()
hotkey_manager = None
notes_manager = None
sensor_cache_lock = threading.Lock()
telegram_poll_state_lock = threading.Lock()
telegram_poll_state = {
    "announced_waiting": False,
}
sensor_runtime_cache = {
    "nvidia_smi": {
        "timestamp": 0.0,
        "usage": None,
        "temp": None,
    },
    "logged_sources": set(),
    "logged_failures": set(),
}


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


ensure_dir(UPDATES_DIR)


def deep_merge(base, updates):
    result = {}
    for key, value in base.items():
        if isinstance(value, dict):
            incoming = updates.get(key, {}) if isinstance(updates.get(key), dict) else {}
            result[key] = deep_merge(value, incoming)
        elif isinstance(value, list):
            incoming = updates.get(key)
            result[key] = incoming if isinstance(incoming, list) and incoming else list(value)
        else:
            result[key] = updates.get(key, value)
    for key, value in updates.items():
        if key not in result:
            result[key] = value
    return result


def coerce_text(value, fallback):
    if value is None:
        return fallback
    return str(value)


def sanitize_spacing(spacing):
    data = deep_merge(DEFAULT_LINE_SPACING, spacing if isinstance(spacing, dict) else {})
    for key, fallback in DEFAULT_LINE_SPACING.items():
        data[key] = coerce_text(data.get(key), fallback)
    return data


def sanitize_metric_formats(formats):
    raw_formats = formats if isinstance(formats, dict) else {}
    result = {}
    for key, defaults in DEFAULT_METRIC_FORMATS.items():
        user_value = raw_formats.get(key)
        if isinstance(user_value, list):
            cleaned = [str(item) for item in user_value if str(item).strip()]
            result[key] = cleaned or list(defaults)
        else:
            result[key] = list(defaults)
    return result


def canonicalize_hotkey(value):
    text = str(value or '').strip()
    if not text:
        return ''
    parts = [part.strip() for part in text.replace('-', '+').split('+') if part.strip()]
    if not parts:
        return ''

    modifiers = []
    key = ''
    for part in parts:
        upper = part.upper()
        modifier = MODIFIER_ALIASES.get(upper)
        if modifier:
            if modifier not in modifiers:
                modifiers.append(modifier)
            continue
        if len(upper) == 1 and upper.isalpha():
            key = upper
        else:
            key = upper.title() if upper.startswith('F') and upper[1:].isdigit() else upper
    if not key:
        return '+'.join(modifiers)
    ordered_modifiers = [name for name in ['Ctrl', 'Alt', 'Shift', 'Win'] if name in modifiers]
    return '+'.join(ordered_modifiers + [key])


def sanitize_hotkeys(hotkeys):
    return app_hotkeys.sanitize_hotkeys(hotkeys)


def load_config():
    defaults = {
        "show_cpu_usage": True,
        "show_cpu_temp": True,
        "show_gpu_usage": True,
        "show_gpu_temp": True,
        "show_ram": True,
        "show_disk": True,
        "show_network": True,
        "autostart": False,
        "update_interval": 1.0,
        "app_version": APP_VERSION,
        "github_repo": DEFAULT_REPOSITORY,
        "known_latest_version": APP_VERSION,
        "pending_update_version": "",
        "pending_update_path": "",
        "pending_update_target": "",
        "pending_update_notes": "",
        "last_changelog_version": "",
        "last_changelog": "",
        "release_history": [],
        "last_update_check": 0.0,
        "last_update_error": "",
        "last_release_url": "",
        "update_failure_count": 0,
        "last_alerted_version": "",
        "update_channel": "release",
        "metric_formats": DEFAULT_METRIC_FORMATS,
        "line_spacing": DEFAULT_LINE_SPACING,
        "hotkeys_enabled": True,
        "hotkeys": DEFAULT_HOTKEYS,
        "notes_reminders": NOTES_DEFAULT_STATE["notes_reminders"],
        "telegram_alerts": ALERTS_DEFAULT_STATE["telegram_alerts"],
    }
    legacy_key_map = {
        "show_gpu": "show_gpu_usage",
    }
    cfg = defaults.copy()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as file:
                user_cfg = json.load(file)
            for key, value in user_cfg.items():
                mapped_key = legacy_key_map.get(key, key)
                if mapped_key in cfg:
                    cfg[mapped_key] = value
        except Exception as e:
            logging.error(f"Ошибка чтения конфига: {e}")
    cfg["metric_formats"] = sanitize_metric_formats(cfg.get("metric_formats"))
    cfg["line_spacing"] = sanitize_spacing(cfg.get("line_spacing"))
    cfg["hotkeys"] = sanitize_hotkeys(cfg.get("hotkeys"))
    cfg["hotkeys_enabled"] = bool(cfg.get("hotkeys_enabled", True))
    if not isinstance(cfg.get("notes_reminders"), dict):
        cfg["notes_reminders"] = NOTES_DEFAULT_STATE["notes_reminders"]
    if not isinstance(cfg.get("telegram_alerts"), dict):
        cfg["telegram_alerts"] = ALERTS_DEFAULT_STATE["telegram_alerts"]
    with open(CONFIG_PATH, 'w', encoding='utf-8') as file:
        json.dump(cfg, file, indent=4, ensure_ascii=False)
    return cfg


cfg = load_config()
current_interval = cfg.get("update_interval", 1.0)


def save_config(config):
    try:
        config["metric_formats"] = sanitize_metric_formats(config.get("metric_formats"))
        config["line_spacing"] = sanitize_spacing(config.get("line_spacing"))
        config["hotkeys"] = sanitize_hotkeys(config.get("hotkeys"))
        config["hotkeys_enabled"] = bool(config.get("hotkeys_enabled", True))
        if not isinstance(config.get("notes_reminders"), dict):
            config["notes_reminders"] = NOTES_DEFAULT_STATE["notes_reminders"]
        if not isinstance(config.get("telegram_alerts"), dict):
            config["telegram_alerts"] = ALERTS_DEFAULT_STATE["telegram_alerts"]
        with open(CONFIG_PATH, 'w', encoding='utf-8') as file:
            json.dump(config, file, indent=4, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка сохранения конфига: {e}")


def build_release_entry(version='', notes='', release_url='', published_at=''):
    return {
        "version": sanitize_version(version),
        "notes": format_release_notes(notes),
        "release_url": str(release_url or '').strip(),
        "published_at": str(published_at or '').strip(),
    }


def sanitize_release_history(history):
    items = history if isinstance(history, list) else []
    result = []
    seen_versions = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        entry = build_release_entry(
            version=item.get('version', ''),
            notes=item.get('notes', ''),
            release_url=item.get('release_url', ''),
            published_at=item.get('published_at', ''),
        )
        version = entry['version']
        if not version or version in seen_versions:
            continue
        seen_versions.add(version)
        result.append(entry)
        if len(result) >= CHANGELOG_HISTORY_LIMIT:
            break
    return result


def store_release_history(entries):
    cfg['release_history'] = sanitize_release_history(entries)
    if cfg['release_history']:
        cfg['last_changelog_version'] = cfg['release_history'][0]['version']
        cfg['last_changelog'] = cfg['release_history'][0]['notes']
    save_config(cfg)


# --- НИЗКОУРОВНЕВАЯ ОТРИСОВКА ---
def get_vfd_char_width(char):
    return vfd_display.get_vfd_char_width(char, SPECIAL_CHARS)


def get_vfd_text_width(text):
    return vfd_display.get_vfd_text_width(text, SPECIAL_CHARS)


def trim_vfd_text(text, width=LINE_WIDTH):
    return vfd_display.trim_vfd_text(text, width=width, special_chars=SPECIAL_CHARS)


def fit_text(text, width=LINE_WIDTH, align='left'):
    return vfd_display.fit_text(text, width=width, align=align, special_chars=SPECIAL_CHARS)



notes_manager = NotesReminderManager(lambda: cfg, save_config, width_fn=get_vfd_char_width, width=LINE_WIDTH)
alert_manager = TelegramAlertManager(lambda: cfg, save_config, width_fn=get_vfd_char_width, width=LINE_WIDTH)


def set_notes_overlay_suspended(suspended: bool):
    if notes_manager is None:
        return
    try:
        notes_manager.set_suspended(suspended)
    except Exception as exc:
        logging.error(f"Ошибка смены паузы overlay-менеджера: {exc}")



def get_active_overlay_item():
    if alert_manager is not None:
        try:
            active_alert = alert_manager.get_current_display()
            if active_alert is not None:
                set_notes_overlay_suspended(True)
                return active_alert
        except Exception as exc:
            logging.error(f"Ошибка alert-менеджера: {exc}")

    set_notes_overlay_suspended(False)
    if notes_manager is None:
        return None
    try:
        return notes_manager.get_current_display()
    except Exception as exc:
        logging.error(f"Ошибка overlay-менеджера: {exc}")
        return None


def show_notes_window(icon=None, item=None):
    open_notes_reminders_window(notes_manager, initial_tab='notes', on_refresh=refresh_menu)


def show_reminders_window(icon=None, item=None):
    open_notes_reminders_window(notes_manager, initial_tab='reminders', on_refresh=refresh_menu)


def show_alerts_window(icon=None, item=None):
    open_alerts_window(alert_manager, on_refresh=refresh_menu)


def cycle_overlay_item(direction):
    if alert_manager is not None:
        item = alert_manager.manual_cycle(direction)
        if item is not None:
            preview = (item.get('author_name') or item.get('text') or '')[:LINE_WIDTH]
            if preview:
                show_temporary_message('ALERT', preview, duration=1.2)
            refresh_menu()
            return
    if notes_manager is None:
        return
    item = notes_manager.manual_cycle(direction)
    if item is not None:
        title = 'ЗАМЕТКА' if str(item.get('id', '')).startswith('note_') else 'НАПОМН'
        preview = (item.get('title') or item.get('text') or '')[:LINE_WIDTH]
        if preview:
            show_temporary_message(title, preview, duration=1.2)


def hide_active_overlay_item():
    active = get_active_overlay_item()
    if active is None:
        return
    if hasattr(active, 'alert_id'):
        alert_manager.dismiss_alert(active.alert_id, 'hidden')
        show_temporary_message('СКРЫТО', active.title[:LINE_WIDTH], duration=1.5)
        refresh_menu()
        return
    notes_manager.mark_item(active.kind, active.item_id, 'hide')
    show_temporary_message('СКРЫТО', active.title[:LINE_WIDTH], duration=1.5)
    refresh_menu()


def complete_active_overlay_item():
    active = get_active_overlay_item()
    if active is None:
        return
    if hasattr(active, 'alert_id'):
        alert_manager.dismiss_alert(active.alert_id, 'done')
        show_temporary_message('ВЫПОЛНЕНО', active.title[:LINE_WIDTH], duration=1.5)
        refresh_menu()
        return
    notes_manager.mark_item(active.kind, active.item_id, 'done')
    show_temporary_message('ВЫПОЛНЕНО', active.title[:LINE_WIDTH], duration=1.5)
    refresh_menu()


def encode_vfd_text(text):
    return vfd_display.encode_vfd_text(text, SPECIAL_CHARS)



def write_serial(payload):
    global ser
    if ser is None:
        return
    with serial_lock:
        ser.write(payload)
        ser.flush()



def write_screen(line1='', line2='', clear_first=False):
    payload = bytearray()
    if clear_first:
        payload.extend(CLR)
    payload.extend(HOME)
    payload.extend(encode_vfd_text(fit_text(line1, LINE_WIDTH)))
    payload.extend(encode_vfd_text(fit_text(line2, LINE_WIDTH)))
    write_serial(bytes(payload))



def set_display_override(line1='', line2=''):
    global display_override
    with display_override_lock:
        display_override = (fit_text(line1, LINE_WIDTH), fit_text(line2, LINE_WIDTH))



def clear_display_override():
    global display_override
    with display_override_lock:
        display_override = None



def get_display_override():
    with display_override_lock:
        return display_override



def show_temporary_message(line1, line2, duration=2.5):
    def worker():
        set_display_override(line1, line2)
        time.sleep(duration)
        clear_display_override()

    threading.Thread(target=worker, daemon=True).start()


class StatusAnimator:
    def __init__(self, title, subtitle_getter=None):
        self.title = title
        self.subtitle_getter = subtitle_getter or (lambda: "Подождите")
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=1)
        clear_display_override()

    def _run(self):
        idx = 0
        while not self.stop_event.is_set():
            subtitle = self.subtitle_getter() or "Подождите"
            frame = STATUS_FRAMES[idx % len(STATUS_FRAMES)]
            set_display_override(self.title, f"{frame} {subtitle}"[:LINE_WIDTH])
            idx += 1
            time.sleep(0.2)


# --- ЛОГИКА ПРИВЕТСТВИЙ И ПРОЩАНИЙ ---
def show_greeting():
    user = getpass.getuser().upper()[:15]
    greetings = [
        f"ПРИВЕТ, {user}!",
        "СИСТЕМА АКТИВНА",
        "ДОБРО ПОЖАЛОВАТЬ",
        f"СТАРТ ОС, {user}",
    ]
    try:
        write_screen(random.choice(greetings), f"ВЕРСИЯ {APP_VERSION}", clear_first=True)
        time.sleep(3)
        write_screen(clear_first=True)
    except Exception:
        pass



def show_farewell():
    global ser
    if ser is None:
        return
    try:
        write_screen("ДО ВСТРЕЧИ!", "СИСТЕМА ВЫКЛЮЧЕНА", clear_first=True)
        time.sleep(2)
        write_serial(CLR)
        with serial_lock:
            ser.close()
        ser = None
    except Exception:
        pass



def cleanup_and_exit(event):
    global app_running
    app_running = False
    if hotkey_manager is not None:
        hotkey_manager.stop()
    show_farewell()
    return True


win32api.SetConsoleCtrlHandler(cleanup_and_exit, True)


# --- ОБНОВЛЕНИЯ И ВЕРСИИ ---
def extract_repo_slug(raw_repo):
    repo = (raw_repo or '').strip()
    if not repo:
        return ''
    if 'github.com' in repo:
        parsed = urllib.parse.urlparse(repo)
        path = parsed.path.strip('/')
        if path.endswith('.git'):
            path = path[:-4]
        return path
    return repo.strip('/')



def parse_version_parts(version):
    tokens = re.findall(r'\d+|[A-Za-z]+', str(version))
    parts = []
    for token in tokens:
        if token.isdigit():
            parts.append((0, int(token)))
        else:
            parts.append((1, token.lower()))
    return parts



def is_newer_version(candidate, current):
    return parse_version_parts(candidate) > parse_version_parts(current)



def sanitize_version(version):
    cleaned = str(version).strip()
    if cleaned.lower().startswith('v'):
        cleaned = cleaned[1:]
    return cleaned



def perform_json_request(url):
    last_error = None
    headers = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': f'vfd-monitor/{APP_VERSION}',
    }
    for attempt in range(1, UPDATE_ATTEMPTS + 1):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=UPDATE_TIMEOUT) as response:
                return json.loads(response.read().decode('utf-8'))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            logging.warning(f"Попытка {attempt}/{UPDATE_ATTEMPTS} проверки обновления не удалась: {exc}")
            time.sleep(min(attempt * 1.5, 6))
    raise RuntimeError(f"Не удалось получить данные релиза: {last_error}")



def pick_release_asset(release):
    assets = release.get('assets') or []
    if not assets:
        raise RuntimeError('В релизе нет файлов для скачивания')

    current_name = os.path.basename(sys.executable if getattr(sys, 'frozen', False) else __file__).lower()
    exact_match = None
    exe_assets = []

    for asset in assets:
        asset_name = asset.get('name', '').lower()
        if asset_name == current_name and asset_name.endswith('.exe'):
            exact_match = asset
        if asset_name.endswith('.exe'):
            exe_assets.append(asset)
    if exact_match is not None:
        return exact_match
    if exe_assets:
        return exe_assets[0]
    raise RuntimeError('В релизе нет .exe-файла. Автообновление поддерживает только готовый Windows-бинарник и не требует Python у клиента.')



def format_release_notes(notes):
    text = (notes or '').replace('\r\n', '\n').strip()
    if not text:
        return 'Изменения не указаны автором релиза.'
    return text


def release_to_entry(release):
    version = sanitize_version(release.get('tag_name') or release.get('name') or '')
    if not version:
        return None
    return build_release_entry(
        version=version,
        notes=release.get('body', ''),
        release_url=release.get('html_url') or get_release_page_url(version),
        published_at=release.get('published_at') or release.get('created_at') or '',
    )


def merge_release_histories(*history_groups):
    result = []
    seen_versions = set()
    for history in history_groups:
        for item in sanitize_release_history(history):
            version = item['version']
            if version in seen_versions:
                continue
            seen_versions.add(version)
            result.append(item)
            if len(result) >= CHANGELOG_HISTORY_LIMIT:
                return result
    return result


def get_recent_releases(limit=CHANGELOG_HISTORY_LIMIT):
    repo = extract_repo_slug(cfg.get('github_repo', ''))
    if not repo:
        raise RuntimeError('В конфиге не указан github_repo')
    url = UPDATE_HISTORY_API_BASE.format(repo=repo, limit=max(1, min(int(limit), CHANGELOG_HISTORY_LIMIT)))
    releases = perform_json_request(url)
    if not isinstance(releases, list):
        raise RuntimeError('GitHub не вернул список релизов')

    entries = []
    for release in releases:
        if not isinstance(release, dict):
            continue
        entry = release_to_entry(release)
        if entry is not None:
            entries.append(entry)
        if len(entries) >= CHANGELOG_HISTORY_LIMIT:
            break
    if not entries:
        raise RuntimeError('В GitHub Releases пока нет записей changelog')
    return entries


def refresh_release_history():
    cached_history = sanitize_release_history(cfg.get('release_history'))
    try:
        remote_history = get_recent_releases()
        merged_history = merge_release_histories(remote_history, cached_history)
        store_release_history(merged_history)
        return merged_history
    except Exception as exc:
        logging.warning(f"Не удалось обновить историю changelog: {exc}")
        if cached_history:
            return cached_history
        raise


def remember_release_entry(version='', notes='', release_url='', published_at=''):
    if not version:
        return sanitize_release_history(cfg.get('release_history'))
    entry = build_release_entry(
        version=version,
        notes=notes,
        release_url=release_url,
        published_at=published_at,
    )
    history = merge_release_histories([entry], cfg.get('release_history'))
    store_release_history(history)
    return history


def build_changelog_text(history):
    entries = sanitize_release_history(history)
    if not entries:
        fallback_version = sanitize_version(
            cfg.get('pending_update_version')
            or cfg.get('last_changelog_version')
            or cfg.get('known_latest_version')
            or APP_VERSION
        )
        fallback_notes = cfg.get('pending_update_notes') or cfg.get('last_changelog') or 'История изменений пока недоступна.'
        entries = [build_release_entry(version=fallback_version, notes=fallback_notes)]

    sections = []
    total = len(entries)
    for index, entry in enumerate(entries, start=1):
        parts = [f"[{index:02d}/{total:02d}] Версия {entry['version']}"]
        if entry.get('published_at'):
            parts.append(f"Дата релиза: {entry['published_at']}")
        if entry.get('release_url'):
            parts.append(f"Ссылка: {entry['release_url']}")
        parts.append("")
        parts.append(entry['notes'])
        sections.append('\n'.join(parts).strip())
    separator = '\n\n' + ('=' * 78) + '\n\n'
    return separator.join(sections)


cfg["release_history"] = sanitize_release_history(cfg.get("release_history"))
save_config(cfg)


def get_release_page_url(version=''):
    repo = extract_repo_slug(cfg.get('github_repo', DEFAULT_REPOSITORY))
    if not repo:
        repo = DEFAULT_REPOSITORY
    base_url = f"https://github.com/{repo}/releases"
    if version:
        return f"{base_url}/tag/v{sanitize_version(version)}"
    return base_url


def refresh_menu(icon=None):
    target_icon = icon or tray_icon
    if target_icon is not None:
        try:
            target_icon.update_menu()
        except Exception as exc:
            logging.warning("Не удалось обновить меню трея: %s", exc)


def safe_tray_callback(callback, refresh_after=False):
    def wrapped(icon=None, item=None):
        try:
            return callback(icon, item)
        except SystemExit:
            raise
        except Exception as exc:
            logging.exception("Ошибка tray callback %s", getattr(callback, '__name__', 'callback'))
            show_temporary_message('ОШИБКА', trim_vfd_text(str(exc), LINE_WIDTH), duration=2.5)
        finally:
            if refresh_after:
                refresh_menu(icon)
    return wrapped


def show_manual_update_alert(version, release_url, error_text):
    with update_alert_lock:
        if cfg.get('last_alerted_version') == version:
            return

        cfg['last_alerted_version'] = version
        save_config(cfg)

    def worker():
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        message = (
            f"Автообновление до версии {version} несколько раз завершилось ошибкой.\n\n"
            f"Ошибка: {error_text}\n\n"
            f"Скачайте релиз вручную по ссылке:\n{release_url}\n\n"
            "Открыть страницу релиза сейчас?"
        )
        try:
            if messagebox.askyesno("Автообновление VFD Monitor", message, parent=root):
                webbrowser.open(release_url)
        finally:
            root.destroy()

    threading.Thread(target=worker, daemon=True).start()


def register_update_failure(version='', release_url='', error_text=''):
    cfg['update_failure_count'] = int(cfg.get('update_failure_count', 0)) + 1
    if version:
        cfg['known_latest_version'] = version
    if release_url:
        cfg['last_release_url'] = release_url
    cfg['last_update_error'] = error_text
    save_config(cfg)

    failure_count = cfg['update_failure_count']
    release_link = release_url or cfg.get('last_release_url') or get_release_page_url(version)
    logging.warning(
        "Сбой автообновления версии %s (%s/%s): %s",
        version or 'неизвестно',
        failure_count,
        UPDATE_ALERT_THRESHOLD,
        error_text,
    )
    if failure_count >= UPDATE_ALERT_THRESHOLD:
        show_manual_update_alert(version or cfg.get('known_latest_version', ''), release_link, error_text)


def clear_update_failure_state():
    cfg['update_failure_count'] = 0
    cfg['last_update_error'] = ''
    cfg['last_alerted_version'] = ''
    save_config(cfg)


def get_launch_command():
    if getattr(sys, 'frozen', False):
        return [sys.executable]

    pythonw = sys.executable
    if pythonw.lower().endswith('python.exe'):
        candidate = pythonw[:-10] + 'pythonw.exe'
        if os.path.exists(candidate):
            pythonw = candidate
    return [pythonw, os.path.abspath(__file__)]



def get_autostart_command():
    return subprocess.list2cmdline(get_launch_command())



def resolve_pending_update_target(pending_path=''):
    configured_target = cfg.get('pending_update_target', '')
    if configured_target:
        return configured_target
    if getattr(sys, 'frozen', False):
        return sys.executable
    pending_name = os.path.basename(pending_path or '') or 'VFD PC Monitor.exe'
    return os.path.join(get_base_path(), pending_name)


def get_release_target_path(release_info):
    asset_name = os.path.basename(release_info.get('asset_name', '') or '')
    if not asset_name.lower().endswith('.exe'):
        raise RuntimeError('Автообновление принимает только .exe-релизы, чтобы приложение не зависело от установленного Python.')
    if getattr(sys, 'frozen', False):
        return os.path.abspath(sys.executable)
    return os.path.join(get_base_path(), asset_name)


def build_update_script(target_path, staged_path, launch_path):
    script_path = os.path.join(tempfile.gettempdir(), 'vfd_apply_update.cmd')
    lines = [
        '@echo off',
        'setlocal enabledelayedexpansion',
        f'set "TARGET={target_path}"',
        f'set "STAGED={staged_path}"',
        f'set "LAUNCH={launch_path}"',
        'for /l %%I in (1,1,90) do (',
        '  copy /Y "!STAGED!" "!TARGET!" >nul 2>&1 && goto copied',
        '  timeout /t 1 /nobreak >nul',
        ')',
        'exit /b 1',
        ':copied',
        'del /Q "!STAGED!" >nul 2>&1',
        'start "" "!LAUNCH!"',
        'del "%~f0" >nul 2>&1',
        'exit /b 0',
    ]
    with open(script_path, 'w', encoding='cp866', errors='replace') as file:
        file.write('\r\n'.join(lines))
    return script_path



def schedule_pending_update_install():
    pending_path = cfg.get('pending_update_path', '')
    pending_version = cfg.get('pending_update_version', '')
    if not pending_path or not os.path.exists(pending_path):
        return False

    target_path = resolve_pending_update_target(pending_path)
    if not target_path.lower().endswith('.exe'):
        raise RuntimeError('Автообновление ожидает установку .exe-файла и не поддерживает запуск через Python.')

    script_path = build_update_script(target_path, pending_path, target_path)
    show_temporary_message('ОБНОВЛЕНИЕ', f'УСТАНОВКА {pending_version}', duration=2)
    logging.info(f"Запущена установка обновления {pending_version} из {pending_path} в {target_path}")
    subprocess.Popen(['cmd', '/c', script_path], close_fds=True)
    return True



def apply_pending_update_on_startup():
    pending_path = cfg.get('pending_update_path', '')
    pending_version = cfg.get('pending_update_version', '')
    if not pending_path or not os.path.exists(pending_path):
        return False

    logging.info(f"Найдена отложенная установка версии {pending_version}")
    animator = StatusAnimator('ОБНОВЛЕНИЕ', lambda: f'СТАРТ {pending_version}')
    animator.start()
    try:
        schedule_pending_update_install()
    finally:
        animator.stop()
    return True



def announce_version_change():
    previous_version = sanitize_version(cfg.get('app_version', ''))
    current_version = sanitize_version(APP_VERSION)
    if previous_version == current_version:
        return

    notes = format_release_notes(cfg.get('pending_update_notes') or cfg.get('last_changelog'))
    cfg['last_changelog_version'] = current_version
    cfg['last_changelog'] = notes
    cfg['app_version'] = current_version
    cfg['known_latest_version'] = max(current_version, sanitize_version(cfg.get('known_latest_version', current_version)), key=parse_version_parts)
    cfg['pending_update_version'] = ''
    cfg['pending_update_path'] = ''
    cfg['pending_update_target'] = ''
    cfg['pending_update_notes'] = ''
    cfg['update_failure_count'] = 0
    cfg['last_update_error'] = ''
    cfg['last_alerted_version'] = ''
    save_config(cfg)
    remember_release_entry(
        version=current_version,
        notes=notes,
        release_url=cfg.get('last_release_url', '') or get_release_page_url(current_version),
    )

    logging.info("Установлена новая версия %s (была %s)", current_version, previous_version or 'неизвестно')
    for line in notes.splitlines():
        logging.info("CHANGELOG %s", line)

    preview = notes.splitlines()[0][:LINE_WIDTH] if notes else 'CHANGES В ЛОГЕ'
    show_temporary_message('НОВАЯ ВЕРСИЯ', preview, duration=4)



def get_latest_release():
    repo = extract_repo_slug(cfg.get('github_repo', ''))
    if not repo:
        raise RuntimeError('В конфиге не указан github_repo')
    url = UPDATE_API_BASE.format(repo=repo)
    release = perform_json_request(url)
    version = sanitize_version(release.get('tag_name') or release.get('name') or '')
    if not version:
        raise RuntimeError('GitHub не вернул номер версии релиза')
    notes = format_release_notes(release.get('body'))
    asset = pick_release_asset(release)
    release_url = release.get('html_url') or get_release_page_url(version)
    published_at = release.get('published_at') or release.get('created_at') or ''
    remember_release_entry(
        version=version,
        notes=notes,
        release_url=release_url,
        published_at=published_at,
    )
    return {
        'version': version,
        'notes': notes,
        'asset_name': asset.get('name', 'release.bin'),
        'asset_url': asset.get('browser_download_url', ''),
        'asset_size': int(asset.get('size') or 0),
        'release_url': release_url,
        'published_at': published_at,
    }



def download_update_asset(release_info, progress_callback=None):
    url = release_info['asset_url']
    if not url:
        raise RuntimeError('Для релиза не найден URL файла обновления')

    version = release_info['version']
    asset_name = release_info['asset_name']
    target_name = os.path.basename(sys.executable if getattr(sys, 'frozen', False) else __file__)
    ext = os.path.splitext(asset_name)[1] or os.path.splitext(target_name)[1] or '.bin'
    final_path = os.path.join(UPDATES_DIR, f'vfd_monitor_{version}{ext}')
    tmp_path = final_path + '.part'

    headers = {
        'User-Agent': f'vfd-monitor/{APP_VERSION}',
        'Accept': 'application/octet-stream',
    }
    last_error = None
    for attempt in range(1, UPDATE_ATTEMPTS + 1):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=UPDATE_TIMEOUT) as response, open(tmp_path, 'wb') as file:
                total = int(response.headers.get('Content-Length', release_info.get('asset_size') or 0))
                downloaded = 0
                while True:
                    chunk = response.read(UPDATE_DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    file.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback:
                        progress_callback(downloaded, total)
            if os.path.getsize(tmp_path) == 0:
                raise RuntimeError('Скачанный файл обновления пустой')
            shutil.move(tmp_path, final_path)
            return final_path
        except Exception as exc:
            last_error = exc
            logging.warning(f"Попытка {attempt}/{UPDATE_ATTEMPTS} скачивания обновления не удалась: {exc}")
            try:
                os.remove(tmp_path)
            except FileNotFoundError:
                pass
            time.sleep(min(attempt * 1.5, 6))

    raise RuntimeError(f'Не удалось скачать обновление: {last_error}')



def check_for_updates(force=False):
    with update_check_lock:
        if not force and time.time() - float(cfg.get('last_update_check', 0.0)) < UPDATE_POLL_INTERVAL:
            return None

        animator = StatusAnimator('ОБНОВЛЕНИЕ', lambda: 'ПРОВЕРКА GITHUB').start()
        try:
            release = get_latest_release()
            cfg['known_latest_version'] = release['version']
            cfg['last_release_url'] = release.get('release_url', '')
            cfg['last_update_check'] = time.time()
            cfg['last_update_error'] = ''
            save_config(cfg)
            refresh_release_history()
        except Exception as exc:
            cfg['last_update_check'] = time.time()
            register_update_failure(
                version=cfg.get('known_latest_version', ''),
                release_url=cfg.get('last_release_url', '') or get_release_page_url(cfg.get('known_latest_version', '')),
                error_text=str(exc),
            )
            logging.error(f"Проверка обновлений завершилась ошибкой: {exc}")
            raise
        finally:
            animator.stop()

        current_version = sanitize_version(APP_VERSION)
        pending_version = sanitize_version(cfg.get('pending_update_version', ''))
        if pending_version and not os.path.exists(cfg.get('pending_update_path', '')):
            cfg['pending_update_version'] = ''
            cfg['pending_update_path'] = ''
            cfg['pending_update_target'] = ''
            cfg['pending_update_notes'] = ''
            save_config(cfg)
            pending_version = ''

        if not is_newer_version(release['version'], current_version):
            clear_update_failure_state()
            return None

        if release['version'] == pending_version and os.path.exists(cfg.get('pending_update_path', '')):
            clear_update_failure_state()
            return None

        if is_newer_version(release['version'], current_version) and release['version'] != pending_version:
            return release
        return None



def download_and_stage_update(release_info):
    with update_download_lock:
        progress = {'downloaded': 0, 'total': 0}

        def on_progress(downloaded, total):
            progress['downloaded'] = downloaded
            progress['total'] = total

        def subtitle():
            total = progress['total']
            downloaded_mb = progress['downloaded'] / (1024 * 1024)
            if total > 0:
                ratio = max(0.0, min(progress['downloaded'] / total, 1.0))
                percent = int(ratio * 100)
                total_mb = total / (1024 * 1024)
                return f"{percent:3d}% {downloaded_mb:4.1f}/{total_mb:4.1f}M"
            return f"{downloaded_mb:4.1f} MB"

        animator = StatusAnimator('СКАЧИВАНИЕ', subtitle).start()
        try:
            final_path = download_update_asset(release_info, progress_callback=on_progress)
        except Exception as exc:
            register_update_failure(
                version=release_info.get('version', ''),
                release_url=release_info.get('release_url', ''),
                error_text=str(exc),
            )
            raise
        finally:
            animator.stop()

        cfg['pending_update_version'] = release_info['version']
        cfg['pending_update_path'] = final_path
        cfg['pending_update_target'] = get_release_target_path(release_info)
        cfg['pending_update_notes'] = release_info['notes']
        cfg['known_latest_version'] = release_info['version']
        cfg['last_release_url'] = release_info.get('release_url', '')
        clear_update_failure_state()
        save_config(cfg)
        remember_release_entry(
            version=release_info['version'],
            notes=release_info['notes'],
            release_url=release_info.get('release_url', ''),
            published_at=release_info.get('published_at', ''),
        )

        logging.info("Скачано обновление %s -> %s", release_info['version'], final_path)
        show_temporary_message('ОБНОВЛЕНИЕ ГОТОВО', f"V{release_info['version']}", duration=4)
        return final_path



def update_worker_loop():
    while app_running:
        try:
            release = check_for_updates(force=False)
            if release:
                download_and_stage_update(release)
        except Exception as exc:
            logging.error(f"Фоновое обновление завершилось ошибкой: {exc}")
        for _ in range(int(UPDATE_POLL_INTERVAL / 5)):
            if not app_running:
                break
            time.sleep(5)



def open_changelog_window(icon=None, item=None):
    def worker():
        import tkinter as tk
        from tkinter import scrolledtext

        try:
            history = refresh_release_history()
        except Exception:
            history = sanitize_release_history(cfg.get('release_history'))

        root = tk.Tk()
        root.title('Changelog VFD Monitor')
        root.geometry('760x520')
        root.attributes('-topmost', True)

        text = scrolledtext.ScrolledText(root, wrap='word', font=('Consolas', 10))
        text.pack(fill='both', expand=True)
        text.insert('1.0', build_changelog_text(history))
        text.configure(state='disabled')
        root.mainloop()

    threading.Thread(target=worker, daemon=True).start()



def manual_check_updates(icon=None, item=None):
    def worker():
        manual_update_feedback['status'] = 'running'
        manual_update_feedback['details'] = ''
        try:
            release = check_for_updates(force=True)
            if release:
                download_and_stage_update(release)
                manual_update_feedback['status'] = 'ready'
                manual_update_feedback['details'] = f"Скачана версия {release['version']}"
            else:
                manual_update_feedback['status'] = 'latest'
                manual_update_feedback['details'] = f"Уже используется актуальная версия {APP_VERSION}"
                show_temporary_message('ОБНОВЛЕНИЕ', 'УЖЕ АКТУАЛЬНО', duration=3)
        except Exception as exc:
            manual_update_feedback['status'] = 'error'
            manual_update_feedback['details'] = str(exc)
            show_temporary_message('ОБНОВЛЕНИЕ', 'ОШИБКА ПРОВЕРКИ', duration=3)

    threading.Thread(target=worker, daemon=True).start()


class POINT(Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
        ("lPrivate", wintypes.DWORD),
    ]


user32 = WinDLL('user32', use_last_error=True)
kernel32 = WinDLL('kernel32', use_last_error=True)


def parse_hotkey(hotkey_text):
    hotkey = canonicalize_hotkey(hotkey_text)
    if not hotkey:
        return None

    parts = hotkey.split('+')
    modifiers = 0
    key_part = ''
    for part in parts:
        upper = part.upper()
        if upper == 'CTRL':
            modifiers |= MOD_CONTROL
        elif upper == 'ALT':
            modifiers |= MOD_ALT
        elif upper == 'SHIFT':
            modifiers |= MOD_SHIFT
        elif upper == 'WIN':
            modifiers |= MOD_WIN
        else:
            key_part = upper

    if not key_part:
        return None

    vk = VK_NAME_MAP.get(key_part.upper())
    if vk is None:
        return None
    return modifiers, vk, hotkey


def format_hotkey_preview(value):
    return canonicalize_hotkey(value) or 'Не назначено'


class HotkeyManager:
    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
        self.thread_id = None
        self.registered = {}
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def stop(self):
        with self.lock:
            self.stop_event.set()
            if self.thread_id:
                user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            thread = self.thread
        if thread:
            thread.join(timeout=2)
        with self.lock:
            self.thread = None
            self.thread_id = None
            self.registered = {}

    def reload(self):
        self.stop()
        self.start()

    def _unregister_all(self):
        for hotkey_id in list(self.registered):
            try:
                user32.UnregisterHotKey(None, hotkey_id)
            except Exception:
                pass
        self.registered = {}

    def _register_configured_hotkeys(self):
        self._unregister_all()
        if not cfg.get("hotkeys_enabled", True):
            logging.info("Горячие клавиши отключены в конфиге")
            return

        hotkeys = cfg.get("hotkeys", {})
        used = {}
        next_hotkey_id = 1
        for action, callback in HOTKEY_CALLBACKS.items():
            parsed = parse_hotkey(hotkeys.get(action, ''))
            if not parsed:
                logging.warning("Горячая клавиша для действия '%s' не задана или некорректна", action)
                continue
            modifiers, vk, normalized = parsed
            key_signature = (modifiers, vk)
            if key_signature in used:
                logging.warning(
                    "Горячая клавиша %s уже назначена для '%s', действие '%s' пропущено",
                    normalized,
                    used[key_signature],
                    action,
                )
                continue
            if not user32.RegisterHotKey(None, next_hotkey_id, modifiers, vk):
                logging.warning("Не удалось зарегистрировать горячую клавишу %s", normalized)
                continue
            self.registered[next_hotkey_id] = callback
            used[key_signature] = action
            next_hotkey_id += 1

    def _run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        self._register_configured_hotkeys()
        msg = MSG()
        while not self.stop_event.is_set():
            result = user32.GetMessageW(byref(msg), None, 0, 0)
            if result <= 0:
                break
            if msg.message == WM_HOTKEY:
                callback = self.registered.get(int(msg.wParam))
                if callback:
                    threading.Thread(target=callback, daemon=True).start()
            user32.TranslateMessage(byref(msg))
            user32.DispatchMessageW(byref(msg))
        self._unregister_all()


def execute_hotkey_toggle_display():
    toggle_display(None, None)


def execute_hotkey_check_updates():
    manual_check_updates(None, None)


def execute_hotkey_show_changelog():
    open_changelog_window(None, None)


def execute_hotkey_open_notes_window():
    show_notes_window(None, None)


def execute_hotkey_open_reminders_window():
    show_reminders_window(None, None)


def execute_hotkey_next_overlay_item():
    cycle_overlay_item(1)


def execute_hotkey_prev_overlay_item():
    cycle_overlay_item(-1)


def execute_hotkey_hide_overlay_item():
    hide_active_overlay_item()


def execute_hotkey_complete_overlay_item():
    complete_active_overlay_item()


HOTKEY_CALLBACKS.update({
    "toggle_display": execute_hotkey_toggle_display,
    "check_updates": execute_hotkey_check_updates,
    "show_changelog": execute_hotkey_show_changelog,
    "open_notes_window": execute_hotkey_open_notes_window,
    "open_reminders_window": execute_hotkey_open_reminders_window,
    "next_overlay_item": execute_hotkey_next_overlay_item,
    "prev_overlay_item": execute_hotkey_prev_overlay_item,
    "hide_overlay_item": execute_hotkey_hide_overlay_item,
    "complete_overlay_item": execute_hotkey_complete_overlay_item,
})


def reload_hotkeys():
    if hotkey_manager is not None:
        hotkey_manager.reload()
    refresh_menu()


def open_config_file(icon=None, item=None):
    try:
        os.startfile(CONFIG_PATH)
    except Exception:
        pass


def toggle_hotkeys(icon, item):
    cfg["hotkeys_enabled"] = not cfg.get("hotkeys_enabled", True)
    save_config(cfg)
    reload_hotkeys()
    refresh_menu(icon)


def open_hotkeys_settings(icon=None, item=None):
    app_hotkeys.open_hotkeys_settings_window(
        config_getter=lambda: cfg,
        save_callback=save_config,
        reload_callback=reload_hotkeys,
        open_config_callback=open_config_file,
    )


def telegram_api_request(method, params=None, timeout=TELEGRAM_REQUEST_TIMEOUT):
    token = (alert_manager.get_state_snapshot().get('bot_token') if alert_manager is not None else '') or ''
    if not token:
        raise RuntimeError('Не задан token Telegram бота.')
    encoded_params = urllib.parse.urlencode(params or {})
    url = TELEGRAM_API_BASE.format(token=token, method=method)
    if encoded_params:
        url = f"{url}?{encoded_params}"
    request = urllib.request.Request(url, headers={'User-Agent': f'VFD-Monitor/{APP_VERSION}'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode('utf-8'))
    if not payload.get('ok'):
        raise RuntimeError(payload.get('description') or 'Telegram API вернул ошибку.')
    return payload.get('result')


def update_telegram_bot_profile():
    if alert_manager is None or not alert_manager.is_configured():
        return
    try:
        profile = telegram_api_request('getMe', timeout=15)
    except Exception as exc:
        logging.warning('Не удалось получить профиль Telegram-бота: %s', exc)
        return
    username = profile.get('username') or ''
    if username:
        alert_manager.set_bot_username(username)


def handle_telegram_update(update):
    if not isinstance(update, dict):
        return
    update_id = int(update.get('update_id') or 0)
    message = update.get('message') or update.get('edited_message') or update.get('channel_post') or update.get('edited_channel_post')
    if not isinstance(message, dict):
        alert_manager.set_last_update_id(update_id + 1)
        return
    alert = build_alert_from_telegram_message(message, update_id=update_id)
    if alert is None:
        alert_manager.set_last_update_id(update_id + 1)
        return
    if not alert_manager.is_sender_allowed(alert.get('author_username', ''), alert.get('author_id', '')):
        logging.info('Telegram alert от %s отклонён whitelist.', alert.get('author_name'))
        alert_manager.set_last_update_id(update_id + 1)
        return
    enqueued = alert_manager.enqueue_alert(alert)
    alert_manager.set_last_update_id(update_id + 1)
    preview = trim_vfd_text(enqueued.get('author_name') or 'ALERT', LINE_WIDTH)
    show_temporary_message('ALERT', preview, duration=1.2)
    refresh_menu()


def toggle_alerts(icon=None, item=None):
    if alert_manager is None:
        return
    snapshot = alert_manager.get_state_snapshot()
    alert_manager.set_enabled(not snapshot.get('enabled', False))
    state = alert_manager.get_state_snapshot()
    if state.get('enabled') and not state.get('bot_token'):
        show_temporary_message('ALERTS', 'НУЖЕН TOKEN', duration=1.8)
    elif state.get('enabled'):
        show_temporary_message('ALERTS', 'TELEGRAM ON', duration=1.8)
    else:
        show_temporary_message('ALERTS', 'TELEGRAM OFF', duration=1.8)
    refresh_menu(icon)


def toggle_alerts_whitelist(icon=None, item=None):
    if alert_manager is None:
        return
    snapshot = alert_manager.get_state_snapshot()
    alert_manager.update_settings(use_whitelist=not snapshot.get('use_whitelist', False))
    state = alert_manager.get_state_snapshot()
    show_temporary_message('WHITELIST', 'ON' if state.get('use_whitelist') else 'OFF', duration=1.5)
    refresh_menu(icon)


def telegram_alert_worker_loop():
    with telegram_poll_state_lock:
        telegram_poll_state['announced_waiting'] = False

    while app_running:
        try:
            state = alert_manager.get_state_snapshot()
            enabled = state.get('enabled', False)
            token = state.get('bot_token', '')
            poll_interval = float(state.get('poll_interval_seconds', 2.0) or 2.0)
            if not enabled:
                with telegram_poll_state_lock:
                    telegram_poll_state['announced_waiting'] = False
                time.sleep(1.0)
                continue
            if not token:
                alert_manager.set_last_error('Telegram alerts включены, но bot token пустой.')
                with telegram_poll_state_lock:
                    telegram_poll_state['announced_waiting'] = False
                time.sleep(2.0)
                continue

            if not state.get('bot_username'):
                update_telegram_bot_profile()
            with telegram_poll_state_lock:
                if not telegram_poll_state.get('announced_waiting'):
                    logging.info('Telegram alerts активированы, ожидание сообщений.')
                    telegram_poll_state['announced_waiting'] = True
            offset = int(state.get('last_update_id', 0) or 0)
            updates = telegram_api_request(
                'getUpdates',
                params={
                    'timeout': TELEGRAM_LONG_POLL_TIMEOUT,
                    'offset': offset,
                    'allowed_updates': json.dumps(['message', 'edited_message', 'channel_post', 'edited_channel_post']),
                },
                timeout=TELEGRAM_REQUEST_TIMEOUT,
            ) or []
            alert_manager.clear_last_error()
            if not updates:
                continue
            for update in updates:
                handle_telegram_update(update)
        except Exception as exc:
            logging.exception('Ошибка Telegram alerts worker')
            alert_manager.set_last_error(str(exc))
            time.sleep(max(2.0, poll_interval if 'poll_interval' in locals() else 2.0))


# --- ИНТЕРФЕЙС И ТРЕЙ ---
def toggle_autostart(icon, item):
    cfg["autostart"] = not cfg["autostart"]
    save_config(cfg)
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
        if cfg["autostart"]:
            winreg.SetValueEx(key, "VFD_Monitor", 0, winreg.REG_SZ, get_autostart_command())
        else:
            try:
                winreg.DeleteValue(key, "VFD_Monitor")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        logging.error(f"Ошибка автозагрузки: {e}")
    refresh_menu(icon)



def toggle_display(icon, item):
    global is_display_on
    is_display_on = not is_display_on
    if not is_display_on:
        try:
            write_serial(CLR)
        except Exception:
            pass
    refresh_menu(icon)



def make_speed_setter(value):
    def setter(icon, item):
        global current_interval
        current_interval = value
        cfg["update_interval"] = value
        save_config(cfg)
        refresh_menu(icon)
    return setter



def make_metric_toggle(config_key):
    def toggle(icon, item):
        cfg[config_key] = not cfg[config_key]
        save_config(cfg)
        refresh_menu(icon)
    return toggle



def is_metric_enabled(config_key):
    return cfg.get(config_key, False)



def set_custom_speed(icon, item):
    def prompt():
        import tkinter as tk
        from tkinter import messagebox, simpledialog

        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        try:
            value = simpledialog.askfloat(
                "Скорость",
                "Интервал обновления (в секундах):",
                parent=root,
                minvalue=0.1,
            )
            if value is not None:
                global current_interval
                current_interval = value
                cfg["update_interval"] = value
                save_config(cfg)
                refresh_menu(icon)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось изменить скорость: {e}")
        finally:
            root.destroy()

    threading.Thread(target=prompt, daemon=True).start()



def open_logs(icon, item):
    try:
        os.startfile(LOG_PATH)
    except Exception:
        pass



def install_update_and_exit(icon=None, item=None):
    global app_running
    if not cfg.get('pending_update_path') or not os.path.exists(cfg.get('pending_update_path', '')):
        show_temporary_message('ОБНОВЛЕНИЕ', 'НЕТ СКАЧАННОГО', duration=3)
        return
    app_running = False
    if hotkey_manager is not None:
        hotkey_manager.stop()
    if tray_icon is not None:
        tray_icon.stop()
    schedule_pending_update_install()
    show_farewell()
    sys.exit(0)



def exit_app(icon, item):
    global app_running
    app_running = False
    if hotkey_manager is not None:
        hotkey_manager.stop()
    if cfg.get('pending_update_path') and os.path.exists(cfg.get('pending_update_path', '')):
        schedule_pending_update_install()
    icon.stop()
    show_farewell()
    sys.exit(0)



def create_image():
    image = Image.new('RGB', (64, 64), color=(0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 16, 48, 48), fill=(0, 255, 128))
    return image


# --- СБОР СТАТИСТИКИ ---
def clamp_percent(value):
    try:
        percent = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, min(percent, 99))


def clamp_temperature(value):
    try:
        temp = float(value)
    except (TypeError, ValueError):
        return None
    if 10 <= temp <= 110:
        return max(0, min(int(temp), 99))
    return None


def log_sensor_source_once(kind, source_name):
    if not source_name:
        return
    with sensor_cache_lock:
        logged = sensor_runtime_cache.setdefault("logged_sources", set())
        key = f"{kind}:{source_name}"
        if key in logged:
            return
        logged.add(key)
    logging.info(f"Источник {kind}: {source_name}")


def log_sensor_failure_once(kind, source_name, reason):
    if not source_name or not reason:
        return
    message = str(reason).strip()
    if not message:
        return
    with sensor_cache_lock:
        logged = sensor_runtime_cache.setdefault("logged_failures", set())
        key = f"{kind}:{source_name}:{message}"
        if key in logged:
            return
        logged.add(key)
    logging.warning(f"Не удалось получить {kind} через {source_name}: {message}")


def open_wmi_namespace(namespace):
    try:
        return wmi.WMI(namespace=namespace)
    except Exception as exc:
        logging.warning(f"WMI namespace {namespace} недоступен: {exc}")
        return None


def init_sensor_context():
    return {
        "wmi_acpi": open_wmi_namespace("root\\wmi"),
        "wmi_gpu_perf": open_wmi_namespace("root\\cimv2"),
        "wmi_openhardware": open_wmi_namespace("root\\OpenHardwareMonitor"),
        "wmi_librehardware": open_wmi_namespace("root\\LibreHardwareMonitor"),
        "nvidia_smi_path": shutil.which("nvidia-smi") or shutil.which("nvidia-smi.exe"),
    }


def collect_sensor_candidates(sensor_row):
    candidates = []
    for attr in ("Name", "Identifier", "Parent", "SensorType", "Hardware", "HardwareType"):
        value = getattr(sensor_row, attr, None)
        if value:
            candidates.append(str(value))
    return " ".join(candidates).lower()


def score_cpu_sensor_text(text):
    score = 0
    if any(token in text for token in ("cpu package", "package", "tctl", "tdie", "cpu die", "die")):
        score += 60
    if any(token in text for token in ("ccd", "core average", "average")):
        score += 40
    if "core" in text:
        score += 20
    if "cpu" in text or "/amdcpu/" in text or "/intelcpu/" in text:
        score += 15
    if any(token in text for token in ("gpu", "pch", "vrm", "motherboard", "system", "ambient")):
        score -= 80
    return score


def get_hardware_monitor_temperature(wmi_obj, target):
    if not wmi_obj:
        return None, None, "namespace недоступен"
    try:
        sensors = wmi_obj.Sensor()
    except Exception as exc:
        return None, None, f"ошибка WMI-запроса Sensor(): {exc}"

    best_temp = None
    best_score = None
    matched_target = False
    invalid_values = 0
    for sensor in sensors:
        sensor_type = str(getattr(sensor, "SensorType", "") or "").lower()
        if sensor_type != "temperature":
            continue

        text = collect_sensor_candidates(sensor)
        if target == "cpu":
            if "cpu" not in text and "/amdcpu/" not in text and "/intelcpu/" not in text:
                continue
            matched_target = True
            score = score_cpu_sensor_text(text)
        else:
            if "gpu" not in text:
                continue
            matched_target = True
            score = 50
            if any(token in text for token in ("core", "edge", "hot spot", "hotspot", "junction", "gpu temperature")):
                score += 30
            if any(token in text for token in ("memory", "mem", "vrm")):
                score -= 40

        temp = clamp_temperature(getattr(sensor, "Value", None))
        if temp is None:
            invalid_values += 1
            continue
        if best_score is None or score > best_score:
            best_score = score
            best_temp = temp

    if best_temp is not None:
        return best_temp, best_score, None
    if matched_target:
        if invalid_values:
            return None, None, "подходящие температурные датчики найдены, но значения пустые или вне диапазона"
        return None, None, "подходящие температурные датчики найдены, но не удалось выбрать валидное значение"
    return None, None, f"не найдено подходящих {target.upper()}-температурных датчиков"


def get_psutil_cpu_temp():
    try:
        sensors = psutil.sensors_temperatures(fahrenheit=False)
    except (AttributeError, NotImplementedError):
        return None, "psutil.sensors_temperatures() не поддерживается в текущей среде"
    except Exception as exc:
        return None, f"ошибка psutil.sensors_temperatures(): {exc}"

    best_temp = None
    best_score = None
    seen_entries = 0
    for name, entries in sensors.items():
        name_text = str(name or "").lower()
        for entry in entries:
            seen_entries += 1
            text = " ".join(
                part.lower()
                for part in (
                    name_text,
                    getattr(entry, "label", "") or "",
                )
                if part
            )
            score = score_cpu_sensor_text(text)
            temp = clamp_temperature(getattr(entry, "current", None))
            if temp is None:
                continue
            if best_score is None or score > best_score:
                best_score = score
                best_temp = temp
    if best_temp is not None:
        return best_temp, None
    if seen_entries:
        return None, "датчики psutil найдены, но не содержат валидной температуры CPU"
    return None, "psutil не вернул ни одного температурного датчика"


def get_acpi_cpu_temp(wmi_obj):
    if not wmi_obj:
        return None, "namespace root\\wmi недоступен"
    try:
        zones = wmi_obj.MSAcpi_ThermalZoneTemperature()
    except Exception as exc:
        return None, f"ошибка запроса MSAcpi_ThermalZoneTemperature(): {exc}"

    invalid_values = 0
    for zone in zones:
        raw_value = getattr(zone, "CurrentTemperature", None)
        if raw_value is None:
            continue
        temp = clamp_temperature((raw_value / 10.0) - 273.15)
        if temp is not None:
            return temp, None
        invalid_values += 1
    if invalid_values:
        return None, "ACPI-зоны найдены, но температуры вне допустимого диапазона"
    return None, "ACPI не вернул доступных thermal zone датчиков"


def get_cpu_temp(sensor_context):
    for namespace_key, namespace_name in (
        ("wmi_librehardware", "LibreHardwareMonitor WMI"),
        ("wmi_openhardware", "OpenHardwareMonitor WMI"),
    ):
        temp, score, reason = get_hardware_monitor_temperature(sensor_context.get(namespace_key), "cpu")
        if temp is not None and score is not None and score >= 0:
            log_sensor_source_once("температуры CPU", namespace_name)
            return temp
        log_sensor_failure_once("температуры CPU", namespace_name, reason)

    temp, reason = get_psutil_cpu_temp()
    if temp is not None:
        log_sensor_source_once("температуры CPU", "psutil sensors_temperatures")
        return temp
    log_sensor_failure_once("температуры CPU", "psutil sensors_temperatures", reason)

    temp, reason = get_acpi_cpu_temp(sensor_context.get("wmi_acpi"))
    if temp is not None:
        log_sensor_source_once("температуры CPU", "ACPI thermal zone")
        return temp
    log_sensor_failure_once("температуры CPU", "ACPI thermal zone", reason)

    return None


def parse_nvidia_smi_query(path):
    if not path:
        return None, None, {
            "usage": "nvidia-smi не найден в PATH",
            "temp": "nvidia-smi не найден в PATH",
        }
    now = time.time()
    with sensor_cache_lock:
        cached = sensor_runtime_cache.setdefault("nvidia_smi", {"timestamp": 0.0, "usage": None, "temp": None})
        if now - cached["timestamp"] < 2.0:
            return cached["usage"], cached["temp"], {
                "usage": "данные отсутствуют в кэше nvidia-smi",
                "temp": "данные отсутствуют в кэше nvidia-smi",
            }

    startupinfo = None
    if os.name == "nt" and hasattr(subprocess, "STARTUPINFO"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    try:
        result = subprocess.run(
            [
                path,
                "--query-gpu=utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
            startupinfo=startupinfo,
        )
    except Exception as exc:
        return None, None, {
            "usage": f"ошибка запуска nvidia-smi: {exc}",
            "temp": f"ошибка запуска nvidia-smi: {exc}",
        }

    usage_values = []
    temp_values = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2:
            continue
        usage = clamp_percent(parts[0])
        temp = clamp_temperature(parts[1])
        if usage is not None:
            usage_values.append(usage)
        if temp is not None:
            temp_values.append(temp)

    usage = max(usage_values) if usage_values else None
    temp = max(temp_values) if temp_values else None
    with sensor_cache_lock:
        cached = sensor_runtime_cache.setdefault("nvidia_smi", {"timestamp": 0.0, "usage": None, "temp": None})
        cached.update({"timestamp": now, "usage": usage, "temp": temp})
    return usage, temp, {
        "usage": None if usage is not None else "nvidia-smi отработал, но не вернул валидную загрузку GPU",
        "temp": None if temp is not None else "nvidia-smi отработал, но не вернул валидную температуру GPU",
    }


def get_nvml_gpu_metrics():
    try:
        device_count = nvml.nvmlDeviceGetCount()
    except Exception as exc:
        return None, None, {
            "usage": f"NVML недоступен: {exc}",
            "temp": f"NVML недоступен: {exc}",
        }

    usage_values = []
    temp_values = []
    usage_errors = []
    temp_errors = []
    for index in range(device_count):
        try:
            handle = nvml.nvmlDeviceGetHandleByIndex(index)
        except Exception as exc:
            usage_errors.append(f"GPU {index}: handle недоступен ({exc})")
            temp_errors.append(f"GPU {index}: handle недоступен ({exc})")
            continue
        try:
            usage = clamp_percent(nvml.nvmlDeviceGetUtilizationRates(handle).gpu)
            if usage is not None:
                usage_values.append(usage)
            else:
                usage_errors.append(f"GPU {index}: NVML вернул пустую/некорректную загрузку")
        except Exception as exc:
            usage_errors.append(f"GPU {index}: nvmlDeviceGetUtilizationRates() -> {exc}")
        try:
            temp = clamp_temperature(nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU))
            if temp is not None:
                temp_values.append(temp)
            else:
                temp_errors.append(f"GPU {index}: NVML вернул пустую/некорректную температуру")
        except Exception as exc:
            temp_errors.append(f"GPU {index}: nvmlDeviceGetTemperature() -> {exc}")

    return (
        max(usage_values) if usage_values else None,
        max(temp_values) if temp_values else None,
        {
            "usage": None if usage_values else ("; ".join(usage_errors) if usage_errors else "NVML не вернул ни одной загрузки GPU"),
            "temp": None if temp_values else ("; ".join(temp_errors) if temp_errors else "NVML не вернул ни одной температуры GPU"),
        },
    )


def get_windows_gpu_usage(wmi_obj):
    if not wmi_obj:
        return None, "namespace root\\cimv2 недоступен"
    try:
        engines = wmi_obj.Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine()
    except Exception as exc:
        return None, f"ошибка запроса Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine(): {exc}"

    usage_values = []
    matching_engines = 0
    for engine in engines:
        name = str(getattr(engine, "Name", "") or "").lower()
        if "engtype_" not in name:
            continue
        if any(token in name for token in ("engtype_3d", "engtype_compute", "engtype_cuda", "engtype_copy", "engtype_video")):
            matching_engines += 1
            usage = clamp_percent(getattr(engine, "UtilizationPercentage", None))
            if usage is not None:
                usage_values.append(usage)

    if not usage_values:
        if matching_engines:
            return None, "GPU Engine Counters найдены, но не содержат валидной загрузки"
        return None, "Windows не вернул подходящих GPU Engine Counters"
    return max(usage_values), None


def get_gpu_monitor_temperature(sensor_context):
    for namespace_key, namespace_name in (
        ("wmi_librehardware", "LibreHardwareMonitor WMI"),
        ("wmi_openhardware", "OpenHardwareMonitor WMI"),
    ):
        temp, _, reason = get_hardware_monitor_temperature(sensor_context.get(namespace_key), "gpu")
        if temp is not None:
            log_sensor_source_once("температуры GPU", namespace_name)
            return temp
        log_sensor_failure_once("температуры GPU", namespace_name, reason)
    return None


def get_gpu_metrics(sensor_context):
    if not cfg.get("show_gpu_usage", True) and not cfg.get("show_gpu_temp", True):
        return None, None

    usage = None
    temp = None

    nvml_usage, nvml_temp, nvml_reasons = get_nvml_gpu_metrics()
    if nvml_usage is not None:
        usage = nvml_usage
        log_sensor_source_once("загрузки GPU", "NVML")
    else:
        log_sensor_failure_once("загрузки GPU", "NVML", nvml_reasons.get("usage"))
    if nvml_temp is not None:
        temp = nvml_temp
        log_sensor_source_once("температуры GPU", "NVML")
    else:
        log_sensor_failure_once("температуры GPU", "NVML", nvml_reasons.get("temp"))

    if usage is None or temp is None:
        cli_usage, cli_temp, cli_reasons = parse_nvidia_smi_query(sensor_context.get("nvidia_smi_path"))
        if usage is None and cli_usage is not None:
            usage = cli_usage
            log_sensor_source_once("загрузки GPU", "nvidia-smi")
        elif usage is None:
            log_sensor_failure_once("загрузки GPU", "nvidia-smi", cli_reasons.get("usage"))
        if temp is None and cli_temp is not None:
            temp = cli_temp
            log_sensor_source_once("температуры GPU", "nvidia-smi")
        elif temp is None:
            log_sensor_failure_once("температуры GPU", "nvidia-smi", cli_reasons.get("temp"))

    if usage is None:
        perf_usage, reason = get_windows_gpu_usage(sensor_context.get("wmi_gpu_perf"))
        if perf_usage is not None:
            usage = perf_usage
            log_sensor_source_once("загрузки GPU", "Windows GPU Engine Counters")
        else:
            log_sensor_failure_once("загрузки GPU", "Windows GPU Engine Counters", reason)

    if temp is None:
        temp = get_gpu_monitor_temperature(sensor_context)

    return usage, temp



def fmt_v(value):
    return vfd_display.fmt_v(value)



def find_vfd():
    chips = ["Prolific", "USB-to-Serial", "FTDI", "Posiflex", "Atol"]
    for port in serial.tools.list_ports.comports():
        desc = (port.description + port.hwid).lower()
        if any(chip.lower() in desc for chip in chips):
            return port.device
    return None



def get_metric_templates(metric_name):
    return vfd_display.get_metric_templates(cfg.get("metric_formats", {}), DEFAULT_METRIC_FORMATS, metric_name)


def apply_template(template, **kwargs):
    return vfd_display.apply_template(template, **kwargs)


def build_usage_options(metric_name, full_prefix, short_prefix, percent, temp, show_usage, show_temp):
    return vfd_display.build_usage_options(
        cfg.get("metric_formats", {}),
        DEFAULT_METRIC_FORMATS,
        metric_name,
        full_prefix,
        short_prefix,
        percent,
        temp,
        show_usage,
        show_temp,
        DEG_CHAR,
    )



def render_segments(segment_options, width=LINE_WIDTH, separator=' ', compact_separator=''):
    return vfd_display.render_segments(
        segment_options,
        width=width,
        separator=separator,
        compact_separator=compact_separator,
        special_chars=SPECIAL_CHARS,
    )



def build_primary_segments(cpu_percent, cpu_temp, gpu_percent, gpu_temp, ram_percent):
    return vfd_display.build_primary_segments(cfg, DEFAULT_METRIC_FORMATS, cpu_percent, cpu_temp, gpu_percent, gpu_temp, ram_percent, DEG_CHAR)



def build_io_segments(disk_read, disk_write, net_in, net_out):
    return vfd_display.build_io_segments(cfg, DEFAULT_METRIC_FORMATS, disk_read, disk_write, net_in, net_out)



def build_line1(cpu_percent, cpu_temp, gpu_percent, gpu_temp, ram_percent):
    return vfd_display.build_line1(
        cfg,
        DEFAULT_LINE_SPACING,
        DEFAULT_METRIC_FORMATS,
        SPECIAL_CHARS,
        DEG_CHAR,
        cpu_percent,
        cpu_temp,
        gpu_percent,
        gpu_temp,
        ram_percent,
        width=LINE_WIDTH,
    )



def build_line2(disk_read, disk_write, net_in, net_out):
    return vfd_display.build_line2(
        cfg,
        DEFAULT_LINE_SPACING,
        DEFAULT_METRIC_FORMATS,
        SPECIAL_CHARS,
        disk_read,
        disk_write,
        net_in,
        net_out,
        width=LINE_WIDTH,
    )



def create_metrics_sampler(sensor_context):
    return RuntimeMetricsSampler(
        cpu_percent_fn=lambda: min(int(psutil.cpu_percent(interval=None)), 99),
        ram_percent_fn=lambda: min(int(psutil.virtual_memory().percent), 99),
        cpu_temp_fn=lambda: get_cpu_temp(sensor_context),
        gpu_metrics_fn=lambda: get_gpu_metrics(sensor_context),
        net_io_fn=psutil.net_io_counters,
        disk_io_fn=psutil.disk_io_counters,
        sample_interval=METRICS_SAMPLE_INTERVAL,
    )



def connect_vfd():
    global ser
    port = find_vfd()
    if not port:
        return False

    try:
        serial_conn = serial.Serial(port, BAUD, timeout=0.2, write_timeout=0.5)
        with serial_lock:
            ser = serial_conn
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            ser.write(INIT_RUS + CLR)
            ser.flush()
        logging.info(f"Подключен дисплей на порту {port}")
        return True
    except Exception as e:
        logging.error(f"Не удалось открыть порт {port}: {e}")
        ser = None
        return False



def monitoring_thread():
    global ser

    try:
        nvml.nvmlInit()
    except Exception as e:
        logging.warning(f"NVML недоступен: {e}")

    sensor_context = init_sensor_context()
    metrics_sampler = create_metrics_sampler(sensor_context)
    metrics_sampler.warmup()
    first_connect = True
    last_display_ts = 0.0

    while app_running:
        if ser is None and not connect_vfd():
            time.sleep(2)
            continue

        if first_connect and ser is not None:
            show_greeting()
            first_connect = False
            last_display_ts = time.time()
            continue

        try:
            metrics_sampler.maybe_refresh()

            override = get_display_override()
            if override:
                set_notes_overlay_suspended(True)
                write_screen(override[0], override[1])
                time.sleep(0.15)
                continue

            if not is_display_on:
                set_notes_overlay_suspended(True)
                time.sleep(0.2)
                continue

            set_notes_overlay_suspended(False)

            overlay_item = get_active_overlay_item()
            if overlay_item is not None:
                write_screen(overlay_item.line1, overlay_item.line2)
                time.sleep(0.15)
                continue

            now = time.time()
            if last_display_ts and now - last_display_ts < current_interval:
                time.sleep(0.05)
                continue

            snapshot = metrics_sampler.get_snapshot()
            line1 = build_line1(snapshot.cpu_percent, snapshot.cpu_temp, snapshot.gpu_percent, snapshot.gpu_temp, snapshot.ram_percent)
            line2 = build_line2(snapshot.disk_read, snapshot.disk_write, snapshot.net_in, snapshot.net_out)
            write_screen(line1, line2)
            last_display_ts = now
        except (serial.SerialException, OSError) as e:
            logging.warning(f"Соединение с дисплеем потеряно: {e}")
            try:
                with serial_lock:
                    if ser is not None:
                        ser.close()
            except Exception:
                pass
            ser = None
            time.sleep(1)
        except Exception as e:
            logging.error(f"Ошибка цикла: {e}")
            time.sleep(1)



def build_metrics_submenu():
    items = []
    for key, label in DISPLAY_FLAGS.items():
        items.append(
            pystray.MenuItem(
                label,
                safe_tray_callback(make_metric_toggle(key), refresh_after=True),
                checked=lambda item, config_key=key: is_metric_enabled(config_key),
            )
        )
    return pystray.Menu(*items)



def build_updates_submenu():
    return pystray.Menu(
        pystray.MenuItem('Проверить сейчас', safe_tray_callback(manual_check_updates, refresh_after=True)),
        pystray.MenuItem(
            'Установить и перезапустить',
            safe_tray_callback(install_update_and_exit),
            enabled=lambda item: bool(cfg.get('pending_update_path')) and os.path.exists(cfg.get('pending_update_path', '')),
        ),
        pystray.MenuItem('Показать changelog (10 версий)', safe_tray_callback(open_changelog_window)),
    )


def build_hotkeys_submenu():
    return pystray.Menu(
        pystray.MenuItem('Включить горячие клавиши', safe_tray_callback(toggle_hotkeys, refresh_after=True), checked=lambda item: cfg.get("hotkeys_enabled", True)),
        pystray.MenuItem('Настроить клавиши...', safe_tray_callback(open_hotkeys_settings)),
    )


def build_notes_reminders_submenu():
    return pystray.Menu(
        pystray.MenuItem('Открыть заметки', safe_tray_callback(show_notes_window)),
        pystray.MenuItem('Открыть напоминания', safe_tray_callback(show_reminders_window)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Следующий элемент', safe_tray_callback(lambda icon, item: cycle_overlay_item(1), refresh_after=True)),
        pystray.MenuItem('Предыдущий элемент', safe_tray_callback(lambda icon, item: cycle_overlay_item(-1), refresh_after=True)),
        pystray.MenuItem('Скрыть активный', safe_tray_callback(lambda icon, item: hide_active_overlay_item(), refresh_after=True)),
        pystray.MenuItem('Завершить активный', safe_tray_callback(lambda icon, item: complete_active_overlay_item(), refresh_after=True)),
    )


def build_alerts_submenu():
    return pystray.Menu(
        pystray.MenuItem('Включить Telegram alerts', safe_tray_callback(toggle_alerts, refresh_after=True), checked=lambda item: alert_manager.get_state_snapshot().get('enabled', False)),
        pystray.MenuItem('Whitelist only', safe_tray_callback(toggle_alerts_whitelist, refresh_after=True), checked=lambda item: alert_manager.get_state_snapshot().get('use_whitelist', False)),
        pystray.MenuItem('Открыть управление alerts', safe_tray_callback(show_alerts_window)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Следующий alert', safe_tray_callback(lambda icon, item: cycle_overlay_item(1), refresh_after=True), enabled=lambda item: bool(alert_manager.get_state_snapshot().get('active_alerts'))),
        pystray.MenuItem('Предыдущий alert', safe_tray_callback(lambda icon, item: cycle_overlay_item(-1), refresh_after=True), enabled=lambda item: bool(alert_manager.get_state_snapshot().get('active_alerts'))),
        pystray.MenuItem('Скрыть активный alert', safe_tray_callback(lambda icon, item: hide_active_overlay_item(), refresh_after=True), enabled=lambda item: bool(alert_manager.get_state_snapshot().get('active_alerts'))),
        pystray.MenuItem('Завершить активный alert', safe_tray_callback(lambda icon, item: complete_active_overlay_item(), refresh_after=True), enabled=lambda item: bool(alert_manager.get_state_snapshot().get('active_alerts'))),
    )


def build_speed_submenu():
    return pystray.Menu(
        pystray.MenuItem('0.5 сек', safe_tray_callback(make_speed_setter(0.5), refresh_after=True), radio=True, checked=lambda item: current_interval == 0.5),
        pystray.MenuItem('1.0 сек (Стандарт)', safe_tray_callback(make_speed_setter(1.0), refresh_after=True), radio=True, checked=lambda item: current_interval == 1.0),
        pystray.MenuItem('5.0 сек', safe_tray_callback(make_speed_setter(5.0), refresh_after=True), radio=True, checked=lambda item: current_interval == 5.0),
        pystray.MenuItem('Свой вариант...', safe_tray_callback(set_custom_speed, refresh_after=True), radio=True, checked=lambda item: current_interval not in [0.5, 1.0, 5.0]),
    )


def build_main_menu():
    return pystray.Menu(
        pystray.MenuItem('Вкл/Выкл дисплей', safe_tray_callback(toggle_display, refresh_after=True)),
        pystray.MenuItem('Что показывать', build_metrics_submenu()),
        pystray.MenuItem('Скорость обновления', build_speed_submenu()),
        pystray.MenuItem('Горячие клавиши', build_hotkeys_submenu()),
        pystray.MenuItem('Заметки и напоминания', build_notes_reminders_submenu()),
        pystray.MenuItem('Telegram alerts', build_alerts_submenu()),
        pystray.MenuItem('Обновления', build_updates_submenu()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Открыть конфиг', safe_tray_callback(open_config_file)),
        pystray.MenuItem('Открыть логи', safe_tray_callback(open_logs)),
        pystray.MenuItem('Автозапуск с Windows', safe_tray_callback(toggle_autostart, refresh_after=True), checked=lambda item: cfg.get("autostart", False)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Выход', exit_app),
    )


def main():
    global tray_icon, hotkey_manager

    if apply_pending_update_on_startup():
        return

    announce_version_change()

    monitor = threading.Thread(target=monitoring_thread, daemon=True)
    monitor.start()

    updater = threading.Thread(target=update_worker_loop, daemon=True)
    updater.start()

    telegram_worker = threading.Thread(target=telegram_alert_worker_loop, daemon=True)
    telegram_worker.start()

    hotkey_manager = app_hotkeys.HotkeyManager(lambda: cfg, lambda: HOTKEY_CALLBACKS)
    hotkey_manager.start()

    tray_icon = pystray.Icon("VFD_Monitor", create_image(), f"VFD PC Monitor v{APP_VERSION}", build_main_menu())
    tray_icon.run()


if __name__ == "__main__":
    main()
