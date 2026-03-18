import serial
import serial.tools.list_ports
import psutil
import time
import wmi
import json
import os
import sys
import threading
import winreg
import logging
import random
import getpass
import win32api
import pystray
from PIL import Image, ImageDraw
from py3nvml import py3nvml as nvml

# --- НАСТРОЙКИ VFD ---
BAUD = 9600
CLR = b'\x0c'
HOME = b'\x0b'
INIT_RUS = b'\x1b\x74\x07'
CONFIG_NAME = "vfd_config.json"
LOG_NAME = "vfd_monitor.log"

def get_base_path():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

os.chdir(get_base_path())
CONFIG_PATH = os.path.join(get_base_path(), CONFIG_NAME)
LOG_PATH = os.path.join(get_base_path(), LOG_NAME)

logging.basicConfig(filename=LOG_PATH, level=logging.INFO, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---
is_display_on = True
current_interval = 1.0
ser = None
app_running = True

def load_config():
    defaults = {"show_gpu": True, "show_ram": True, "autostart": False, "update_interval": 1.0}
    cfg = defaults.copy()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                user_cfg = json.load(f)
                for k, v in user_cfg.items(): cfg[k] = v
        except Exception as e:
            logging.error(f"Ошибка чтения конфига: {e}")
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=4)
    return cfg

def save_config(cfg):
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        logging.error(f"Ошибка сохранения конфига: {e}")

cfg = load_config()
current_interval = cfg.get("update_interval", 1.0)

# --- ЛОГИКА ПРИВЕТСТВИЙ И ПРОЩАНИЙ ---

def show_greeting(serial_conn):
    user = getpass.getuser().upper()[:15] # Ограничиваем длину имени
    greetings = [
        f" ПРИВЕТ, {user}! ",
        "  СИСТЕМА АКТИВНА   ",
        "  ДОБРО ПОЖАЛОВАТЬ  ",
        f" СТАРТ ОС, {user} "
    ]
    msg = random.choice(greetings).center(20)
    try:
        serial_conn.write(CLR + HOME + msg.encode('cp866'))
        time.sleep(5)
        serial_conn.write(CLR)
    except: pass

def show_farewell():
    global ser
    if ser:
        try:
            msg = " СИСТЕМА ВЫКЛЮЧЕНА  ".center(20)
            ser.write(CLR + HOME + msg.encode('cp866'))
            time.sleep(3) # Даем время прочитать
            ser.write(CLR) # Очищаем дисплей (тушим)
            ser.close()
        except: pass

def cleanup_and_exit(event):
    # Обработчик событий выключения/перезагрузки Windows
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
            winreg.DeleteValue(key, "VFD_Monitor")
        winreg.CloseKey(key)
    except Exception as e: logging.error(f"Ошибка автозагрузки: {e}")

def toggle_display(icon, item):
    global is_display_on, ser
    is_display_on = not is_display_on
    if not is_display_on and ser:
        try: ser.write(CLR)
        except: pass

def make_speed_setter(val):
    def setter(icon, item):
        global current_interval
        current_interval = val
        cfg["update_interval"] = val
        save_config(cfg)
    return setter

def set_custom_speed(icon, item):
    def prompt():
        import tkinter as tk
        from tkinter import simpledialog, messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True) # Окно поверх всех
        try:
            val = simpledialog.askfloat("Скорость", "Интервал обновления (в секундах):", parent=root)
            if val is not None:
                if val < 0.1:
                    messagebox.showwarning("Внимание", "Интервал менее 0.1с недопустим!")
                else:
                    global current_interval
                    current_interval = val
                    cfg["update_interval"] = val
                    save_config(cfg)
        finally:
            root.destroy()
    threading.Thread(target=prompt, daemon=True).start()

def open_logs(icon, item):
    try: os.startfile(LOG_PATH)
    except: pass

def exit_app(icon, item):
    global app_running
    app_running = False
    icon.stop()
    show_farewell()
    sys.exit(0)

def create_image():
    image = Image.new('RGB', (64, 64), color=(0, 0, 0))
    dc = ImageDraw.Draw(image)
    dc.rectangle((16, 16, 48, 48), fill=(0, 255, 128))
    return image

# --- СБОР СТАТИСТИКИ ---

def get_cpu_t(wmi_obj):
    if not wmi_obj: return None
    try:
        zones = wmi_obj.MSAcpi_ThermalZoneTemperature()
        if zones:
            t = (zones[0].CurrentTemperature / 10.0) - 273.15
            return min(int(t), 99) if 10 < t < 110 else None
    except: return None

def get_gpu():
    try:
        h = nvml.nvmlDeviceGetHandleByIndex(0)
        u = nvml.nvmlDeviceGetUtilizationRates(h).gpu
        t = nvml.nvmlDeviceGetTemperature(h, nvml.NVML_TEMPERATURE_GPU)
        return min(int(u), 99), min(int(t), 99)
    except: return None, None

def fmt_v(val):
    kb = val / 1024
    if kb < 1: return " 0K"
    if kb < 100: return f"{int(kb):2d}K"
    if kb < 1000: return f"{int(kb//100):1d}00"
    mb = kb / 1024
    if mb < 10: return f" {int(mb)}M"
    return f"{min(int(mb), 99):2d}M"

def find_vfd():
    chips = ["Prolific", "USB-to-Serial", "FTDI", "Posiflex", "Atol"]
    for p in serial.tools.list_ports.comports():
        desc = (p.description + p.hwid).lower()
        if any(x.lower() in desc for x in chips): return p.device
    return None

def monitoring_thread():
    global ser
    try: nvml.nvmlInit()
    except: pass
    
    wmi_n = None
    try: wmi_n = wmi.WMI(namespace="root\\wmi")
    except: pass

    last_n, last_d, last_t = psutil.net_io_counters(), psutil.disk_io_counters(), time.time()
    is_first_connect = True
    
    while app_running:
        if ser is None:
            port = find_vfd()
            if port:
                try:
                    ser = serial.Serial(port, BAUD, timeout=0.1)
                    ser.write(INIT_RUS)
                    if is_first_connect:
                        show_greeting(ser)
                        is_first_connect = False
                except: time.sleep(2); continue
            else: time.sleep(2); continue

        try:
            if not is_display_on:
                time.sleep(0.2); continue

            now = time.time()
            dt = now - last_t
            if dt < current_interval:
                time.sleep(0.05); continue

            cp, rp = min(int(psutil.cpu_percent()), 99), min(int(psutil.virtual_memory().percent), 99)
            ct, (gp, gt) = get_cpu_t(wmi_n), get_gpu()
            
            n, d = psutil.net_io_counters(), psutil.disk_io_counters()
            ni, no = (n.bytes_recv - last_n.bytes_recv)/dt, (n.bytes_sent - last_n.bytes_sent)/dt
            dr, dw = (d.read_bytes - last_d.read_bytes)/dt, (d.write_bytes - last_d.write_bytes)/dt
            last_n, last_d, last_t = n, d, now

            # --- ФОРМИРОВАНИЕ СТРОК ---
            # Используем слова-заменители, чтобы потом перевести их в байты
            cpu_str = f"C:{cp:02d}%" if not ct else f"C:{cp:02d} {ct:02d}~deg~"
            
            gpu_str = ""
            if cfg.get("show_gpu") and gp is not None:
                gpu_str = f" G:{gp:02d}%" if not gt else f" G:{gp:02d} {gt:02d}~deg~"

            ram_str = f" R:{rp:02d}%" if cfg.get("show_ram") else ""

            line1 = f"{cpu_str}{gpu_str}{ram_str}".strip().center(20)
            line2 = f"D{fmt_v(dr)}~down~{fmt_v(dw)}~up~ N{fmt_v(ni)}~down~{fmt_v(no)}~up~".center(20)

            # Переводим в CP866 и меняем псевдо-теги на реальные шестнадцатеричные байты
            raw_bytes = (line1[:20] + line2[:20]).encode('cp866', errors='replace')
            raw_bytes = raw_bytes.replace(b'~deg~', b'\xf8')
            raw_bytes = raw_bytes.replace(b'~up~', b'\x18')
            raw_bytes = raw_bytes.replace(b'~down~', b'\x19')

            ser.write(HOME + raw_bytes)

        except (serial.SerialException, OSError): 
            ser = None
        except Exception as e: 
            logging.error(f"Ошибка цикла: {e}")
            time.sleep(1)

def main():
    monitor_thread = threading.Thread(target=monitoring_thread, daemon=True)
    monitor_thread.start()

    speed_submenu = pystray.Menu(
        pystray.MenuItem('0.5 сек', make_speed_setter(0.5), radio=True, checked=lambda i: current_interval == 0.5),
        pystray.MenuItem('1.0 сек (Стандарт)', make_speed_setter(1.0), radio=True, checked=lambda i: current_interval == 1.0),
        pystray.MenuItem('5.0 сек', make_speed_setter(5.0), radio=True, checked=lambda i: current_interval == 5.0),
        pystray.MenuItem('Свой вариант...', set_custom_speed, radio=True, checked=lambda i: current_interval not in [0.5, 1.0, 5.0])
    )

    menu = pystray.Menu(
        pystray.MenuItem('Вкл/Выкл дисплей', toggle_display),
        pystray.MenuItem('Скорость обновления', speed_submenu),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Открыть логи', open_logs),
        pystray.MenuItem('Автозапуск с Windows', toggle_autostart, checked=lambda item: cfg.get("autostart", False)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Выход', exit_app)
    )
    
    icon = pystray.Icon("VFD_Monitor", create_image(), "VFD PC Monitor", menu)
    icon.run()

if __name__ == "__main__":
    main()
