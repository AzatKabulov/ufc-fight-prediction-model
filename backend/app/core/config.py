from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Fight IQ API"
    app_env: str = "local"
    database_url: str = "sqlite:///./fightiq.db"
    admin_api_key: str = "change-me"
    frontend_origin: str = "http://localhost:5179"
    ufcstats_base_url: str = "http://ufcstats.com"
    scraper_timeout_seconds: int = 12
    scraper_request_delay_seconds: float = 0.15
    onewin_odds_url: str | None = None
    the_odds_api_key: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
