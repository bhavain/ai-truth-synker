"""Configuration management for Truth Engine"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    # OpenAI Configuration
    openai_api_key: str

    # LangSmith Configuration
    langchain_tracing_v2: bool = True
    langchain_endpoint: str = "https://api.smith.langchain.com"
    langchain_api_key: str
    langchain_project: str = "truth_engine_dev"

    # Dolt Database Configuration
    dolt_host: str = "localhost"
    dolt_port: int = 3306
    dolt_user: str = "root"
    dolt_password: str
    dolt_database: str = "hardware_sync_db"

    # ChromaDB Configuration
    chroma_host: str = "localhost"
    chroma_port: int = 8000

    # Application Configuration
    environment: str = "development"
    log_level: str = "INFO"

    # Model Configuration
    bouncer_model: str = "gpt-4o-mini"
    watcher_model: str = "gpt-4o-mini"
    judge_model: str = "gpt-4o"

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @property
    def dolt_connection_string(self) -> str:
        """Generate MySQL connection string for Dolt"""
        return f"mysql+pymysql://{self.dolt_user}:{self.dolt_password}@{self.dolt_host}:{self.dolt_port}/{self.dolt_database}"

    @property
    def chroma_url(self) -> str:
        """Generate ChromaDB connection URL"""
        return f"http://{self.chroma_host}:{self.chroma_port}"


@lru_cache()
def get_settings() -> Settings:
    """Cached settings instance"""
    return Settings()
