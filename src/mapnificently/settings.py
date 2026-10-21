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
    allow_refresh: bool = Field(
        default=True,
        description="Allow POST /api/cities/{city}/refresh. Turn off on public servers, where "
        "anyone could otherwise trigger downloads and heavy processing.",
    )

    @property
    def feeds_root(self) -> Path:
        """Downloaded feeds, one folder per provider, shared by all regions."""
        return self.data_dir / "feeds"

    @property
    def regions_root(self) -> Path:
        """Prepared data, one folder per region."""
        return self.data_dir / "regions"

    def feeds_dir(self, provider: str) -> Path:
        """Where a provider's downloads are kept (shared by every region using it)."""
        return self.feeds_root / provider

    def region_dir(self, region_id: str) -> Path:
        """Prepared tables and cached timetables of one region."""
        return self.regions_root / region_id


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings."""
    return Settings()
