from __future__ import annotations

import threading

_gui_lock = threading.Lock()
_gui_state = {"window_open": False}


def open_alerts_window(manager, on_refresh=None):
    if _gui_state["window_open"]:
        return

    def worker():
        import tkinter as tk
        from tkinter import messagebox, ttk

        with _gui_lock:
            _gui_state["window_open"] = True
            try:
                root = tk.Tk()
                root.title("Telegram alerts")
                root.geometry("1260x820")
                root.minsize(1080, 700)
                root.attributes("-topmost", True)

                state = manager.get_state_snapshot()

                enabled_var = tk.BooleanVar(value=state.get("enabled", False))
                token_var = tk.StringVar(value=state.get("bot_token") or "")
                whitelist_var = tk.BooleanVar(value=state.get("use_whitelist", False))
                poll_interval_var = tk.StringVar(value=str(state.get("poll_interval_seconds", 2.0)))
                page_duration_var = tk.StringVar(value=str(state.get("display_page_duration_seconds", 3.2)))
                status_var = tk.StringVar(value=state.get("last_error") or "Ожидание сообщений")
                bot_username_var = tk.StringVar(value=state.get("bot_username") or "")
                active_count_var = tk.StringVar()
                history_count_var = tk.StringVar()

                main = ttk.Frame(root, padding=12)
                main.pack(fill="both", expand=True)
                main.columnconfigure(0, weight=1)
                main.rowconfigure(1, weight=1)
                main.rowconfigure(2, weight=1)

                settings = ttk.LabelFrame(main, text="Настройки Telegram alerts", padding=12)
                settings.grid(row=0, column=0, sticky="nsew")
                settings.columnconfigure(1, weight=1)

                ttk.Checkbutton(settings, text="Включить приём alerts", variable=enabled_var).grid(row=0, column=0, sticky="w")
                ttk.Checkbutton(settings, text="Использовать whitelist", variable=whitelist_var).grid(row=0, column=1, sticky="w", padx=(12, 0))

                ttk.Label(settings, text="Bot token").grid(row=1, column=0, sticky="w", pady=(12, 4))
                ttk.Entry(settings, textvariable=token_var).grid(row=2, column=0, columnspan=2, sticky="ew")

                ttk.Label(settings, text="Имя бота (для справки)").grid(row=3, column=0, sticky="w", pady=(12, 4))
                ttk.Entry(settings, textvariable=bot_username_var).grid(row=4, column=0, columnspan=2, sticky="ew")

                timing = ttk.Frame(settings)
                timing.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
                ttk.Label(timing, text="Пауза polling (сек)").pack(side="left")
                ttk.Entry(timing, textvariable=poll_interval_var, width=8).pack(side="left", padx=(8, 20))
                ttk.Label(timing, text="Сек на страницу").pack(side="left")
                ttk.Entry(timing, textvariable=page_duration_var, width=8).pack(side="left", padx=(8, 0))

                whitelist_frame = ttk.LabelFrame(settings, text="Whitelist: username без @ или numeric user id", padding=8)
                whitelist_frame.grid(row=6, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
                whitelist_text = tk.Text(whitelist_frame, height=6, wrap="word")
                whitelist_text.pack(fill="both", expand=True)
                whitelist_text.insert("1.0", "\n".join(state.get("allowed_users") or []))

                buttons = ttk.Frame(settings)
                buttons.grid(row=7, column=0, columnspan=2, sticky="w", pady=(12, 0))

                lists = ttk.PanedWindow(main, orient=tk.VERTICAL)
                lists.grid(row=1, column=0, sticky="nsew", pady=(12, 0))

                active_frame = ttk.LabelFrame(lists, text="Активные alerts", padding=12)
                history_frame = ttk.LabelFrame(lists, text="История alerts", padding=12)
                lists.add(active_frame, weight=1)
                lists.add(history_frame, weight=1)

                active_frame.columnconfigure(0, weight=1)
                active_frame.rowconfigure(1, weight=1)
                history_frame.columnconfigure(0, weight=1)
                history_frame.rowconfigure(1, weight=1)

                ttk.Label(active_frame, textvariable=active_count_var).grid(row=0, column=0, sticky="w")
                active_columns = ("author", "received")
                active_tree = ttk.Treeview(active_frame, columns=active_columns, show="headings", height=10)
                active_tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
                for column, title, width in (("author", "Автор", 260), ("received", "Получено", 180)):
                    active_tree.heading(column, text=title)
                    active_tree.column(column, width=width, anchor="w")
                active_scroll = ttk.Scrollbar(active_frame, orient="vertical", command=active_tree.yview)
                active_scroll.grid(row=1, column=1, sticky="ns", pady=(8, 0))
                active_tree.configure(yscrollcommand=active_scroll.set)
                active_preview = tk.Text(active_frame, height=8, wrap="word")
                active_preview.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(10, 0))

                active_buttons = ttk.Frame(active_frame)
                active_buttons.grid(row=3, column=0, sticky="w", pady=(10, 0))

                ttk.Label(history_frame, textvariable=history_count_var).grid(row=0, column=0, sticky="w")
                history_columns = ("action", "author", "created")
                history_tree = ttk.Treeview(history_frame, columns=history_columns, show="headings", height=12)
                history_tree.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
                for column, title, width in (("action", "Действие", 120), ("author", "Автор", 260), ("created", "Когда", 180)):
                    history_tree.heading(column, text=title)
                    history_tree.column(column, width=width, anchor="w")
                history_scroll = ttk.Scrollbar(history_frame, orient="vertical", command=history_tree.yview)
                history_scroll.grid(row=1, column=1, sticky="ns", pady=(8, 0))
                history_tree.configure(yscrollcommand=history_scroll.set)
                history_preview = tk.Text(history_frame, height=10, wrap="word")
                history_preview.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(10, 0))

                active_items = []
                history_items = []

                def format_alert_preview(alert):
                    if not alert:
                        return ""
                    parts = [
                        f"Автор: {alert.get('author_name') or 'Telegram'}",
                        f"User ID: {alert.get('author_id') or '-'}",
                        f"Username: @{alert.get('author_username')}" if alert.get('author_username') else "Username: -",
                        f"Получено: {alert.get('received_at') or '-'}",
                        "",
                        alert.get('text') or "(пустой текст)",
                    ]
                    return "\n".join(parts)

                def refresh_active_view():
                    nonlocal active_items
                    snapshot = manager.get_state_snapshot()
                    active_items = snapshot.get("active_alerts") or []
                    active_count_var.set(f"В очереди: {len(active_items)}")
                    active_tree.delete(*active_tree.get_children())
                    for alert in active_items:
                        active_tree.insert("", "end", iid=alert["id"], values=(alert.get("author_name"), alert.get("received_at")))
                    if active_items:
                        first = active_items[0]
                        if not active_tree.selection():
                            active_tree.selection_set(first["id"])
                            active_tree.focus(first["id"])
                        on_active_select()
                    else:
                        active_preview.delete("1.0", tk.END)

                def refresh_history_view():
                    nonlocal history_items
                    snapshot = manager.get_state_snapshot()
                    history_items = snapshot.get("history") or []
                    history_count_var.set(f"Записей в истории: {len(history_items)}")
                    history_tree.delete(*history_tree.get_children())
                    for entry in history_items:
                        alert = entry.get("alert") or {}
                        history_tree.insert("", "end", iid=entry["id"], values=(entry.get("action"), alert.get("author_name"), entry.get("created_at")))
                    if history_items:
                        first = history_items[0]
                        if not history_tree.selection():
                            history_tree.selection_set(first["id"])
                            history_tree.focus(first["id"])
                        on_history_select()
                    else:
                        history_preview.delete("1.0", tk.END)

                def refresh_status():
                    snapshot = manager.get_state_snapshot()
                    error = snapshot.get("last_error") or ""
                    username = snapshot.get("bot_username") or ""
                    if error:
                        status_var.set(error)
                    elif username:
                        status_var.set(f"Бот: @{username}. Ожидание сообщений.")
                    else:
                        status_var.set("Ожидание сообщений")

                def refresh_all():
                    refresh_active_view()
                    refresh_history_view()
                    refresh_status()
                    if on_refresh:
                        on_refresh()

                def get_selected_active():
                    selected = active_tree.selection()
                    if not selected:
                        return None
                    for alert in active_items:
                        if alert["id"] == selected[0]:
                            return alert
                    return None

                def get_selected_history():
                    selected = history_tree.selection()
                    if not selected:
                        return None
                    for entry in history_items:
                        if entry["id"] == selected[0]:
                            return entry
                    return None

                def on_active_select(_event=None):
                    alert = get_selected_active()
                    active_preview.delete("1.0", tk.END)
                    active_preview.insert("1.0", format_alert_preview(alert))

                def on_history_select(_event=None):
                    entry = get_selected_history()
                    history_preview.delete("1.0", tk.END)
                    if entry:
                        history_preview.insert("1.0", f"Действие: {entry.get('action')}\nДата: {entry.get('created_at')}\n\n{format_alert_preview(entry.get('alert') or {})}")

                def save_settings():
                    allowed_users = whitelist_text.get("1.0", tk.END)
                    try:
                        manager.update_settings(
                            enabled=enabled_var.get(),
                            bot_token=token_var.get(),
                            bot_username=bot_username_var.get(),
                            use_whitelist=whitelist_var.get(),
                            allowed_users=allowed_users,
                            poll_interval=float(poll_interval_var.get().strip()),
                            display_page_duration=float(page_duration_var.get().strip()),
                        )
                        refresh_all()
                        messagebox.showinfo("Сохранено", "Настройки alerts сохранены.", parent=root)
                    except Exception as exc:
                        messagebox.showerror("Ошибка", str(exc), parent=root)

                def toggle_enabled():
                    manager.set_enabled(not manager.get_state_snapshot().get("enabled", False))
                    enabled_var.set(manager.get_state_snapshot().get("enabled", False))
                    refresh_all()

                def dismiss_selected(action: str):
                    alert = get_selected_active()
                    if not alert:
                        return
                    try:
                        manager.dismiss_alert(alert["id"], action=action)
                        refresh_all()
                    except Exception as exc:
                        messagebox.showerror("Ошибка", str(exc), parent=root)

                ttk.Button(buttons, text="Сохранить настройки", command=save_settings).pack(side="left", padx=(0, 8))
                ttk.Button(buttons, text="Вкл/Выкл через форму", command=toggle_enabled).pack(side="left", padx=(0, 8))
                ttk.Label(buttons, textvariable=status_var).pack(side="left", padx=(12, 0))

                ttk.Button(active_buttons, text="Скрыть выбранный", command=lambda: dismiss_selected("hidden")).pack(side="left", padx=(0, 8))
                ttk.Button(active_buttons, text="Выполнено", command=lambda: dismiss_selected("done")).pack(side="left", padx=(0, 8))
                ttk.Button(active_buttons, text="Обновить", command=refresh_all).pack(side="left")

                active_tree.bind("<<TreeviewSelect>>", on_active_select)
                history_tree.bind("<<TreeviewSelect>>", on_history_select)

                def on_close():
                    _gui_state["window_open"] = False
                    root.destroy()

                root.protocol("WM_DELETE_WINDOW", on_close)
                refresh_all()
                root.mainloop()
            finally:
                _gui_state["window_open"] = False

    threading.Thread(target=worker, daemon=True).start()
