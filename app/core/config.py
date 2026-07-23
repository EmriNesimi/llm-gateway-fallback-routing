from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"

    redis_url: str = "redis://localhost:6379/0"
    database_url: str | None = None

    gateway_secret_key: str = "dev-only-insecure-key"
    otel_exporter_otlp_endpoint: str | None = None


settings = Settings()
