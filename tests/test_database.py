import sqlite3
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

    def test_multiple_teachers_can_register_and_invite_students(self) -> None:
        first_teacher = self.db.upsert_teacher(1, "First Teacher")
        second_teacher = self.db.upsert_teacher(2, "Second Teacher")

        first_status, first_invite = self.db.accept_invite(
            self.db.create_invite(first_teacher["id"]),
            10,
            "First Student",
        )
        second_status, second_invite = self.db.accept_invite(
            self.db.create_invite(second_teacher["id"]),
            20,
            "Second Student",
        )

        self.assertEqual(first_status, "ok")
        self.assertEqual(second_status, "ok")
        self.assertEqual(first_invite["teacher_name"], "First Teacher")
        self.assertEqual(second_invite["teacher_name"], "Second Teacher")
        self.assertEqual(len(self.db.list_students(first_teacher["id"])), 1)
        self.assertEqual(len(self.db.list_students(second_teacher["id"])), 1)

    def test_delete_teacher_by_telegram_removes_teacher_data_only(self) -> None:
        first_teacher = self.db.upsert_teacher(1, "First Teacher")
        second_teacher = self.db.upsert_teacher(2, "Second Teacher")
        first_code = self.db.create_invite(first_teacher["id"])
        second_code = self.db.create_invite(second_teacher["id"])
        self.db.accept_invite(first_code, 10, "First Student")
        self.db.accept_invite(second_code, 20, "Second Student")
        first_student = self.db.list_students(first_teacher["id"])[0]
        self.db.create_lesson(
            first_teacher["id"],
            first_student["id"],
            datetime.now(timezone.utc) + timedelta(days=2),
            ["day", "hour"],
        )

        self.assertTrue(self.db.delete_teacher_by_telegram(1))

        self.assertIsNone(self.db.get_teacher_by_telegram(1))
        self.assertIsNotNone(self.db.get_teacher_by_telegram(2))
        self.assertEqual(len(self.db.list_students(first_teacher["id"])), 0)
        self.assertEqual(len(self.db.list_upcoming_lessons(first_teacher["id"])), 0)
        self.assertEqual(len(self.db.list_students(second_teacher["id"])), 1)
        self.assertTrue(self.db.is_student(20))

    def test_lesson_creation_rejects_student_from_another_teacher(self) -> None:
        first_teacher = self.db.upsert_teacher(1, "First Teacher")
        second_teacher = self.db.upsert_teacher(2, "Second Teacher")
        self.db.accept_invite(self.db.create_invite(first_teacher["id"]), 10, "First Student")
        self.db.accept_invite(self.db.create_invite(second_teacher["id"]), 20, "Second Student")
        first_student = self.db.list_students(first_teacher["id"])[0]
        second_student = self.db.list_students(second_teacher["id"])[0]

        with self.assertRaisesRegex(ValueError, "students"):
            self.db.create_lesson(
                first_teacher["id"],
                second_student["id"],
                datetime.now(timezone.utc) + timedelta(days=2),
                ["day"],
            )
        with self.assertRaisesRegex(ValueError, "students"):
            self.db.create_lessons(
                first_teacher["id"],
                [first_student["id"], second_student["id"]],
                [datetime.now(timezone.utc) + timedelta(days=2)],
                ["day"],
            )

        self.assertEqual(len(self.db.list_upcoming_lessons(first_teacher["id"])), 0)
        self.assertEqual(len(self.db.list_upcoming_lessons(second_teacher["id"])), 0)

    def test_schedule_rule_creation_rejects_student_from_another_teacher(self) -> None:
        first_teacher = self.db.upsert_teacher(1, "First Teacher")
        second_teacher = self.db.upsert_teacher(2, "Second Teacher")
        self.db.accept_invite(self.db.create_invite(first_teacher["id"]), 10, "First Student")
        self.db.accept_invite(self.db.create_invite(second_teacher["id"]), 20, "Second Student")
        first_student = self.db.list_students(first_teacher["id"])[0]
        second_student = self.db.list_students(second_teacher["id"])[0]

        with self.assertRaisesRegex(ValueError, "students"):
            self.db.create_schedule_rules(
                first_teacher["id"],
                [first_student["id"], second_student["id"]],
                [1, 3],
                "18:00",
                "2026-07-07",
                "2026-12-31",
                ["day", "hour"],
            )

        self.assertEqual(len(self.db.list_active_schedule_rules(first_teacher["id"])), 0)
        self.assertEqual(len(self.db.list_active_schedule_rules(second_teacher["id"])), 0)

    def test_database_trigger_rejects_cross_teacher_lesson_insert(self) -> None:
        first_teacher = self.db.upsert_teacher(1, "First Teacher")
        second_teacher = self.db.upsert_teacher(2, "Second Teacher")
        self.db.accept_invite(self.db.create_invite(second_teacher["id"]), 20, "Second Student")
        second_student = self.db.list_students(second_teacher["id"])[0]

        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO lessons (teacher_id, student_id, starts_at, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        first_teacher["id"],
                        second_student["id"],
                        datetime.now(timezone.utc).isoformat(),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

    def test_database_trigger_rejects_cross_teacher_schedule_rule_insert(self) -> None:
        first_teacher = self.db.upsert_teacher(1, "First Teacher")
        second_teacher = self.db.upsert_teacher(2, "Second Teacher")
        self.db.accept_invite(self.db.create_invite(second_teacher["id"]), 20, "Second Student")
        second_student = self.db.list_students(second_teacher["id"])[0]

        with self.assertRaises(sqlite3.IntegrityError):
            with self.db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO schedule_rules (
                        teacher_id, student_id, weekdays, lesson_time,
                        start_date, end_date, reminders, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        first_teacher["id"],
                        second_student["id"],
                        "1,3",
                        "18:00",
                        "2026-07-07",
                        "2026-12-31",
                        "day,hour",
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

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
