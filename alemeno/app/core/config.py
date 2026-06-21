from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://alemeno:alemeno_pass@db:5432/transactions_db"
    SYNC_DATABASE_URL: str = "postgresql://alemeno:alemeno_pass@db:5432/transactions_db"

    # Redis / Celery
    REDIS_URL: str = "redis://redis:6379/0"

    # LLM keys (at least one required for LLM features)
    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # Processing
    LLM_BATCH_SIZE: int = 15          # rows per LLM batch call
    LLM_MAX_RETRIES: int = 3
    ANOMALY_MULTIPLIER: float = 3.0   # flag if > N * median

    DOMESTIC_MERCHANTS: list[str] = ["swiggy", "ola", "irctc", "zomato", "jio recharge"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
