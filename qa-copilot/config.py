from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path


class Settings(BaseSettings):
    anthropic_api_key: str = Field(..., env="ANTHROPIC_API_KEY")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./qa_copilot.db", env="DATABASE_URL"
    )
    screenshots_dir: Path = Field(default=Path("./screenshots"), env="SCREENSHOTS_DIR")

    # Exploration
    max_exploration_depth: int = Field(default=4, env="MAX_EXPLORATION_DEPTH")
    max_actions_per_page: int = Field(default=20, env="MAX_ACTIONS_PER_PAGE")
    max_bugs_per_session: int = Field(default=60, env="MAX_BUGS_PER_SESSION")

    # Browser
    headless: bool = Field(default=True, env="HEADLESS")
    browser_timeout_ms: int = Field(default=30000, env="BROWSER_TIMEOUT_MS")

    # AI
    model: str = Field(default="claude-sonnet-4-6", env="MODEL")
    vision_enabled: bool = Field(default=True, env="VISION_ENABLED")
    vision_model: str = Field(default="claude-sonnet-4-6", env="VISION_MODEL")

    # Concurrency
    max_concurrent_personas: int = Field(default=3, env="MAX_CONCURRENT_PERSONAS")

    # Logging
    log_level: str = Field(default="INFO", env="LOG_LEVEL")

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
settings.screenshots_dir.mkdir(parents=True, exist_ok=True)
