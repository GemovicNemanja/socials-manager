"""Configuration. Read once at startup and treated as immutable."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr
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


class XConfig(BaseModel):
    """The X developer app ("Native App": OAuth 2.0 with PKCE, a public client without a secret).

    Set ``SOCIALMGR_X__CLIENT_ID`` in ``.env``; the client id is never committed.
    """

    model_config = ConfigDict(frozen=True)

    client_id: str | None = None
    redirect_uri: str = "http://127.0.0.1:8765/auth/x/callback"
    scopes: tuple[str, ...] = ("tweet.read", "users.read", "follows.read", "offline.access")
    api_base: str = "https://api.x.com"
    authorize_url: str = "https://x.com/i/oauth2/authorize"


class PluginConfig(BaseModel):
    """Which implementation backs each extension interface (IMPLEMENTATION_PLAN.md §13).

    Each value is a built-in name or a ``module:attribute`` path.
    """

    model_config = ConfigDict(frozen=True)

    providers: tuple[str, ...] = ("fake_owner",)
    prior_source: str = "hand_set"
    champion_model: str = "nb2"
    challenger_models: tuple[str, ...] = ("trailing_median",)
    labeler: str = "claude_code"


class RunConfig(BaseModel):
    """What a run does besides collecting (IMPLEMENTATION_PLAN.md §6, "At each run")."""

    model_config = ConfigDict(frozen=True)

    regime_check: bool = True  # look for ranker changes in xai-org/x-algorithm's param.rs
    backup: bool = True
    backup_keep_daily: int = Field(default=14, ge=1)
    backup_keep_weekly: int = Field(default=12, ge=0)


class JournalConfig(BaseModel):
    """The public journal (plan §9, M4): a static site published to a public GitHub repository.

    ``repo`` is the git remote the built site is pushed to (GitHub Pages serves it), for example
    ``git@github.com-personal:you/growing-in-public.git``; empty means "not published yet".
    """

    model_config = ConfigDict(frozen=True)

    repo: str | None = None
    branch: str = "main"
    site_url: str | None = None  # where GitHub Pages serves it, for share links
    tagline: str = "growing in public"
    headline: str = "I'm growing my X account from zero, and I write down a forecast before every post"
    intro: str = (
        "Before each post goes out, I freeze a forecast of how it should do. Two days later I score it. "
        "I run small randomized experiments on what to change, and I write up what held and what "
        "didn't, including the experiments that went nowhere."
    )
    auto_refresh: bool = True  # each run republishes the numbers once an entry has been published
    author_name: str | None = None  # git identity for site commits; default: this repo's git config
    author_email: str | None = None


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
    run: RunConfig = RunConfig()
    anthropic_api_key: SecretStr | None = None  # only for the anthropic_api labeller
    x: XConfig = XConfig()
    journal: JournalConfig = JournalConfig()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "socialmgr.sqlite3"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def journal_dir(self) -> Path:
        """Entries (markdown, drafts and published) and the publish checkout. Local and private."""
        return self.data_dir / "journal"

    @property
    def resolved_backup_dir(self) -> Path:
        return self.backup_dir if self.backup_dir is not None else self.data_dir / "backups"
