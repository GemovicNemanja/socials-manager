"""Configuration. Read once at startup and treated as immutable."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AccountConfig(BaseModel):
    """The owner account this install tracks."""

    model_config = ConfigDict(frozen=True)

    platform: str = "x"
    handle: str = "@your_account"
    tz: str = "UTC"
    premium: bool = True


class PeerConfig(BaseModel):
    """A non-owner account to track. Empty in v1; used by tests and later milestones."""

    model_config = ConfigDict(frozen=True)

    platform: str = "x"
    handle: str
    role: str = "peer"


class PluginConfig(BaseModel):
    """Which implementation backs each extension interface (IMPLEMENTATION_PLAN.md §13).

    Each value is a built-in name or a ``module:attribute`` path.
    """

    model_config = ConfigDict(frozen=True)

    providers: tuple[str, ...] = ("fake_owner",)
    prior_source: str = "hand_set"
    champion_model: str = "trailing_median"
    challenger_models: tuple[str, ...] = ()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SOCIALMGR_",
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
        frozen=True,
    )

    data_dir: Path = Path("data")
    backup_dir: Path | None = None
    monthly_cap_usd: float = Field(default=15.0, ge=0)
    host: str = "127.0.0.1"
    port: int = 8765
    workspace_id: int = 1
    account: AccountConfig = AccountConfig()
    peers: tuple[PeerConfig, ...] = ()
    plugins: PluginConfig = PluginConfig()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "socialmgr.sqlite3"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def resolved_backup_dir(self) -> Path:
        return self.backup_dir if self.backup_dir is not None else self.data_dir / "backups"
