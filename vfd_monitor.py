import getpass
import json
import logging
import os
import random
import sys
import threading
import time
import winreg

import psutil
import pystray
import serial
import serial.tools.list_ports
import win32api
import wmi
from PIL import Image, ImageDraw
from py3nvml import py3nvml as nvml

# --- НАСТРОЙКИ VFD ---
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


def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


os.chdir(get_base_path())
CONFIG_PATH = os.path.join(get_base_path(), CONFIG_NAME)
LOG_PATH = os.path.join(get_base_path(), LOG_NAME)

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
serial_lock = threading.Lock()


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
    with open(CONFIG_PATH, 'w', encoding='utf-8') as file:
        json.dump(cfg, file, indent=4)
    return cfg


cfg = load_config()
current_interval = cfg.get("update_interval", 1.0)


def save_config(config):
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as file:
            json.dump(config, file, indent=4)
    except Exception as e:
        logging.error(f"Ошибка сохранения конфига: {e}")


# --- НИЗКОУРОВНЕВАЯ ОТРИСОВКА ---
def fit_text(text, width=LINE_WIDTH, align='left'):
    text = text[:width]
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
        write_screen(random.choice(greetings), "МОНИТОРИНГ ЗАПУЩЕН", clear_first=True)
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
    show_farewell()
    return True


win32api.SetConsoleCtrlHandler(cleanup_and_exit, True)


# --- ИНТЕРФЕЙС И ТРЕЙ ---
def toggle_autostart(icon, item):
    cfg["autostart"] = not cfg["autostart"]
    save_config(cfg)
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_ALL_ACCESS)
        if cfg["autostart"]:
            winreg.SetValueEx(key, "VFD_Monitor", 0, winreg.REG_SZ, sys.executable)
        else:
            try:
                winreg.DeleteValue(key, "VFD_Monitor")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception as e:
        logging.error(f"Ошибка автозагрузки: {e}")



def toggle_display(icon, item):
    global is_display_on
    is_display_on = not is_display_on
    if not is_display_on:
        try:
            write_serial(CLR)
        except Exception:
            pass



def make_speed_setter(value):
    def setter(icon, item):
        global current_interval
        current_interval = value
        cfg["update_interval"] = value
        save_config(cfg)
    return setter



def make_metric_toggle(config_key):
    def toggle(icon, item):
        cfg[config_key] = not cfg[config_key]
        save_config(cfg)
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



def exit_app(icon, item):
    global app_running
    app_running = False
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



def build_usage_options(prefix, percent, temp, show_usage, show_temp):
    options = []
    if show_usage and percent is not None and show_temp and temp is not None:
        options.append(f"{prefix}{percent:02d} {temp:02d}{DEG_CHAR}")
        options.append(f"{prefix}{percent:02d}%")
        options.append(f"{prefix}{percent:02d}")
    elif show_usage and percent is not None:
        options.append(f"{prefix}{percent:02d}%")
        options.append(f"{prefix}{percent:02d}")
    elif show_temp and temp is not None:
        options.append(f"{prefix}{temp:02d}{DEG_CHAR}")
        options.append(f"{prefix}{temp:02d}")
    return options



def render_segments(segment_options, width=LINE_WIDTH):
    if not segment_options:
        return fit_text('', width, align='center')

    indexes = [0] * len(segment_options)
    while True:
        text = ' '.join(options[index] for options, index in zip(segment_options, indexes))
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
            compact = ''.join(options[-1] for options in segment_options)
            return fit_text(compact, width, align='center')

        indexes[candidate] += 1



def build_primary_segments(cpu_percent, cpu_temp, gpu_percent, gpu_temp, ram_percent):
    segments = []
    cpu_options = build_usage_options(
        'C',
        cpu_percent,
        cpu_temp,
        cfg.get("show_cpu_usage", True),
        cfg.get("show_cpu_temp", True),
    )
    if cpu_options:
        segments.append(cpu_options)

    gpu_options = build_usage_options(
        'G',
        gpu_percent,
        gpu_temp,
        cfg.get("show_gpu_usage", True),
        cfg.get("show_gpu_temp", True),
    )
    if gpu_options:
        segments.append(gpu_options)

    if cfg.get("show_ram", True):
        segments.append([f"R{ram_percent:02d}%", f"R{ram_percent:02d}"])
    return segments



def build_io_segments(disk_read, disk_write, net_in, net_out):
    segments = []
    if cfg.get("show_disk", True):
        segments.append([
            f"D:{fmt_v(disk_read)}/{fmt_v(disk_write)}",
            f"D{fmt_v(disk_read)}/{fmt_v(disk_write)}",
        ])
    if cfg.get("show_network", True):
        segments.append([
            f"N:{fmt_v(net_in)}/{fmt_v(net_out)}",
            f"N{fmt_v(net_in)}/{fmt_v(net_out)}",
        ])
    return segments



def build_line1(cpu_percent, cpu_temp, gpu_percent, gpu_temp, ram_percent):
    return render_segments(build_primary_segments(cpu_percent, cpu_temp, gpu_percent, gpu_temp, ram_percent))



def build_line2(disk_read, disk_write, net_in, net_out):
    return render_segments(build_io_segments(disk_read, disk_write, net_in, net_out))



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



def main():
    monitor = threading.Thread(target=monitoring_thread, daemon=True)
    monitor.start()

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
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Открыть логи', open_logs),
        pystray.MenuItem('Автозапуск с Windows', toggle_autostart, checked=lambda item: cfg.get("autostart", False)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Выход', exit_app),
    )

    icon = pystray.Icon("VFD_Monitor", create_image(), "VFD PC Monitor", menu)
    icon.run()


if __name__ == "__main__":
    main()
