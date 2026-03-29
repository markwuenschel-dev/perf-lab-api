"""
app/core/config.py

Add these to your .env:

    SECRET_KEY=<run: openssl rand -hex 32>
    ALGORITHM=HS256
    ACCESS_TOKEN_EXPIRE_MINUTES=10080   # 7 days
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "Performance Lab API"
    API_V1_STR: str = "/v1"
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost/dbname"
    DEBUG: bool = True

    # Auth
    SECRET_KEY: str = "change-me-in-production"  # override in .env
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
