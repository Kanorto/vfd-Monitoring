from __future__ import annotations

import logging
import threading
from ctypes import Structure, WinDLL, byref, wintypes

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312

DEFAULT_HOTKEYS = {
    "toggle_display": "Ctrl+Alt+D",
    "check_updates": "Ctrl+Alt+U",
    "show_changelog": "Ctrl+Alt+C",
    "open_notes_window": "Ctrl+Alt+N",
    "open_reminders_window": "Ctrl+Alt+R",
    "next_overlay_item": "Ctrl+Right",
    "prev_overlay_item": "Ctrl+Left",
    "hide_overlay_item": "Ctrl+Down",
    "complete_overlay_item": "Ctrl+Up",
}

HOTKEY_LABELS = {
    "toggle_display": "Вкл/выкл дисплей",
    "check_updates": "Проверить обновления",
    "show_changelog": "Показать changelog",
    "open_notes_window": "Открыть заметки",
    "open_reminders_window": "Открыть напоминания",
    "next_overlay_item": "Следующий элемент",
    "prev_overlay_item": "Предыдущий элемент",
    "hide_overlay_item": "Скрыть активный",
    "complete_overlay_item": "Завершить активный",
}

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
    ]


user32 = WinDLL('user32', use_last_error=True)
kernel32 = WinDLL('kernel32', use_last_error=True)


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
    def __init__(self, config_getter, callbacks_getter):
        self._config_getter = config_getter
        self._callbacks_getter = callbacks_getter
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
        cfg = self._config_getter()
        if not cfg.get("hotkeys_enabled", True):
            logging.info("Горячие клавиши отключены в конфиге")
            return

        hotkeys = cfg.get("hotkeys", {})
        callbacks = self._callbacks_getter()
        used = {}
        next_hotkey_id = 1
        for action, callback in callbacks.items():
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


def open_hotkeys_settings_window(config_getter, save_callback, reload_callback, open_config_callback):
    cfg = config_getter()

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
        ttk.Button(button_bar, text='Открыть конфиг', command=open_config_callback).pack(side='left', padx=(0, 8))

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
            save_callback(cfg)
            reload_callback()
            root.destroy()

        ttk.Button(button_bar, text='Сохранить', command=save_and_close).pack(side='left', padx=(0, 8))
        ttk.Button(button_bar, text='Отмена', command=root.destroy).pack(side='left')

        frame.columnconfigure(1, weight=1)
        root.mainloop()

    threading.Thread(target=worker, daemon=True).start()
