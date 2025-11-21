from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Performance Lab API"
    API_V1_STR: str = "/v1"
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost/dbname"  # Update this
    DEBUG: bool = True

    class Config:
        env_file = ".env"

settings = Settings()
