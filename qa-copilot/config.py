from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path


class Settings(BaseSettings):
    anthropic_api_key: str = Field(..., env="ANTHROPIC_API_KEY")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./qa_copilot.db", env="DATABASE_URL"
    )
    screenshots_dir: Path = Field(default=Path("./screenshots"), env="SCREENSHOTS_DIR")
    max_exploration_depth: int = Field(default=4, env="MAX_EXPLORATION_DEPTH")
    max_actions_per_page: int = Field(default=20, env="MAX_ACTIONS_PER_PAGE")
    headless: bool = Field(default=True, env="HEADLESS")
    browser_timeout_ms: int = Field(default=30000, env="BROWSER_TIMEOUT_MS")
    log_level: str = Field(default="INFO", env="LOG_LEVEL")

    model: str = "claude-sonnet-4-6"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
settings.screenshots_dir.mkdir(parents=True, exist_ok=True)
