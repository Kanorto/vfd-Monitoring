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

# --- НАСТРОЙКИ VFD ---
APP_VERSION = os.environ.get("VFD_MONITOR_VERSION", "0.3.0")
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
DEFAULT_REPOSITORY = os.environ.get("VFD_MONITOR_GITHUB_REPO", "Kanorto/vfd-Monitoring")
UPDATE_API_BASE = "https://api.github.com/repos/{repo}/releases/latest"
UPDATE_DOWNLOAD_CHUNK = 1024 * 128
UPDATE_TIMEOUT = 20
UPDATE_ATTEMPTS = 4
UPDATE_POLL_INTERVAL = 1800
UPDATE_ALERT_THRESHOLD = 3
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
        "D:{read}/{write}",
        "D{read}/{write}",
    ],
    "network": [
        "N:{recv}/{send}",
        "N{recv}/{send}",
    ],
}
DEFAULT_LINE_SPACING = {
    "primary": " ",
    "secondary": " ",
    "primary_compact": "",
    "secondary_compact": "",
}
DEFAULT_HOTKEYS = {
    "toggle_display": "Ctrl+Alt+D",
    "check_updates": "Ctrl+Alt+U",
    "show_changelog": "Ctrl+Alt+C",
}
HOTKEY_LABELS = {
    "toggle_display": "Вкл/выкл дисплей",
    "check_updates": "Проверить обновления",
    "show_changelog": "Показать changelog",
}
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
    raw_hotkeys = hotkeys if isinstance(hotkeys, dict) else {}
    result = {}
    for action, default in DEFAULT_HOTKEYS.items():
        result[action] = canonicalize_hotkey(raw_hotkeys.get(action, default))
    return result


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
        "pending_update_notes": "",
        "last_changelog_version": "",
        "last_changelog": "",
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
        with open(CONFIG_PATH, 'w', encoding='utf-8') as file:
            json.dump(config, file, indent=4, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка сохранения конфига: {e}")


# --- НИЗКОУРОВНЕВАЯ ОТРИСОВКА ---
def fit_text(text, width=LINE_WIDTH, align='left'):
    text = str(text)[:width]
    if align == 'center':
        return text.center(width)
    if align == 'right':
        return text.rjust(width)
    return text.ljust(width)



def encode_vfd_text(text):
    encoded = bytearray()
    for char in text:
        if char in SPECIAL_CHARS:
            encoded.extend(SPECIAL_CHARS[char])
            continue
        encoded.extend(char.encode('cp866', errors='replace'))
    return bytes(encoded)



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
    python_asset = None

    for asset in assets:
        asset_name = asset.get('name', '').lower()
        if asset_name == current_name:
            exact_match = asset
        if asset_name.endswith('.exe'):
            return asset
        if asset_name.endswith('.py') and python_asset is None:
            python_asset = asset
    if exact_match is not None:
        return exact_match
    if python_asset is not None:
        return python_asset
    return assets[0]



def format_release_notes(notes):
    text = (notes or '').replace('\r\n', '\n').strip()
    if not text:
        return 'Изменения не указаны автором релиза.'
    return text


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
        except Exception:
            pass


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



def build_update_script(target_path, staged_path):
    launch_cmd = subprocess.list2cmdline(get_launch_command())
    script_path = os.path.join(tempfile.gettempdir(), 'vfd_apply_update.cmd')
    lines = [
        '@echo off',
        'setlocal enabledelayedexpansion',
        f'set "TARGET={target_path}"',
        f'set "STAGED={staged_path}"',
        'for /l %%I in (1,1,90) do (',
        '  copy /Y "!STAGED!" "!TARGET!" >nul 2>&1 && goto copied',
        '  timeout /t 1 /nobreak >nul',
        ')',
        'exit /b 1',
        ':copied',
        'del /Q "!STAGED!" >nul 2>&1',
        f'start "" {launch_cmd}',
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

    target_path = sys.executable if getattr(sys, 'frozen', False) else os.path.abspath(__file__)
    script_path = build_update_script(target_path, pending_path)
    show_temporary_message('ОБНОВЛЕНИЕ', f'УСТАНОВКА {pending_version}', duration=2)
    logging.info(f"Запущена установка обновления {pending_version} из {pending_path}")
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
    cfg['pending_update_notes'] = ''
    cfg['update_failure_count'] = 0
    cfg['last_update_error'] = ''
    cfg['last_alerted_version'] = ''
    save_config(cfg)

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
    return {
        'version': version,
        'notes': notes,
        'asset_name': asset.get('name', 'release.bin'),
        'asset_url': asset.get('browser_download_url', ''),
        'asset_size': int(asset.get('size') or 0),
        'release_url': release.get('html_url') or get_release_page_url(version),
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
            if total > 0:
                ratio = max(0.0, min(progress['downloaded'] / total, 1.0))
                bars = min(8, max(1, int(ratio * 8)))
                percent = int(ratio * 100)
                return f"{percent:3d}% [{'#' * bars}{'.' * (8 - bars)}]"
            size_mb = progress['downloaded'] / (1024 * 1024)
            return f"{size_mb:4.1f} MB"

        animator = StatusAnimator('СКАЧИВАЮ ОБН', subtitle).start()
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
        cfg['pending_update_notes'] = release_info['notes']
        cfg['known_latest_version'] = release_info['version']
        cfg['last_release_url'] = release_info.get('release_url', '')
        clear_update_failure_state()
        save_config(cfg)

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

        root = tk.Tk()
        root.title('Changelog VFD Monitor')
        root.geometry('760x520')
        root.attributes('-topmost', True)

        text = scrolledtext.ScrolledText(root, wrap='word', font=('Consolas', 10))
        text.pack(fill='both', expand=True)
        version = cfg.get('last_changelog_version') or cfg.get('pending_update_version') or cfg.get('known_latest_version') or APP_VERSION
        changelog = cfg.get('last_changelog') or cfg.get('pending_update_notes') or 'История изменений пока недоступна.'
        text.insert('1.0', f"Версия: {version}\n\n{changelog}")
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


HOTKEY_CALLBACKS.update({
    "toggle_display": execute_hotkey_toggle_display,
    "check_updates": execute_hotkey_check_updates,
    "show_changelog": execute_hotkey_show_changelog,
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
    def worker():
        import tkinter as tk
        from tkinter import messagebox, ttk

        root = tk.Tk()
        root.title("Горячие клавиши VFD Monitor")
        root.attributes('-topmost', True)
        root.resizable(False, False)

        state_var = tk.BooleanVar(value=cfg.get("hotkeys_enabled", True))
        value_vars = {
            action: tk.StringVar(value=format_hotkey_preview(cfg.get("hotkeys", {}).get(action, '')))
            for action in DEFAULT_HOTKEYS
        }
        local_hotkeys = dict(cfg.get("hotkeys", {}))

        pressed_modifiers = set()
        capture_window = {"value": None}

        def normalize_combo(modifiers, key_name):
            ordered = [name for name in ["Ctrl", "Alt", "Shift", "Win"] if name in modifiers]
            return '+'.join(ordered + [key_name]) if key_name else '+'.join(ordered)

        def start_capture(action):
            if capture_window["value"] is not None:
                capture_window["value"].destroy()

            pressed_modifiers.clear()
            dialog = tk.Toplevel(root)
            dialog.title(f"Запись: {HOTKEY_LABELS[action]}")
            dialog.attributes('-topmost', True)
            dialog.resizable(False, False)
            dialog.grab_set()
            capture_window["value"] = dialog

            def close_capture():
                capture_window["value"] = None
                dialog.destroy()

            hint_var = tk.StringVar(value="Нажмите комбинацию, затем отпустите клавиши.")
            ttk.Label(dialog, text=HOTKEY_LABELS[action], font=('Segoe UI', 10, 'bold')).pack(padx=16, pady=(12, 8))
            ttk.Label(dialog, textvariable=hint_var, width=40, anchor='center').pack(padx=16, pady=(0, 12))

            def on_press(event):
                key = event.keysym.lower()
                if key in ('control_l', 'control_r'):
                    pressed_modifiers.add('Ctrl')
                    hint_var.set(normalize_combo(pressed_modifiers, ''))
                    return
                if key in ('alt_l', 'alt_r', 'meta_l', 'meta_r'):
                    pressed_modifiers.add('Alt')
                    hint_var.set(normalize_combo(pressed_modifiers, ''))
                    return
                if key in ('shift_l', 'shift_r'):
                    pressed_modifiers.add('Shift')
                    hint_var.set(normalize_combo(pressed_modifiers, ''))
                    return
                if key in ('super_l', 'super_r', 'win_l', 'win_r'):
                    pressed_modifiers.add('Win')
                    hint_var.set(normalize_combo(pressed_modifiers, ''))
                    return

                key_name = event.keysym.upper() if len(event.keysym) == 1 else event.keysym.replace('_', '').upper()
                combo = canonicalize_hotkey(normalize_combo(pressed_modifiers, key_name))
                if combo:
                    local_hotkeys[action] = combo
                    value_vars[action].set(combo)
                    close_capture()

            def on_release(event):
                key = event.keysym.lower()
                if key in ('control_l', 'control_r'):
                    pressed_modifiers.discard('Ctrl')
                elif key in ('alt_l', 'alt_r', 'meta_l', 'meta_r'):
                    pressed_modifiers.discard('Alt')
                elif key in ('shift_l', 'shift_r'):
                    pressed_modifiers.discard('Shift')
                elif key in ('super_l', 'super_r', 'win_l', 'win_r'):
                    pressed_modifiers.discard('Win')

            dialog.bind('<KeyPress>', on_press)
            dialog.bind('<KeyRelease>', on_release)
            dialog.bind('<Escape>', lambda event: close_capture())
            dialog.protocol("WM_DELETE_WINDOW", close_capture)
            dialog.focus_force()

        frame = ttk.Frame(root, padding=14)
        frame.pack(fill='both', expand=True)

        ttk.Checkbutton(frame, text='Включить глобальные горячие клавиши', variable=state_var).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 12))

        row = 1
        for action, label in HOTKEY_LABELS.items():
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=4)
            ttk.Entry(frame, textvariable=value_vars[action], state='readonly', width=24).grid(row=row, column=1, sticky='ew', pady=4)
            button_frame = ttk.Frame(frame)
            button_frame.grid(row=row, column=2, sticky='e', pady=4)
            ttk.Button(button_frame, text='Записать', command=lambda action_name=action: start_capture(action_name)).pack(side='left', padx=(0, 6))
            ttk.Button(
                button_frame,
                text='Очистить',
                command=lambda action_name=action: (local_hotkeys.__setitem__(action_name, ''), value_vars[action_name].set('Не назначено')),
            ).pack(side='left')
            row += 1

        ttk.Label(
            frame,
            text="Шаблоны форматирования и отступы редактируются в vfd_config.json\nразделами metric_formats и line_spacing.",
            justify='left',
        ).grid(row=row, column=0, columnspan=3, sticky='w', pady=(12, 10))
        row += 1

        button_bar = ttk.Frame(frame)
        button_bar.grid(row=row, column=0, columnspan=3, sticky='e')
        ttk.Button(button_bar, text='Открыть конфиг', command=open_config_file).pack(side='left', padx=(0, 8))

        def save_and_close():
            normalized = sanitize_hotkeys(local_hotkeys)
            duplicates = {}
            for action, hotkey_value in normalized.items():
                if not hotkey_value:
                    continue
                if hotkey_value in duplicates:
                    messagebox.showerror(
                        "Повтор клавиш",
                        f"Комбинация {hotkey_value} уже назначена для '{HOTKEY_LABELS[duplicates[hotkey_value]]}'.",
                        parent=root,
                    )
                    return
                if parse_hotkey(hotkey_value) is None:
                    messagebox.showerror("Ошибка", f"Комбинация {hotkey_value} не поддерживается.", parent=root)
                    return
                duplicates[hotkey_value] = action
            cfg["hotkeys_enabled"] = bool(state_var.get())
            cfg["hotkeys"] = normalized
            save_config(cfg)
            reload_hotkeys()
            root.destroy()

        ttk.Button(button_bar, text='Сохранить', command=save_and_close).pack(side='left', padx=(0, 8))
        ttk.Button(button_bar, text='Отмена', command=root.destroy).pack(side='left')

        frame.columnconfigure(1, weight=1)
        root.mainloop()

    threading.Thread(target=worker, daemon=True).start()


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
def get_cpu_temp(wmi_obj):
    if not wmi_obj:
        return None
    try:
        zones = wmi_obj.MSAcpi_ThermalZoneTemperature()
        if zones:
            temp = (zones[0].CurrentTemperature / 10.0) - 273.15
            return min(int(temp), 99) if 10 < temp < 110 else None
    except Exception:
        return None
    return None



def get_gpu_metrics():
    if not cfg.get("show_gpu_usage", True) and not cfg.get("show_gpu_temp", True):
        return None, None
    try:
        handle = nvml.nvmlDeviceGetHandleByIndex(0)
        util = nvml.nvmlDeviceGetUtilizationRates(handle).gpu
        temp = nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU)
        return min(int(util), 99), min(int(temp), 99)
    except Exception:
        return None, None



def fmt_v(value):
    kb = value / 1024
    if kb < 1:
        return " 0K"
    if kb < 100:
        return f"{int(kb):2d}K"
    if kb < 1000:
        return f"{int(kb // 100):1d}00"
    mb = kb / 1024
    if mb < 10:
        return f" {int(mb)}M"
    return f"{min(int(mb), 99):2d}M"



def find_vfd():
    chips = ["Prolific", "USB-to-Serial", "FTDI", "Posiflex", "Atol"]
    for port in serial.tools.list_ports.comports():
        desc = (port.description + port.hwid).lower()
        if any(chip.lower() in desc for chip in chips):
            return port.device
    return None



def get_metric_templates(metric_name):
    metric_formats = cfg.get("metric_formats", {})
    templates = metric_formats.get(metric_name)
    if isinstance(templates, list) and templates:
        return templates
    return list(DEFAULT_METRIC_FORMATS[metric_name])


def apply_template(template, **kwargs):
    try:
        return str(template).format(**kwargs)
    except Exception as exc:
        logging.warning("Некорректный шаблон '%s': %s", template, exc)
        return ''


def build_usage_options(metric_name, full_prefix, short_prefix, percent, temp, show_usage, show_temp):
    options = []
    templates = get_metric_templates(metric_name)
    values = {
        "usage": percent if percent is not None else 0,
        "temp": temp if temp is not None else 0,
        "full_prefix": full_prefix,
        "short_prefix": short_prefix,
        "degree": DEG_CHAR,
    }
    if show_usage and percent is not None and show_temp and temp is not None:
        for template in templates:
            rendered = apply_template(template, **values)
            if rendered and rendered not in options:
                options.append(rendered)
    elif show_usage and percent is not None:
        for template in templates:
            if '{temp' in str(template):
                continue
            rendered = apply_template(template, **values)
            if rendered and rendered not in options:
                options.append(rendered)
    elif show_temp and temp is not None:
        for template in templates:
            if '{usage' in str(template):
                continue
            rendered = apply_template(template, **values)
            if rendered and rendered not in options:
                options.append(rendered)
    return options



def render_segments(segment_options, width=LINE_WIDTH, separator=' ', compact_separator=''):
    if not segment_options:
        return fit_text('', width, align='center')

    indexes = [0] * len(segment_options)
    while True:
        text = separator.join(options[index] for options, index in zip(segment_options, indexes))
        if len(text) <= width:
            return fit_text(text, width, align='center')

        candidate = None
        best_delta = 0
        for idx, options in enumerate(segment_options):
            if indexes[idx] >= len(options) - 1:
                continue
            current_length = len(options[indexes[idx]])
            next_length = len(options[indexes[idx] + 1])
            delta = current_length - next_length
            if delta > best_delta:
                best_delta = delta
                candidate = idx

        if candidate is None:
            compact = compact_separator.join(options[-1] for options in segment_options)
            return fit_text(compact, width, align='center')

        indexes[candidate] += 1



def build_primary_segments(cpu_percent, cpu_temp, gpu_percent, gpu_temp, ram_percent):
    segments = []
    cpu_options = build_usage_options(
        'cpu',
        'CPU',
        'C',
        cpu_percent,
        cpu_temp,
        cfg.get("show_cpu_usage", True),
        cfg.get("show_cpu_temp", True),
    )
    if cpu_options:
        segments.append(cpu_options)

    gpu_options = build_usage_options(
        'gpu',
        'GPU',
        'G',
        gpu_percent,
        gpu_temp,
        cfg.get("show_gpu_usage", True),
        cfg.get("show_gpu_temp", True),
    )
    if gpu_options:
        segments.append(gpu_options)

    if cfg.get("show_ram", True):
        ram_options = []
        for template in get_metric_templates("ram"):
            rendered = apply_template(template, value=ram_percent)
            if rendered and rendered not in ram_options:
                ram_options.append(rendered)
        if ram_options:
            segments.append(ram_options)
    return segments



def build_io_segments(disk_read, disk_write, net_in, net_out):
    segments = []
    if cfg.get("show_disk", True):
        disk_options = []
        for template in get_metric_templates("disk"):
            rendered = apply_template(template, read=fmt_v(disk_read), write=fmt_v(disk_write))
            if rendered and rendered not in disk_options:
                disk_options.append(rendered)
        if disk_options:
            segments.append(disk_options)
    if cfg.get("show_network", True):
        network_options = []
        for template in get_metric_templates("network"):
            rendered = apply_template(template, recv=fmt_v(net_in), send=fmt_v(net_out))
            if rendered and rendered not in network_options:
                network_options.append(rendered)
        if network_options:
            segments.append(network_options)
    return segments



def build_line1(cpu_percent, cpu_temp, gpu_percent, gpu_temp, ram_percent):
    spacing = cfg.get("line_spacing", DEFAULT_LINE_SPACING)
    return render_segments(
        build_primary_segments(cpu_percent, cpu_temp, gpu_percent, gpu_temp, ram_percent),
        separator=spacing.get("primary", " "),
        compact_separator=spacing.get("primary_compact", ""),
    )



def build_line2(disk_read, disk_write, net_in, net_out):
    spacing = cfg.get("line_spacing", DEFAULT_LINE_SPACING)
    return render_segments(
        build_io_segments(disk_read, disk_write, net_in, net_out),
        separator=spacing.get("secondary", " "),
        compact_separator=spacing.get("secondary_compact", ""),
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

    wmi_namespace = None
    try:
        wmi_namespace = wmi.WMI(namespace="root\\wmi")
    except Exception as e:
        logging.warning(f"WMI namespace root\\wmi недоступен: {e}")

    last_net = psutil.net_io_counters()
    last_disk = psutil.disk_io_counters()
    last_ts = time.time()
    first_connect = True
    psutil.cpu_percent(interval=None)

    while app_running:
        if ser is None and not connect_vfd():
            time.sleep(2)
            continue

        if first_connect and ser is not None:
            show_greeting()
            first_connect = False
            last_net = psutil.net_io_counters()
            last_disk = psutil.disk_io_counters()
            last_ts = time.time()
            continue

        try:
            override = get_display_override()
            if override:
                write_screen(override[0], override[1])
                time.sleep(0.15)
                continue

            if not is_display_on:
                time.sleep(0.2)
                continue

            now = time.time()
            dt = now - last_ts
            if dt < current_interval:
                time.sleep(0.05)
                continue

            cpu_percent = min(int(psutil.cpu_percent(interval=None)), 99)
            ram_percent = min(int(psutil.virtual_memory().percent), 99)
            cpu_temp = get_cpu_temp(wmi_namespace)
            gpu_percent, gpu_temp = get_gpu_metrics()

            net = psutil.net_io_counters()
            disk = psutil.disk_io_counters()
            net_in = max((net.bytes_recv - last_net.bytes_recv) / dt, 0)
            net_out = max((net.bytes_sent - last_net.bytes_sent) / dt, 0)
            disk_read = max((disk.read_bytes - last_disk.read_bytes) / dt, 0)
            disk_write = max((disk.write_bytes - last_disk.write_bytes) / dt, 0)
            last_net, last_disk, last_ts = net, disk, now

            line1 = build_line1(cpu_percent, cpu_temp, gpu_percent, gpu_temp, ram_percent)
            line2 = build_line2(disk_read, disk_write, net_in, net_out)
            write_screen(line1, line2)
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
                make_metric_toggle(key),
                checked=lambda item, config_key=key: is_metric_enabled(config_key),
            )
        )
    return pystray.Menu(*items)



def build_updates_submenu():
    return pystray.Menu(
        pystray.MenuItem('Проверить сейчас', manual_check_updates),
        pystray.MenuItem(
            'Установить и перезапустить',
            install_update_and_exit,
            enabled=lambda item: bool(cfg.get('pending_update_path')) and os.path.exists(cfg.get('pending_update_path', '')),
        ),
        pystray.MenuItem('Показать changelog', open_changelog_window),
    )


def build_hotkeys_submenu():
    return pystray.Menu(
        pystray.MenuItem('Включить горячие клавиши', toggle_hotkeys, checked=lambda item: cfg.get("hotkeys_enabled", True)),
        pystray.MenuItem('Настроить клавиши...', open_hotkeys_settings),
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

    hotkey_manager = HotkeyManager()
    hotkey_manager.start()

    speed_submenu = pystray.Menu(
        pystray.MenuItem('0.5 сек', make_speed_setter(0.5), radio=True, checked=lambda item: current_interval == 0.5),
        pystray.MenuItem('1.0 сек (Стандарт)', make_speed_setter(1.0), radio=True, checked=lambda item: current_interval == 1.0),
        pystray.MenuItem('5.0 сек', make_speed_setter(5.0), radio=True, checked=lambda item: current_interval == 5.0),
        pystray.MenuItem('Свой вариант...', set_custom_speed, radio=True, checked=lambda item: current_interval not in [0.5, 1.0, 5.0]),
    )

    menu = pystray.Menu(
        pystray.MenuItem('Вкл/Выкл дисплей', toggle_display),
        pystray.MenuItem('Что показывать', build_metrics_submenu()),
        pystray.MenuItem('Скорость обновления', speed_submenu),
        pystray.MenuItem('Горячие клавиши', build_hotkeys_submenu()),
        pystray.MenuItem('Обновления', build_updates_submenu()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Открыть конфиг', open_config_file),
        pystray.MenuItem('Открыть логи', open_logs),
        pystray.MenuItem('Автозапуск с Windows', toggle_autostart, checked=lambda item: cfg.get("autostart", False)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Выход', exit_app),
    )

    tray_icon = pystray.Icon("VFD_Monitor", create_image(), f"VFD PC Monitor v{APP_VERSION}", menu)
    tray_icon.run()


if __name__ == "__main__":
    main()
