from __future__ import annotations

import threading
from datetime import datetime

from notes_engine import WEEKDAY_LABELS

_gui_lock = threading.Lock()
_gui_state = {"window_open": False}


def open_notes_reminders_window(manager, initial_tab: str = "notes", on_refresh=None):
    if _gui_state["window_open"]:
        return

    def worker():
        import tkinter as tk
        from tkinter import messagebox, ttk

        with _gui_lock:
            _gui_state["window_open"] = True
            try:
                root = tk.Tk()
                root.title("Заметки и напоминания")
                root.geometry("1160x760")
                root.minsize(980, 640)
                root.attributes("-topmost", True)

                state = manager.get_state_snapshot()
                notes = state["notes"]
                reminders = state["reminders"]
                history = state["history"]

                notebook = ttk.Notebook(root)
                notebook.pack(fill="both", expand=True)

                notes_frame = ttk.Frame(notebook, padding=12)
                reminders_frame = ttk.Frame(notebook, padding=12)
                notebook.add(notes_frame, text="Заметки")
                notebook.add(reminders_frame, text="Напоминания")
                if initial_tab == "reminders":
                    notebook.select(reminders_frame)

                history_frame = ttk.LabelFrame(root, text="История", padding=12)
                history_frame.pack(fill="both", expand=False, padx=12, pady=(0, 12))

                root.columnconfigure(0, weight=1)

                # --- Notes UI ---
                notes_pane = ttk.PanedWindow(notes_frame, orient=tk.HORIZONTAL)
                notes_pane.pack(fill="both", expand=True)
                notes_list_wrap = ttk.Frame(notes_pane)
                notes_editor_wrap = ttk.Frame(notes_pane)
                notes_pane.add(notes_list_wrap, weight=2)
                notes_pane.add(notes_editor_wrap, weight=3)

                notes_filter_var = tk.StringVar()
                notes_count_var = tk.StringVar()
                notes_listbox = tk.Listbox(notes_list_wrap, height=18, exportselection=False)
                notes_listbox.pack(fill="both", expand=True, pady=(8, 0))

                ttk.Label(notes_list_wrap, text="Активные заметки показываются циклически на дисплее.").pack(anchor="w")
                ttk.Entry(notes_list_wrap, textvariable=notes_filter_var).pack(fill="x", pady=(8, 0))
                ttk.Label(notes_list_wrap, textvariable=notes_count_var).pack(anchor="w", pady=(8, 0))

                note_text = tk.Text(notes_editor_wrap, height=10, wrap="word")
                note_enabled_var = tk.BooleanVar(value=True)
                note_interval_var = tk.StringVar(value="30")
                note_selected_id = {"value": None}

                ttk.Label(notes_editor_wrap, text="Текст заметки").pack(anchor="w")
                note_text.pack(fill="both", expand=True, pady=(4, 10))
                options_row = ttk.Frame(notes_editor_wrap)
                options_row.pack(fill="x", pady=(0, 10))
                ttk.Checkbutton(options_row, text="Включена", variable=note_enabled_var).pack(side="left")
                ttk.Label(options_row, text="Интервал показа (сек)").pack(side="left", padx=(16, 8))
                ttk.Entry(options_row, textvariable=note_interval_var, width=10).pack(side="left")

                note_buttons = ttk.Frame(notes_editor_wrap)
                note_buttons.pack(fill="x")

                # --- Reminders UI ---
                reminders_pane = ttk.PanedWindow(reminders_frame, orient=tk.HORIZONTAL)
                reminders_pane.pack(fill="both", expand=True)
                reminders_list_wrap = ttk.Frame(reminders_pane)
                reminders_editor_wrap = ttk.Frame(reminders_pane)
                reminders_pane.add(reminders_list_wrap, weight=2)
                reminders_pane.add(reminders_editor_wrap, weight=3)

                reminders_filter_var = tk.StringVar()
                reminders_listbox = tk.Listbox(reminders_list_wrap, height=18, exportselection=False)
                reminders_listbox.pack(fill="both", expand=True, pady=(8, 0))
                ttk.Label(reminders_list_wrap, text="Разовые, ежедневные и недельные напоминания.").pack(anchor="w")
                ttk.Entry(reminders_list_wrap, textvariable=reminders_filter_var).pack(fill="x", pady=(8, 0))

                reminder_selected_id = {"value": None}
                reminder_title_var = tk.StringVar()
                reminder_enabled_var = tk.BooleanVar(value=True)
                reminder_type_var = tk.StringVar(value="date_time")
                reminder_date_var = tk.StringVar(value=datetime.now().date().isoformat())
                reminder_time_var = tk.StringVar(value="09:00")
                weekday_vars = [tk.BooleanVar(value=False) for _ in range(7)]
                reminder_text = tk.Text(reminders_editor_wrap, height=8, wrap="word")

                ttk.Label(reminders_editor_wrap, text="Заголовок").pack(anchor="w")
                ttk.Entry(reminders_editor_wrap, textvariable=reminder_title_var).pack(fill="x", pady=(4, 10))
                type_row = ttk.Frame(reminders_editor_wrap)
                type_row.pack(fill="x", pady=(0, 10))
                ttk.Label(type_row, text="Тип").pack(side="left")
                type_combo = ttk.Combobox(type_row, textvariable=reminder_type_var, values=["date_time", "daily", "weekly"], state="readonly", width=14)
                type_combo.pack(side="left", padx=(8, 16))
                ttk.Label(type_row, text="Дата").pack(side="left")
                ttk.Entry(type_row, textvariable=reminder_date_var, width=12).pack(side="left", padx=(8, 16))
                ttk.Label(type_row, text="Время").pack(side="left")
                ttk.Entry(type_row, textvariable=reminder_time_var, width=8).pack(side="left", padx=(8, 16))
                ttk.Checkbutton(type_row, text="Включено", variable=reminder_enabled_var).pack(side="left")

                weekdays_row = ttk.LabelFrame(reminders_editor_wrap, text="Дни недели")
                weekdays_row.pack(fill="x", pady=(0, 10))
                for idx, label in enumerate(WEEKDAY_LABELS):
                    ttk.Checkbutton(weekdays_row, text=label, variable=weekday_vars[idx]).grid(row=0, column=idx, sticky="w", padx=4, pady=4)

                ttk.Label(reminders_editor_wrap, text="Текст напоминания").pack(anchor="w")
                reminder_text.pack(fill="both", expand=True, pady=(4, 10))
                reminder_buttons = ttk.Frame(reminders_editor_wrap)
                reminder_buttons.pack(fill="x")

                # --- History UI ---
                history_columns = ("type", "title", "action", "created")
                history_tree = ttk.Treeview(history_frame, columns=history_columns, show="headings", height=8)
                history_tree.pack(fill="both", expand=True)
                for col, title, width in [
                    ("type", "Тип", 120),
                    ("title", "Название", 360),
                    ("action", "Действие", 120),
                    ("created", "Когда", 180),
                ]:
                    history_tree.heading(col, text=title)
                    history_tree.column(col, width=width, anchor="w")

                def refresh_history_view():
                    snapshot = manager.get_state_snapshot()
                    for item in history_tree.get_children():
                        history_tree.delete(item)
                    for entry in snapshot["history"][:100]:
                        title = entry.get("title") or entry.get("text")[:40]
                        history_tree.insert("", "end", values=(entry.get("source_type"), title, entry.get("action"), entry.get("created_at")))

                def filtered_notes():
                    snapshot = manager.get_state_snapshot()
                    query = notes_filter_var.get().strip().lower()
                    result = []
                    for item in snapshot["notes"]:
                        hay = f"{item.get('text', '')} {item.get('status', '')}".lower()
                        if query and query not in hay:
                            continue
                        result.append(item)
                    return result

                def filtered_reminders():
                    snapshot = manager.get_state_snapshot()
                    query = reminders_filter_var.get().strip().lower()
                    result = []
                    for item in snapshot["reminders"]:
                        hay = f"{item.get('title', '')} {item.get('text', '')} {item.get('time', '')}".lower()
                        if query and query not in hay:
                            continue
                        result.append(item)
                    return result

                def refresh_notes_view():
                    snapshot = manager.get_state_snapshot()
                    current = filtered_notes()
                    notes_listbox.delete(0, tk.END)
                    active_count = 0
                    for item in current:
                        if item.get("status") == "active" and item.get("enabled"):
                            active_count += 1
                        preview = (item.get("text") or "").replace("\n", " ")[:45] or "(пустая заметка)"
                        suffix = "ON" if item.get("enabled") else "OFF"
                        notes_listbox.insert(tk.END, f"[{suffix}] {item.get('interval_seconds')}с | {preview}")
                    settings = snapshot.get("settings", {})
                    limits = settings.get("limits", {}) if isinstance(settings, dict) else {}
                    note_limit = limits.get("max_active_notes", active_count)
                    reminder_limit = limits.get("max_active_reminders", 0)
                    notes_count_var.set(f"Активно {active_count}/{note_limit} заметок | лимит напоминаний: {reminder_limit}")
                    notes_listbox._items = current

                def refresh_reminders_view():
                    current = filtered_reminders()
                    reminders_listbox.delete(0, tk.END)
                    for item in current:
                        state = "TRIG" if item.get("triggered_at") else ("ON" if item.get("enabled") else "OFF")
                        preview = item.get("title") or (item.get("text") or "Напоминание")[:35]
                        schedule = f"{item.get('reminder_type')} {item.get('date')} {item.get('time')}"
                        reminders_listbox.insert(tk.END, f"[{state}] {schedule} | {preview}")
                    reminders_listbox._items = current

                def refresh_all():
                    refresh_notes_view()
                    refresh_reminders_view()
                    refresh_history_view()
                    if on_refresh:
                        on_refresh()

                def clear_note_form():
                    note_selected_id["value"] = None
                    note_text.delete("1.0", tk.END)
                    note_enabled_var.set(True)
                    note_interval_var.set("30")

                def populate_note_form(item):
                    note_selected_id["value"] = item["id"]
                    note_text.delete("1.0", tk.END)
                    note_text.insert("1.0", item.get("text") or "")
                    note_enabled_var.set(bool(item.get("enabled")))
                    note_interval_var.set(str(item.get("interval_seconds", 30)))

                def get_selected_note():
                    selection = notes_listbox.curselection()
                    if not selection:
                        return None
                    items = getattr(notes_listbox, "_items", [])
                    if selection[0] >= len(items):
                        return None
                    return items[selection[0]]

                def on_note_select(_event=None):
                    item = get_selected_note()
                    if item:
                        populate_note_form(item)

                def save_note():
                    text = note_text.get("1.0", tk.END).strip()
                    if not text:
                        messagebox.showerror("Ошибка", "Введите текст заметки.", parent=root)
                        return
                    try:
                        interval = int(note_interval_var.get().strip())
                    except ValueError:
                        messagebox.showerror("Ошибка", "Интервал должен быть целым числом.", parent=root)
                        return
                    enabled = bool(note_enabled_var.get())
                    try:
                        if note_selected_id["value"]:
                            manager.update_note(note_selected_id["value"], text, interval, enabled)
                        else:
                            manager.add_note(text, interval, enabled)
                        clear_note_form()
                        refresh_all()
                    except Exception as exc:
                        messagebox.showerror("Ошибка", str(exc), parent=root)

                def delete_note():
                    item = get_selected_note()
                    if not item:
                        return
                    if not messagebox.askyesno("Удалить", "Удалить заметку?", parent=root):
                        return
                    try:
                        manager.delete_note(item["id"])
                        clear_note_form()
                        refresh_all()
                    except Exception as exc:
                        messagebox.showerror("Ошибка", str(exc), parent=root)

                def hide_note():
                    item = get_selected_note()
                    if not item:
                        return
                    try:
                        manager.mark_item("note", item["id"], "hide")
                        clear_note_form()
                        refresh_all()
                    except Exception as exc:
                        messagebox.showerror("Ошибка", str(exc), parent=root)

                def done_note():
                    item = get_selected_note()
                    if not item:
                        return
                    try:
                        manager.mark_item("note", item["id"], "done")
                        clear_note_form()
                        refresh_all()
                    except Exception as exc:
                        messagebox.showerror("Ошибка", str(exc), parent=root)

                ttk.Button(note_buttons, text="Новая", command=clear_note_form).pack(side="left", padx=(0, 8))
                ttk.Button(note_buttons, text="Сохранить", command=save_note).pack(side="left", padx=(0, 8))
                ttk.Button(note_buttons, text="Скрыть", command=hide_note).pack(side="left", padx=(0, 8))
                ttk.Button(note_buttons, text="Выполнено", command=done_note).pack(side="left", padx=(0, 8))
                ttk.Button(note_buttons, text="Удалить", command=delete_note).pack(side="left")

                def clear_reminder_form():
                    reminder_selected_id["value"] = None
                    reminder_title_var.set("")
                    reminder_enabled_var.set(True)
                    reminder_type_var.set("date_time")
                    reminder_date_var.set(datetime.now().date().isoformat())
                    reminder_time_var.set("09:00")
                    reminder_text.delete("1.0", tk.END)
                    for var in weekday_vars:
                        var.set(False)

                def populate_reminder_form(item):
                    reminder_selected_id["value"] = item["id"]
                    reminder_title_var.set(item.get("title") or "")
                    reminder_enabled_var.set(bool(item.get("enabled")))
                    reminder_type_var.set(item.get("reminder_type") or "date_time")
                    reminder_date_var.set(item.get("date") or datetime.now().date().isoformat())
                    reminder_time_var.set(item.get("time") or "09:00")
                    reminder_text.delete("1.0", tk.END)
                    reminder_text.insert("1.0", item.get("text") or "")
                    weekdays = set(item.get("weekdays") or [])
                    for idx, var in enumerate(weekday_vars):
                        var.set(idx in weekdays)

                def get_selected_reminder():
                    selection = reminders_listbox.curselection()
                    if not selection:
                        return None
                    items = getattr(reminders_listbox, "_items", [])
                    if selection[0] >= len(items):
                        return None
                    return items[selection[0]]

                def on_reminder_select(_event=None):
                    item = get_selected_reminder()
                    if item:
                        populate_reminder_form(item)

                def save_reminder():
                    title = reminder_title_var.get().strip()
                    text = reminder_text.get("1.0", tk.END).strip()
                    reminder_type = reminder_type_var.get().strip()
                    date_value = reminder_date_var.get().strip()
                    time_value = reminder_time_var.get().strip()
                    weekdays = [idx for idx, var in enumerate(weekday_vars) if var.get()]
                    enabled = bool(reminder_enabled_var.get())
                    if reminder_type == "weekly" and not weekdays:
                        messagebox.showerror("Ошибка", "Для weekly нужно выбрать хотя бы один день недели.", parent=root)
                        return
                    try:
                        if reminder_selected_id["value"]:
                            manager.update_reminder(reminder_selected_id["value"], title, text, reminder_type, date_value, time_value, weekdays, enabled)
                        else:
                            manager.add_reminder(title, text, reminder_type, date_value, time_value, weekdays, enabled)
                        clear_reminder_form()
                        refresh_all()
                    except Exception as exc:
                        messagebox.showerror("Ошибка", str(exc), parent=root)

                def delete_reminder():
                    item = get_selected_reminder()
                    if not item:
                        return
                    if not messagebox.askyesno("Удалить", "Удалить напоминание?", parent=root):
                        return
                    try:
                        manager.delete_reminder(item["id"])
                        clear_reminder_form()
                        refresh_all()
                    except Exception as exc:
                        messagebox.showerror("Ошибка", str(exc), parent=root)

                def hide_reminder():
                    item = get_selected_reminder()
                    if not item:
                        return
                    try:
                        manager.mark_item("reminder", item["id"], "hide")
                        clear_reminder_form()
                        refresh_all()
                    except Exception as exc:
                        messagebox.showerror("Ошибка", str(exc), parent=root)

                def done_reminder():
                    item = get_selected_reminder()
                    if not item:
                        return
                    try:
                        manager.mark_item("reminder", item["id"], "done")
                        clear_reminder_form()
                        refresh_all()
                    except Exception as exc:
                        messagebox.showerror("Ошибка", str(exc), parent=root)

                ttk.Button(reminder_buttons, text="Новое", command=clear_reminder_form).pack(side="left", padx=(0, 8))
                ttk.Button(reminder_buttons, text="Сохранить", command=save_reminder).pack(side="left", padx=(0, 8))
                ttk.Button(reminder_buttons, text="Скрыть", command=hide_reminder).pack(side="left", padx=(0, 8))
                ttk.Button(reminder_buttons, text="Выполнено", command=done_reminder).pack(side="left", padx=(0, 8))
                ttk.Button(reminder_buttons, text="Удалить", command=delete_reminder).pack(side="left")

                notes_listbox.bind("<<ListboxSelect>>", on_note_select)
                reminders_listbox.bind("<<ListboxSelect>>", on_reminder_select)
                notes_filter_var.trace_add("write", lambda *_: refresh_notes_view())
                reminders_filter_var.trace_add("write", lambda *_: refresh_reminders_view())

                def on_close():
                    _gui_state["window_open"] = False
                    root.destroy()

                root.protocol("WM_DELETE_WINDOW", on_close)
                refresh_all()
                root.mainloop()
            finally:
                _gui_state["window_open"] = False

    threading.Thread(target=worker, daemon=True).start()
