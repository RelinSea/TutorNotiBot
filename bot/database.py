import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from bot.models import REMINDER_OPTIONS
from bot.utils import to_utc_iso, utc_now_iso


class Database:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS teachers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    setup_completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS students (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    teacher_id INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
                    telegram_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(teacher_id, telegram_id)
                );

                CREATE TABLE IF NOT EXISTS invites (
                    code TEXT PRIMARY KEY,
                    teacher_id INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    used_at TEXT,
                    used_by INTEGER
                );

                CREATE TABLE IF NOT EXISTS schedule_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    teacher_id INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
                    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                    weekdays TEXT NOT NULL,
                    lesson_time TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT,
                    reminders TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    teacher_id INTEGER NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
                    student_id INTEGER NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                    schedule_rule_id INTEGER REFERENCES schedule_rules(id) ON DELETE CASCADE,
                    starts_at TEXT NOT NULL,
                    original_starts_at TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reminder_logs (
                    occurrence_key TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY (occurrence_key, kind)
                );

                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    sent_at TEXT,
                    UNIQUE(lesson_id, kind)
                );

                CREATE INDEX IF NOT EXISTS idx_lessons_teacher_time
                    ON lessons(teacher_id, starts_at);
                CREATE INDEX IF NOT EXISTS idx_schedule_rules_teacher
                    ON schedule_rules(teacher_id, is_active);
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(sent_at, remind_at);

                CREATE TRIGGER IF NOT EXISTS lessons_student_teacher_insert
                BEFORE INSERT ON lessons
                WHEN NOT EXISTS (
                    SELECT 1 FROM students
                    WHERE students.id = NEW.student_id
                      AND students.teacher_id = NEW.teacher_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'student does not belong to teacher');
                END;

                CREATE TRIGGER IF NOT EXISTS lessons_student_teacher_update
                BEFORE UPDATE OF teacher_id, student_id ON lessons
                WHEN NOT EXISTS (
                    SELECT 1 FROM students
                    WHERE students.id = NEW.student_id
                      AND students.teacher_id = NEW.teacher_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'student does not belong to teacher');
                END;

                CREATE TRIGGER IF NOT EXISTS schedule_rules_student_teacher_insert
                BEFORE INSERT ON schedule_rules
                WHEN NOT EXISTS (
                    SELECT 1 FROM students
                    WHERE students.id = NEW.student_id
                      AND students.teacher_id = NEW.teacher_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'student does not belong to teacher');
                END;

                CREATE TRIGGER IF NOT EXISTS schedule_rules_student_teacher_update
                BEFORE UPDATE OF teacher_id, student_id ON schedule_rules
                WHEN NOT EXISTS (
                    SELECT 1 FROM students
                    WHERE students.id = NEW.student_id
                      AND students.teacher_id = NEW.teacher_id
                )
                BEGIN
                    SELECT RAISE(ABORT, 'student does not belong to teacher');
                END;
                """
            )
            self._ensure_teacher_columns(conn)
            self._ensure_lesson_columns(conn)

    def _ensure_teacher_columns(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(teachers)").fetchall()
        }
        if "setup_completed_at" not in columns:
            conn.execute("ALTER TABLE teachers ADD COLUMN setup_completed_at TEXT")

    def _ensure_lesson_columns(self, conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(lessons)").fetchall()
        }
        if "schedule_rule_id" not in columns:
            conn.execute("ALTER TABLE lessons ADD COLUMN schedule_rule_id INTEGER")
        if "original_starts_at" not in columns:
            conn.execute("ALTER TABLE lessons ADD COLUMN original_starts_at TEXT")
        if "status" not in columns:
            conn.execute("ALTER TABLE lessons ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")

    def owner_exists(self) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM teachers LIMIT 1").fetchone()
            return row is not None

    def upsert_teacher(self, telegram_id: int, name: str) -> sqlite3.Row:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO teachers (telegram_id, name, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET name = excluded.name
                """,
                (telegram_id, name, now),
            )
            return conn.execute(
                "SELECT * FROM teachers WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()

    def get_teacher_by_telegram(self, telegram_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM teachers WHERE telegram_id = ?", (telegram_id,)
            ).fetchone()

    def delete_teacher_by_telegram(self, telegram_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM teachers WHERE telegram_id = ?",
                (telegram_id,),
            )
            return cursor.rowcount > 0

    def is_student(self, telegram_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM students WHERE telegram_id = ? LIMIT 1", (telegram_id,)
            ).fetchone()
            return row is not None

    def is_setup_completed(self, teacher_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM teachers
                WHERE id = ? AND setup_completed_at IS NOT NULL
                LIMIT 1
                """,
                (teacher_id,),
            ).fetchone()
            return row is not None

    def mark_setup_completed(self, teacher_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE teachers
                SET setup_completed_at = COALESCE(setup_completed_at, ?)
                WHERE id = ?
                """,
                (utc_now_iso(), teacher_id),
            )

    def create_invite(self, teacher_id: int) -> str:
        now = utc_now_iso()
        with self.connect() as conn:
            while True:
                code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8]
                try:
                    conn.execute(
                        "INSERT INTO invites (code, teacher_id, created_at) VALUES (?, ?, ?)",
                        (code, teacher_id, now),
                    )
                    return code
                except sqlite3.IntegrityError:
                    continue

    def accept_invite(
        self, code: str, telegram_id: int, name: str
    ) -> tuple[str, sqlite3.Row | None]:
        now = utc_now_iso()
        with self.connect() as conn:
            invite = conn.execute(
                """
                SELECT invites.*, teachers.name AS teacher_name
                     , teachers.telegram_id AS teacher_telegram_id
                FROM invites
                JOIN teachers ON teachers.id = invites.teacher_id
                WHERE invites.code = ?
                """,
                (code,),
            ).fetchone()
            if invite is None:
                return "missing", None
            if invite["teacher_telegram_id"] == telegram_id:
                return "self", invite
            if invite["used_at"] is not None and invite["used_by"] != telegram_id:
                return "used", invite

            conn.execute(
                """
                INSERT INTO students (teacher_id, telegram_id, name, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(teacher_id, telegram_id) DO UPDATE SET name = excluded.name
                """,
                (invite["teacher_id"], telegram_id, name, now),
            )
            conn.execute(
                "UPDATE invites SET used_at = ?, used_by = ? WHERE code = ?",
                (now, telegram_id, code),
            )
            return "ok", invite

    def list_students(self, teacher_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM students
                WHERE teacher_id = ?
                ORDER BY name COLLATE NOCASE
                """,
                (teacher_id,),
            ).fetchall()

    def get_student_for_teacher(
        self, teacher_id: int, student_id: int
    ) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM students
                WHERE id = ? AND teacher_id = ?
                """,
                (student_id, teacher_id),
            ).fetchone()

    def list_students_by_ids(
        self,
        teacher_id: int,
        student_ids: list[int],
    ) -> list[sqlite3.Row]:
        if not student_ids:
            return []

        placeholders = ",".join("?" for _ in student_ids)
        with self.connect() as conn:
            return conn.execute(
                f"""
                SELECT * FROM students
                WHERE teacher_id = ? AND id IN ({placeholders})
                ORDER BY name COLLATE NOCASE
                """,
                (teacher_id, *student_ids),
            ).fetchall()

    def delete_student(self, teacher_id: int, student_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM students WHERE id = ? AND teacher_id = ?",
                (student_id, teacher_id),
            )
            return cursor.rowcount > 0

    def create_lesson(
        self,
        teacher_id: int,
        student_id: int,
        starts_at_utc: datetime,
        reminder_kinds: list[str],
    ) -> tuple[int, int]:
        with self.connect() as conn:
            self._ensure_students_belong_to_teacher(conn, teacher_id, [student_id])
            return self._insert_lesson(
                conn,
                teacher_id,
                student_id,
                starts_at_utc,
                reminder_kinds,
            )

    def create_lessons(
        self,
        teacher_id: int,
        student_ids: list[int],
        starts_at_values: list[datetime],
        reminder_kinds: list[str],
    ) -> tuple[int, int]:
        lessons_created = 0
        reminders_created = 0
        with self.connect() as conn:
            self._ensure_students_belong_to_teacher(conn, teacher_id, student_ids)
            for student_id in student_ids:
                for starts_at in starts_at_values:
                    _lesson_id, reminder_count = self._insert_lesson(
                        conn,
                        teacher_id,
                        student_id,
                        starts_at,
                        reminder_kinds,
                    )
                    lessons_created += 1
                    reminders_created += reminder_count
        return lessons_created, reminders_created

    def get_lesson_for_teacher(
        self, teacher_id: int, lesson_id: int
    ) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT lessons.*, students.name AS student_name
                FROM lessons
                JOIN students ON students.id = lessons.student_id
                WHERE lessons.id = ? AND lessons.teacher_id = ?
                """,
                (lesson_id, teacher_id),
            ).fetchone()

    def update_lesson(
        self,
        teacher_id: int,
        lesson_id: int,
        starts_at_utc: datetime,
        reminder_kinds: list[str],
    ) -> int | None:
        with self.connect() as conn:
            lesson = conn.execute(
                "SELECT * FROM lessons WHERE id = ? AND teacher_id = ?",
                (lesson_id, teacher_id),
            ).fetchone()
            if lesson is None:
                return None

            conn.execute(
                "UPDATE lessons SET starts_at = ? WHERE id = ?",
                (to_utc_iso(starts_at_utc), lesson_id),
            )
            conn.execute("DELETE FROM reminders WHERE lesson_id = ?", (lesson_id,))
            return self._insert_reminders(conn, lesson_id, starts_at_utc, reminder_kinds)

    def list_lesson_reminder_kinds(
        self,
        teacher_id: int,
        lesson_id: int,
    ) -> list[str] | None:
        with self.connect() as conn:
            lesson = conn.execute(
                "SELECT starts_at FROM lessons WHERE id = ? AND teacher_id = ?",
                (lesson_id, teacher_id),
            ).fetchone()
            if lesson is None:
                return None
            rows = conn.execute(
                "SELECT kind FROM reminders WHERE lesson_id = ? ORDER BY kind",
                (lesson_id,),
            ).fetchall()
            return [str(row["kind"]) for row in rows]

    def list_upcoming_lessons(
        self, teacher_id: int, limit: int = 20
    ) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT lessons.*, students.name AS student_name
                FROM lessons
                JOIN students ON students.id = lessons.student_id
                WHERE lessons.teacher_id = ?
                  AND lessons.starts_at >= ?
                  AND lessons.schedule_rule_id IS NULL
                  AND lessons.status = 'active'
                ORDER BY lessons.starts_at
                LIMIT ?
                """,
                (teacher_id, utc_now_iso(), limit),
            ).fetchall()

    def list_lessons_between(
        self,
        teacher_id: int,
        starts_at_from: str,
        starts_at_to: str,
    ) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT lessons.*, students.name AS student_name
                FROM lessons
                JOIN students ON students.id = lessons.student_id
                WHERE lessons.teacher_id = ?
                  AND lessons.starts_at >= ?
                  AND lessons.starts_at < ?
                  AND lessons.schedule_rule_id IS NULL
                  AND lessons.status = 'active'
                ORDER BY lessons.starts_at
                """,
                (teacher_id, starts_at_from, starts_at_to),
            ).fetchall()

    def has_any_schedule(self, teacher_id: int) -> bool:
        with self.connect() as conn:
            lesson = conn.execute(
                "SELECT 1 FROM lessons WHERE teacher_id = ? LIMIT 1",
                (teacher_id,),
            ).fetchone()
            if lesson is not None:
                return True

            rule = conn.execute(
                """
                SELECT 1 FROM schedule_rules
                WHERE teacher_id = ? AND is_active = 1
                LIMIT 1
                """,
                (teacher_id,),
            ).fetchone()
            return rule is not None

    def delete_lesson(self, teacher_id: int, lesson_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM lessons WHERE id = ? AND teacher_id = ?",
                (lesson_id, teacher_id),
            )
            return cursor.rowcount > 0

    def due_reminders(self, limit: int = 50) -> list[sqlite3.Row]:
        now = utc_now_iso()
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT
                    reminders.id AS reminder_id,
                    reminders.kind,
                    lessons.starts_at,
                    students.telegram_id AS student_telegram_id,
                    students.name AS student_name,
                    teachers.name AS teacher_name
                FROM reminders
                JOIN lessons ON lessons.id = reminders.lesson_id
                JOIN students ON students.id = lessons.student_id
                JOIN teachers ON teachers.id = lessons.teacher_id
                WHERE reminders.sent_at IS NULL
                  AND reminders.remind_at <= ?
                  AND lessons.starts_at > ?
                  AND lessons.status = 'active'
                  AND lessons.schedule_rule_id IS NULL
                ORDER BY reminders.remind_at
                LIMIT ?
                """,
                (now, now, limit),
            ).fetchall()

    def mark_reminder_sent(self, reminder_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE reminders SET sent_at = ? WHERE id = ?",
                (utc_now_iso(), reminder_id),
            )

    def create_schedule_rules(
        self,
        teacher_id: int,
        student_ids: list[int],
        weekdays: list[int],
        lesson_time: str,
        start_date: str,
        end_date: str | None,
        reminder_kinds: list[str],
    ) -> int:
        now = utc_now_iso()
        weekdays_value = ",".join(str(value) for value in sorted(weekdays))
        reminders_value = ",".join(sorted(reminder_kinds))
        created = 0
        with self.connect() as conn:
            self._ensure_students_belong_to_teacher(conn, teacher_id, student_ids)
            for student_id in student_ids:
                conn.execute(
                    """
                    INSERT INTO schedule_rules (
                        teacher_id, student_id, weekdays, lesson_time,
                        start_date, end_date, reminders, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        teacher_id,
                        student_id,
                        weekdays_value,
                        lesson_time,
                        start_date,
                        end_date,
                        reminders_value,
                        now,
                    ),
                )
                created += 1
        return created

    def list_active_schedule_rules(self, teacher_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT schedule_rules.*, students.name AS student_name,
                       students.telegram_id AS student_telegram_id,
                       teachers.name AS teacher_name
                FROM schedule_rules
                JOIN students ON students.id = schedule_rules.student_id
                JOIN teachers ON teachers.id = schedule_rules.teacher_id
                WHERE schedule_rules.teacher_id = ?
                  AND schedule_rules.is_active = 1
                ORDER BY students.name COLLATE NOCASE, schedule_rules.lesson_time
                """,
                (teacher_id,),
            ).fetchall()

    def get_schedule_rule_for_teacher(
        self,
        teacher_id: int,
        rule_id: int,
    ) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT schedule_rules.*, students.name AS student_name
                FROM schedule_rules
                JOIN students ON students.id = schedule_rules.student_id
                WHERE schedule_rules.teacher_id = ?
                  AND schedule_rules.id = ?
                """,
                (teacher_id, rule_id),
            ).fetchone()

    def deactivate_schedule_rule(self, teacher_id: int, rule_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE schedule_rules
                SET is_active = 0
                WHERE teacher_id = ? AND id = ?
                """,
                (teacher_id, rule_id),
            )
            return cursor.rowcount > 0

    def delete_schedule_rule(self, teacher_id: int, rule_id: int) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM schedule_rules WHERE teacher_id = ? AND id = ?",
                (teacher_id, rule_id),
            )
            return cursor.rowcount > 0

    def active_schedule_rules(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT schedule_rules.*, students.name AS student_name,
                       students.telegram_id AS student_telegram_id,
                       teachers.name AS teacher_name
                FROM schedule_rules
                JOIN students ON students.id = schedule_rules.student_id
                JOIN teachers ON teachers.id = schedule_rules.teacher_id
                WHERE schedule_rules.is_active = 1
                """
            ).fetchall()

    def reminder_log_exists(self, occurrence_key: str, kind: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM reminder_logs
                WHERE occurrence_key = ? AND kind = ?
                """,
                (occurrence_key, kind),
            ).fetchone()
            return row is not None

    def mark_rule_reminder_sent(self, occurrence_key: str, kind: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO reminder_logs (occurrence_key, kind, sent_at)
                VALUES (?, ?, ?)
                """,
                (occurrence_key, kind, utc_now_iso()),
            )

    def _insert_reminders(
        self,
        conn: sqlite3.Connection,
        lesson_id: int,
        starts_at_utc: datetime,
        reminder_kinds: list[str],
    ) -> int:
        created = 0
        for kind in reminder_kinds:
            option = REMINDER_OPTIONS[kind]
            remind_at = starts_at_utc - option.delta
            if remind_at <= datetime.now(timezone.utc):
                continue
            conn.execute(
                """
                INSERT INTO reminders (lesson_id, kind, remind_at)
                VALUES (?, ?, ?)
                """,
                (lesson_id, kind, to_utc_iso(remind_at)),
            )
            created += 1
        return created

    def _insert_lesson(
        self,
        conn: sqlite3.Connection,
        teacher_id: int,
        student_id: int,
        starts_at_utc: datetime,
        reminder_kinds: list[str],
    ) -> tuple[int, int]:
        cursor = conn.execute(
            """
            INSERT INTO lessons (teacher_id, student_id, starts_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (teacher_id, student_id, to_utc_iso(starts_at_utc), utc_now_iso()),
        )
        lesson_id = int(cursor.lastrowid)
        created = self._insert_reminders(conn, lesson_id, starts_at_utc, reminder_kinds)
        return lesson_id, created

    def _ensure_students_belong_to_teacher(
        self,
        conn: sqlite3.Connection,
        teacher_id: int,
        student_ids: list[int],
    ) -> None:
        unique_ids = sorted(set(student_ids))
        if not unique_ids:
            return

        placeholders = ",".join("?" for _ in unique_ids)
        rows = conn.execute(
            f"""
            SELECT id FROM students
            WHERE teacher_id = ? AND id IN ({placeholders})
            """,
            (teacher_id, *unique_ids),
        ).fetchall()
        if len(rows) != len(unique_ids):
            raise ValueError("All students must belong to the teacher")
