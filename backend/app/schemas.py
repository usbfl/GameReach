from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .dna.ontology import LABELS as FEATURE_LABELS, valid_value as valid_feature
from .dna.query import DnaQuery
from .tags.models import CardAttachment, Intent, CustomRequirement


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Preference(Strict):
    feature_key: str
    target: Any
    weight: float = Field(1, gt=0, le=10)

    @model_validator(mode="after")
    def valid(self):
        if (
            self.feature_key not in FEATURE_LABELS
            or self.target is None
            or not valid_feature(self.feature_key, self.target)
        ):
            raise ValueError("无效的 Game DNA 偏好")
        return self


class Constraints(Strict):
    platform: str = Field("PC", max_length=50)
    released_only: bool = True
    language: str | None = Field(None, max_length=50)
    max_price: float | None = Field(None, ge=0)
    currency: str = Field("CNY", max_length=10)
    region: str = Field("CN", max_length=10)
    played: list[str] = Field(default_factory=list, max_length=200)


class RoleModes(Strict):
    planner: Literal["ai", "human"] = "ai"
    researcher: Literal["ai", "human"] = "ai"
    verifier: Literal["ai", "human"] = "ai"


class DiscoveryScope(Strict):
    stores: list[Literal["steam", "gog", "epic", "itch"]] = Field(default_factory=list, max_length=4)
    platforms: list[Literal["pc", "switch", "ps5", "xbox_series"]] = Field(default_factory=list, max_length=4)


class ConfirmedConditions(Strict):
    intents: list[Intent] = Field(default_factory=list, max_length=12)
    custom: list[CustomRequirement] = Field(default_factory=list, max_length=12)
    constraints: Constraints = Field(default_factory=Constraints)


class ResearchRequest(Strict):
    confirmed_conditions: ConfirmedConditions | None = None
    intent_preview_id: str | None = Field(None, max_length=100)
    research_strategy: Literal["quick", "balanced", "thorough"] = "balanced"
    recommendation_count: int = Field(5, ge=1, le=10)
    recommendation_mode: Literal["legacy_dna", "player_tags"] = "legacy_dna"
    attachments: list[CardAttachment] = Field(default_factory=list, max_length=6)
    dna_query: DnaQuery | None = None
    discovery: DiscoveryScope = Field(default_factory=DiscoveryScope)
    query: str = Field("", max_length=3000)
    kind: Literal["scout"] = "scout"
    game_ids: list[str] = Field(default_factory=list, max_length=4)
    preferences: list[Preference] = Field(default_factory=list, max_length=16)
    constraints: Constraints = Field(default_factory=Constraints)
    role_modes: RoleModes = Field(default_factory=RoleModes)
    demo_fault: Literal["none", "rate_limited", "timeout"] = "none"

    @model_validator(mode="after")
    def games(self):
        self.game_ids = list(dict.fromkeys(self.game_ids))
        return self


class ToolArgs(Strict):
    tag_recall: bool = False
    catalog_offset: int = Field(0, ge=0, le=2000)
    catalog_page_size: int = Field(100, ge=1, le=200)
    review_limit: int | None = Field(None, ge=12, le=500)
    tag_keywords: list[str] = Field(default_factory=list, max_length=12)
    stores: list[Literal["steam", "gog", "epic", "itch"]] = Field(default_factory=list, max_length=4)
    platforms: list[Literal["pc", "switch", "ps5", "xbox_series"]] = Field(default_factory=list, max_length=4)
    keyword_hints: list[
        Literal[
            "roguelike",
            "roguelite",
            "rogue-lite",
            "soulslike",
            "souls-like",
            "deckbuilding",
            "action-rpg",
            "action-roguelike",
        ]
    ] = Field(default_factory=list, max_length=8)
    genre_ids: list[int] = Field(default_factory=list, max_length=8)
    discover: bool = False

    store: Literal["all", "steam", "gog", "epic", "itch"] = "all"
    platform: Literal["all", "pc", "switch", "ps5", "xbox_series"] = "all"
    game_id: str | None = Field(None, max_length=100)
    q: str = Field("", max_length=120)
    after: str | None = Field(None, max_length=40)
    cursor: str = Field("*", max_length=400)


class Action(Strict):
    tool_name: Literal["search_games", "get_game_details", "get_reviews", "get_updates"]
    arguments: ToolArgs = Field(default_factory=ToolArgs)


class Finding(Strict):
    aspect: Literal["mechanic", "core_loop", "strength", "tradeoff", "audience"] = "mechanic"
    game_id: str = Field(max_length=100)
    feature_key: str | None = None
    value: Any = None
    claim_text: str = Field(max_length=1000)
    document_id: str
    quote: str = Field(min_length=1, max_length=600)
    assessment_type: Literal["fact", "interpretation"] = "interpretation"
    scope: dict[str, str] = Field(default_factory=dict, max_length=4)

    @model_validator(mode="after")
    def validate_value(self):
        if any(k not in ["platform", "difficulty", "mode", "patch"] or len(v) > 80 for k, v in self.scope.items()):
            raise ValueError("无效特征作用域")
        if self.feature_key is not None and (
            self.feature_key not in FEATURE_LABELS or not valid_feature(self.feature_key, self.value)
        ):
            raise ValueError("无效特征值")
        return self


class ClaimVerdict(Strict):
    claim_id: str
    verdict: Literal["supported", "rejected", "insufficient"]
    reason: str = Field(min_length=1, max_length=800)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class AgentOutput(Strict):
    verdicts: list[ClaimVerdict] = Field(default_factory=list, max_length=100)
    role: Literal["planner", "researcher", "verifier"]
    summary: str = Field(min_length=1, max_length=2000)
    findings: list[Finding] = Field(default_factory=list, max_length=80)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    missing_information: list[str] = Field(default_factory=list, max_length=30)
    proposed_actions: list[Action] = Field(default_factory=list, max_length=15)
    status: Literal["continue", "done", "needs_evidence"] = "done"
    rejected_claim_ids: list[str] = Field(default_factory=list, max_length=100)
    preferences: list[Preference] = Field(default_factory=list, max_length=16)


class HumanInput(Strict):
    action_id: str = Field(min_length=8, max_length=100)
    state_version: int
    decision: Literal["accept", "reject", "submit"] = "accept"
    note: str = Field("", max_length=3000)
    game_ids: list[str] = Field(default_factory=list, max_length=5)
    claim_ids: list[str] = Field(default_factory=list, max_length=100)
    source_title: str = Field("", max_length=200)
    source_text: str = Field("", max_length=5000)
    source_url: str = Field("", max_length=1000)


class ShortlistInput(Strict):
    game_id: str = Field(max_length=100)
    reason: str = Field("", max_length=500)


class ProfileInput(Strict):
    preferences: list[Preference] = Field(default_factory=list, max_length=16)
    constraints: Constraints = Field(default_factory=Constraints)


class FeedbackInput(Strict):
    game_id: str
    reaction: Literal["like", "dislike", "played", "later"]
    reason: str = Field("", max_length=1000)
