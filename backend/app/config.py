from pathlib import Path
from typing import Literal
from pydantic import Field, SecretStr, AliasChoices, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", populate_by_name=True)
    data_mode: Literal["fixtures", "live"] = "fixtures"
    llm_mode: Literal["mock", "live"] = "mock"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_use_tools: bool = True
    igdb_client_id: str = ""
    igdb_client_secret: SecretStr = SecretStr("")
    steam_web_api_key: SecretStr = SecretStr("")
    database_url: str = "sqlite:///./data/gamereach.db"
    display_timezone: str = "Asia/Taipei"
    max_dna_followup_rounds: int = Field(2, ge=0, le=6)
    max_planning_rounds: int = Field(6, ge=1, le=20)
    max_llm_calls: int = Field(18, ge=1, le=100)
    max_tool_calls: int = Field(15, ge=1, le=100)
    max_external_requests: int = Field(25, ge=1, le=200)
    max_active_seconds: float = Field(180, gt=0, le=1800)
    interest_min_games: int = Field(3, ge=2, le=7)
    interest_max_games: int = Field(7, ge=3, le=12)
    interest_max_cards: int = Field(6, ge=1, le=12)
    reuse_enabled: bool = True
    reuse_ttl_seconds: int = Field(86400, ge=60, le=604800)
    source_catalog_ttl_seconds: int = Field(21600, ge=60, le=86400)
    source_details_ttl_seconds: int = Field(21600, ge=60, le=86400)
    source_reviews_ttl_seconds: int = Field(1800, ge=60, le=86400)
    tag_scan_limit: int = Field(500, ge=20, le=2000)
    tag_candidate_limit: int = Field(80, ge=1, le=300)
    tag_investigation_limit: int = Field(20, ge=1, le=30)
    tag_max_batches_per_game: int = Field(3, ge=1, le=6)
    tag_review_batch_size: int = Field(4, ge=2, le=8)
    tag_review_batch_chars: int = Field(16000, ge=4000, le=32000)
    tag_review_fetch_limit: int = Field(36, ge=12, le=120)
    max_candidates: int = Field(20, ge=1, le=20)
    max_investigated_games: int = Field(
        5, ge=1, le=5, validation_alias=AliasChoices("MAX_INVESTIGATED_GAMES", "MAX_DEEP_RESEARCH_GAMES")
    )
    max_retries_per_call: int = Field(2, ge=0, le=2)
    run_live_tests: bool = False
    enable_demo_controls: bool = True
    enable_verifier: bool = True
    dynamic_routing: bool = True
    min_coverage: float = Field(0.6, ge=0, le=1)
    igdb_min_interval: float = Field(0.26, ge=0)
    steam_min_interval: float = Field(1, ge=0)
    llm_timeout_seconds: float = Field(30, gt=0, le=120)
    max_reviews_per_game: int = Field(120, ge=20, le=500)
    review_context_limit: int = Field(60, ge=20, le=120)
    min_reviews_for_recommendation: int = Field(20, ge=1, le=120)
    min_cited_reviews: int = Field(3, ge=1, le=12)
    min_known_mechanics: int = Field(3, ge=1, le=18)

    @model_validator(mode="after")
    def sampling_limits(self):
        if self.interest_min_games > self.interest_max_games:
            raise ValueError("兴趣卡片最少成员不能超过最多成员")
        if (
            not self.min_cited_reviews
            <= self.min_reviews_for_recommendation
            <= self.review_context_limit
            <= self.max_reviews_per_game
        ):
            raise ValueError(
                "采样配置需满足 MIN_CITED_REVIEWS ≤ MIN_REVIEWS_FOR_RECOMMENDATION ≤ REVIEW_CONTEXT_LIMIT ≤ MAX_REVIEWS_PER_GAME"
            )
        return self

    def resolved_db_url(self):
        if self.database_url.startswith("sqlite:///./"):
            return "sqlite:///" + str(ROOT / self.database_url.removeprefix("sqlite:///./"))
        return self.database_url

    def diagnostics(self):
        missing = []
        if self.llm_mode == "live":
            for name in ["llm_base_url", "llm_api_key", "llm_model"]:
                v = getattr(self, name)
                if not (v.get_secret_value() if isinstance(v, SecretStr) else v):
                    missing.append(name.upper())
        if self.data_mode == "live":
            if not self.igdb_client_id:
                missing.append("IGDB_CLIENT_ID")
            if not self.igdb_client_secret.get_secret_value():
                missing.append("IGDB_CLIENT_SECRET")
        return missing
