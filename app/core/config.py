import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    app_name: str = "Strategic Navigator"
    app_version: str = "1.0.0"
    debug: bool = False

    host: str = "0.0.0.0"
    port: int = 8000

    time_zone: str = "Europe/Moscow"

    # Корп-прокси для парсинга внешних источников
    http_proxy: str = "http://ar-proxy.severstal.severstalgroup.com:3128"
    https_proxy: str = "http://ar-proxy.severstal.severstalgroup.com:3128"
    parsing_proxy: str = "http://ar-proxy.severstal.severstalgroup.com:3128"
    no_proxy: str = "localhost,127.0.0.1,.severstal.severstalgroup.com,.severstalgroup.com"

    # Qdrant (векторная БД в контуре)
    qdrant_url: str = ""
    qdrant_api_key: str = ""

    # OpenRouter / LLM
    openrouter_api_key: str = ""
    brief_model: str = "google/gemini-2.5-flash"
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_dimensions: int = 1536

    auth_secret: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    def apply_runtime_env(self) -> None:
        """Прокидывает прокси и Qdrant в os.environ для news_parsers / requests."""
        mapping = {
            "HTTP_PROXY": self.http_proxy,
            "HTTPS_PROXY": self.https_proxy,
            "PARSING_PROXY": self.parsing_proxy or self.http_proxy,
            "NO_PROXY": self.no_proxy,
            "QDRANT_URL": self.qdrant_url,
            "QDRANT_API_KEY": self.qdrant_api_key,
            "OPENROUTER_API_KEY": self.openrouter_api_key,
            "BRIEF_MODEL": self.brief_model,
            "EMBEDDING_MODEL": self.embedding_model,
            "EMBEDDING_DIMENSIONS": str(self.embedding_dimensions),
            "AUTH_SECRET": self.auth_secret,
            "TZ": self.time_zone,
        }
        for key, value in mapping.items():
            if value and not os.getenv(key, "").strip():
                os.environ[key] = value


settings = Settings()
settings.apply_runtime_env()
