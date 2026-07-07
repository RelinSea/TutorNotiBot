import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from bot.database import Database


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.db = Database(str(Path(self.tmp.name) / "test.sqlite3"))
        self.db.init()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_owner_invite_student_lesson_flow(self) -> None:
        self.assertFalse(self.db.owner_exists())

        teacher = self.db.upsert_teacher(1, "Teacher")
        self.assertTrue(self.db.owner_exists())

        code = self.db.create_invite(teacher["id"])
        status, invite = self.db.accept_invite(code, 2, "Student")

        self.assertEqual(status, "ok")
        self.assertEqual(invite["teacher_name"], "Teacher")

        students = self.db.list_students(teacher["id"])
        self.assertEqual(len(students), 1)
        self.assertTrue(self.db.is_student(2))

        lesson_id, created = self.db.create_lesson(
            teacher["id"],
            students[0]["id"],
            datetime.now(timezone.utc) + timedelta(days=2),
            ["day", "hour", "10m"],
        )

        self.assertGreater(lesson_id, 0)
        self.assertEqual(created, 3)
        self.assertEqual(len(self.db.list_upcoming_lessons(teacher["id"])), 1)

        changed = self.db.update_lesson(
            teacher["id"],
            lesson_id,
            datetime.now(timezone.utc) + timedelta(days=3),
            ["day", "hour"],
        )

        self.assertEqual(changed, 2)
        self.assertTrue(self.db.delete_lesson(teacher["id"], lesson_id))
        self.assertEqual(len(self.db.list_upcoming_lessons(teacher["id"])), 0)

    def test_invite_cannot_be_reused_by_another_student(self) -> None:
        teacher = self.db.upsert_teacher(1, "Teacher")
        code = self.db.create_invite(teacher["id"])

        first_status, _ = self.db.accept_invite(code, 2, "First Student")
        second_status, _ = self.db.accept_invite(code, 3, "Second Student")

        self.assertEqual(first_status, "ok")
        self.assertEqual(second_status, "used")
        self.assertEqual(len(self.db.list_students(teacher["id"])), 1)

    def test_teacher_cannot_accept_own_invite(self) -> None:
        teacher = self.db.upsert_teacher(1, "Teacher")
        code = self.db.create_invite(teacher["id"])

        status, _ = self.db.accept_invite(code, 1, "Teacher")

        self.assertEqual(status, "self")
        self.assertEqual(len(self.db.list_students(teacher["id"])), 0)

    def test_teacher_setup_completion_is_persistent(self) -> None:
        teacher = self.db.upsert_teacher(1, "Teacher")

        self.assertFalse(self.db.is_setup_completed(teacher["id"]))
        self.db.mark_setup_completed(teacher["id"])
        self.db.mark_setup_completed(teacher["id"])

        self.assertTrue(self.db.is_setup_completed(teacher["id"]))

    def test_delete_student_removes_related_lessons(self) -> None:
        teacher = self.db.upsert_teacher(1, "Teacher")
        code = self.db.create_invite(teacher["id"])
        self.db.accept_invite(code, 2, "Student")
        student = self.db.list_students(teacher["id"])[0]

        self.db.create_lesson(
            teacher["id"],
            student["id"],
            datetime.now(timezone.utc) + timedelta(days=2),
            ["day", "hour"],
        )

        self.assertEqual(len(self.db.list_upcoming_lessons(teacher["id"])), 1)
        self.assertTrue(self.db.delete_student(teacher["id"], student["id"]))
        self.assertEqual(len(self.db.list_students(teacher["id"])), 0)
        self.assertEqual(len(self.db.list_upcoming_lessons(teacher["id"])), 0)

    def test_create_lessons_for_multiple_students_and_dates(self) -> None:
        teacher = self.db.upsert_teacher(1, "Teacher")
        first_code = self.db.create_invite(teacher["id"])
        self.db.accept_invite(first_code, 2, "First Student")
        second_code = self.db.create_invite(teacher["id"])
        self.db.accept_invite(second_code, 3, "Second Student")
        student_ids = [int(student["id"]) for student in self.db.list_students(teacher["id"])]
        starts = [
            datetime.now(timezone.utc) + timedelta(days=2),
            datetime.now(timezone.utc) + timedelta(days=9),
        ]

        self.assertFalse(self.db.has_any_schedule(teacher["id"]))
        lessons_count, reminders_count = self.db.create_lessons(
            teacher["id"],
            student_ids,
            starts,
            ["day", "hour"],
        )

        self.assertEqual(lessons_count, 4)
        self.assertEqual(reminders_count, 8)
        self.assertEqual(len(self.db.list_upcoming_lessons(teacher["id"])), 4)
        self.assertTrue(self.db.has_any_schedule(teacher["id"]))

    def test_create_schedule_rules_for_multiple_students(self) -> None:
        teacher = self.db.upsert_teacher(1, "Teacher")
        first_code = self.db.create_invite(teacher["id"])
        self.db.accept_invite(first_code, 2, "First Student")
        second_code = self.db.create_invite(teacher["id"])
        self.db.accept_invite(second_code, 3, "Second Student")
        student_ids = [int(student["id"]) for student in self.db.list_students(teacher["id"])]

        self.assertFalse(self.db.has_any_schedule(teacher["id"]))
        rules_count = self.db.create_schedule_rules(
            teacher["id"],
            student_ids,
            [1, 3],
            "18:00",
            "2026-07-07",
            "2026-12-31",
            ["day", "hour"],
        )
        rules = self.db.list_active_schedule_rules(teacher["id"])

        self.assertEqual(rules_count, 2)
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0]["weekdays"], "1,3")
        self.assertEqual(len(self.db.list_upcoming_lessons(teacher["id"])), 0)
        self.assertTrue(self.db.has_any_schedule(teacher["id"]))

    def test_reminder_logs_are_unique_by_occurrence_and_kind(self) -> None:
        self.db.mark_rule_reminder_sent("rule:1:2026-07-07T18:00", "day")
        self.db.mark_rule_reminder_sent("rule:1:2026-07-07T18:00", "day")

        self.assertTrue(self.db.reminder_log_exists("rule:1:2026-07-07T18:00", "day"))

    def test_past_reminders_are_not_created(self) -> None:
        teacher = self.db.upsert_teacher(1, "Teacher")
        code = self.db.create_invite(teacher["id"])
        self.db.accept_invite(code, 2, "Student")
        student = self.db.list_students(teacher["id"])[0]

        _lesson_id, created = self.db.create_lesson(
            teacher["id"],
            student["id"],
            datetime.now(timezone.utc) + timedelta(minutes=30),
            ["day", "hour", "10m"],
        )

        self.assertEqual(created, 1)


if __name__ == "__main__":
    unittest.main()
