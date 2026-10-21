"""Application settings, read from environment variables prefixed with ``MAPNIFICENTLY_``."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration shared by the API, the CLI and the data pipeline."""

    model_config = SettingsConfigDict(env_prefix="MAPNIFICENTLY_", env_file=".env", extra="ignore")

    data_dir: Path = Field(
        default_factory=lambda: Path.home() / ".cache" / "mapnificently",
        description="Where downloaded feeds and built timetables are stored.",
    )
    cities_file: Path | None = Field(
        default=None,
        description="Optional TOML file with extra cities, or overrides of the packaged ones.",
    )
    entur_client_name: str = Field(
        default="mapnificently-dev",
        description="Sent as ET-Client-Name to Entur. Use '<organisation>-<application>'.",
    )
    api_url: str = Field(
        default="http://127.0.0.1:8000",
        description="Base URL of the API, used by the CLI.",
    )
    timetable_cache_size: int = Field(default=4, ge=1, description="Timetables kept in memory.")

    def city_dir(self, city_id: str) -> Path:
        """Directory holding the feeds and built timetables of one city."""
        return self.data_dir / city_id


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings."""
    return Settings()
