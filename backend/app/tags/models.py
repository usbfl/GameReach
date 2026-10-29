from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .catalog import TAGS


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CardAttachment(Strict):
    id: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)


class Intent(Strict):
    tag_id: str
    polarity: Literal["want", "avoid"] = "want"
    priority: Literal[1, 2, 3] = 2
    source_id: str = "current"
    source_quote: str = Field(min_length=1, max_length=500)
    scope: dict[str, str] = Field(default_factory=dict, max_length=5)
    explicit_redline: bool = False
    confirmed_by_user: bool = False

    @model_validator(mode="after")
    def valid(self):
        if self.tag_id not in TAGS:
            raise ValueError("未知标签，须记录到 unmapped，不能发明标签 ID")
        if any(
            k not in {"platform", "mode", "difficulty", "patch", "stage"} or len(v) > 80 for k, v in self.scope.items()
        ):
            raise ValueError("无效作用域")
        return self


class Interpretation(Strict):
    summary: str = Field(min_length=1, max_length=500)
    intents: list[Intent] = Field(default_factory=list, max_length=12)
    unmapped: list[str] = Field(default_factory=list, max_length=12)


class TagFinding(Strict):
    game_id: str = Field(max_length=100)
    document_id: str = Field(max_length=500)
    aspect: Literal["tag", "core_loop", "strength", "tradeoff"]
    tag_id: str | None = None
    tag_polarity: Literal["present", "absent"] = "present"
    claim_text: str = Field(min_length=1, max_length=400)
    quote: str = Field(min_length=1, max_length=600)
    scope: dict[str, str] = Field(default_factory=dict, max_length=5)

    @model_validator(mode="after")
    def valid(self):
        if (self.aspect == "tag") != (self.tag_id is not None):
            raise ValueError("tag 主张需要 tag_id；其他主张不填 tag_id")
        if self.tag_id is not None and self.tag_id not in TAGS:
            raise ValueError("未知标签")
        if any(
            k not in {"platform", "mode", "difficulty", "patch", "stage"} or len(v) > 80 for k, v in self.scope.items()
        ):
            raise ValueError("无效作用域")
        return self


class Extraction(Strict):
    summary: str = Field(min_length=1, max_length=500)
    findings: list[TagFinding] = Field(default_factory=list, max_length=16)
    missing_information: list[str] = Field(default_factory=list, max_length=12)


class Verdict(Strict):
    claim_id: str
    verdict: Literal["supported", "rejected", "insufficient"]
    reason: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, max_length=4)


class Verification(Strict):
    summary: str = Field(min_length=1, max_length=500)
    verdicts: list[Verdict] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def unique(self):
        if len({v.claim_id for v in self.verdicts}) != len(self.verdicts):
            raise ValueError("同一主张不能提交多个核验结论")
        return self


class Reorder(Strict):
    priorities: dict[str, Literal[1, 2, 3]] = Field(default_factory=dict, max_length=12)


class CustomRequirement(Strict):
    text: str = Field(min_length=1, max_length=500)
    polarity: Literal["want", "avoid"] = "want"
    priority: Literal[1, 2, 3] = 2
    explicit_redline: bool = False
    status: Literal["unmapped", "confirmed", "ignored"] = "unmapped"
