from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from config import load_config

cfg = load_config()
engine = create_async_engine(cfg.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
