from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import time
import unittest

from alerts_engine import DEFAULT_STATE as ALERTS_DEFAULT_STATE, TelegramAlertManager, build_alert_from_telegram_message
from notes_engine import DEFAULT_STATE as NOTES_DEFAULT_STATE, NotesReminderManager


class OverlayPriorityTests(unittest.TestCase):
    def build_notes_manager(self):
        store = {
            "notes_reminders": deepcopy(NOTES_DEFAULT_STATE["notes_reminders"]),
        }
        store["notes_reminders"]["settings"]["display"]["note_page_duration_seconds"] = 0.5
        store["notes_reminders"]["settings"]["display"]["reminder_page_duration_seconds"] = 0.5

        def get_cfg():
            return store

        def save_cfg(new_cfg):
            store.clear()
            store.update(deepcopy(new_cfg))

        return NotesReminderManager(get_cfg, save_cfg), store

    def build_alert_manager(self):
        store = {
            "telegram_alerts": deepcopy(ALERTS_DEFAULT_STATE["telegram_alerts"]),
        }

        def get_cfg():
            return store

        def save_cfg(new_cfg):
            store.clear()
            store.update(deepcopy(new_cfg))

        return TelegramAlertManager(get_cfg, save_cfg), store

    def test_reminder_has_priority_over_note(self):
        manager, _ = self.build_notes_manager()
        manager.add_note("Заметка", 5, True)

        now = datetime.now().replace(second=0, microsecond=0)
        manager.add_reminder(
            title="Срочно",
            text="Напоминание важнее заметки",
            reminder_type="date_time",
            date_value=now.date().isoformat(),
            time_value=now.strftime("%H:%M"),
            weekdays=[],
            enabled=True,
        )

        current = manager.get_current_display(now=now)
        self.assertIsNotNone(current)
        self.assertEqual(current.kind, "reminder")
        self.assertEqual(current.title, "Срочно")

    def test_suspension_postpones_note_pagination_under_alert(self):
        manager, _ = self.build_notes_manager()
        manager.add_note("Первая строка\nВторая строка\nТретья строка", 5, True)

        first = manager.get_current_display(now=datetime.now())
        self.assertIsNotNone(first)
        self.assertEqual(first.kind, "note")
        self.assertEqual(first.page_index, 0)

        time.sleep(0.2)
        manager.set_suspended(True)
        time.sleep(0.8)
        manager.set_suspended(False)

        still_first = manager.get_current_display(now=datetime.now())
        self.assertIsNotNone(still_first)
        self.assertEqual(still_first.kind, "note")
        self.assertEqual(still_first.page_index, 0)

        time.sleep(0.6)
        second = manager.get_current_display(now=datetime.now())
        self.assertIsNotNone(second)
        self.assertEqual(second.page_index, 1)

    def test_alert_manager_keeps_multiline_text_and_history(self):
        manager, _ = self.build_alert_manager()
        manager.update_settings(bot_token="token", enabled=True, use_whitelist=True, allowed_users=["allowed_user", "123"])
        alert = build_alert_from_telegram_message(
            {
                "text": "Первая\nВторая\nТретья",
                "from": {"id": 123, "username": "allowed_user", "first_name": "Allowed"},
                "chat": {"id": 999},
            },
            update_id=10,
        )

        self.assertIsNotNone(alert)
        self.assertTrue(manager.is_sender_allowed(alert["author_username"], alert["author_id"]))
        manager.enqueue_alert(alert)
        current = manager.get_current_display()
        history_entry = manager.get_state_snapshot()["history"][0]

        self.assertIsNotNone(current)
        self.assertEqual(current.line1, "@allowed_user")
        self.assertGreaterEqual(current.page_count, 2)
        self.assertEqual(history_entry["alert"]["text"], "Первая\nВторая\nТретья")


if __name__ == "__main__":
    unittest.main()
