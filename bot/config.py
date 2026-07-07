import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    bot_token: str
    timezone: str = "Asia/Qyzylorda"
    database: str = "reminders.sqlite3"
    reminder_poll_seconds: int = 60

    proxy: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        token = os.getenv("BOT_TOKEN", "").strip()
        if not token or token == "replace-with-token-from-botfather":
            raise RuntimeError("Set BOT_TOKEN in .env or environment variables")

        return cls(
            bot_token=token,
            timezone=os.getenv("BOT_TIMEZONE", "Asia/Qyzylorda"),
            database=os.getenv("BOT_DATABASE", "reminders.sqlite3"),
            reminder_poll_seconds=int(os.getenv("REMINDER_POLL_SECONDS", "60")),
            proxy=os.getenv("BOT_PROXY", "").strip(),
        )


def load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)
