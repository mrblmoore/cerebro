from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Cerebrus API"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    # Database
    DATABASE_URL: str
    SQLALCHEMY_ECHO: bool = False

    # Vector Database
    QDRANT_URL: str
    QDRANT_API_KEY: Optional[str] = None

    # LLM
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4"
    OPENAI_ORG_ID: Optional[str] = None
    LLM_PROVIDER: str = "openai"  # options: openai, ollama, qwen
    OLLAMA_URL: Optional[str] = "http://localhost:11434"  # Ollama default
    QWEN_API_URL: Optional[str] = None
    QWEN_API_KEY: Optional[str] = None

    # Screenpipe
    SCREENPIPE_URL: str = "http://localhost:3030"
    CEREBRO_LOG_PATH: str = ""  # Optional: path to plain-text log file. Defaults to ./cerebro.log

    class Config:
        env_file = ".env"


settings = Settings()
