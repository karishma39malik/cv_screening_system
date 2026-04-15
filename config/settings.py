from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional
import os

class Settings(BaseSettings):
    # ---- Database ----
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "cv_screening"
    postgres_user: str = "hr_admin"
    postgres_password: str

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ---- Ollama ----
    ollama_base_url: str = "http://localhost:11434"
    ollama_llm_model: str = "llama3.1:8b"
    ollama_embed_model: str = "nomic-embed-text"

    # ---- API ----
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    secret_key: str
    environment: str = "production"

    # ---- Files ----
    upload_dir: str = "/app/uploads"
    max_file_size_mb: int = 20
    allowed_extensions: str = "pdf,docx,txt"

    @property
    def allowed_ext_list(self) -> list[str]:
        return [ext.strip() for ext in self.allowed_extensions.split(",")]

    # ---- Logging ----
    log_level: str = "INFO"
    log_dir: str = "/app/logs"

    # ---- System ----
    max_bulk_upload: int = 500
    embedding_dimension: int = 768

    class Config:
        env_file = ".env"
        case_sensitive = False


# Singleton instance
settings = Settings()
