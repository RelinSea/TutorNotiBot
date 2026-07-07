import unittest
from datetime import date, time

from bot.handlers.teacher import schedule_occurrences


class RecurrenceTests(unittest.TestCase):
    def test_schedule_occurrences_support_multiple_weekdays(self) -> None:
        values = schedule_occurrences(
            date(2026, 7, 7),
            date(2026, 7, 16),
            [1, 3],
            time(18, 0),
        )

        self.assertEqual(
            [value.date().isoformat() for value in values],
            ["2026-07-07", "2026-07-09", "2026-07-14", "2026-07-16"],
        )


if __name__ == "__main__":
    unittest.main()
