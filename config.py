from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Config:
    bot_token: str
    super_admin_id: int
    database_url: str

def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    admin_id = os.getenv("SUPER_ADMIN_ID", "").strip()
    db = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./sanoat.db").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN .env faylida ko'rsatilmagan")
    if not admin_id.isdigit():
        raise RuntimeError("SUPER_ADMIN_ID raqam bo'lishi kerak")
    return Config(token, int(admin_id), db)
