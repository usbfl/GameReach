import hashlib
import json
import re
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .ontology import FEATURES, VERSION, valid_value, LABELS


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Scope(Strict):
    stores: list[Literal["steam", "gog", "epic", "itch"]] = Field(default_factory=list, max_length=4)
    platforms: list[Literal["pc", "switch", "ps5", "xbox_series"]] = Field(default_factory=list, max_length=4)


class Predicate(Strict):
    kind: Literal["leaf", "all", "any", "not"] = "leaf"
    feature_key: str | None = None
    operator: Literal["eq", "ne", "gte", "lte", "in", "overlap"] = "eq"
    target: Any = None
    children: list["Predicate"] = Field(default_factory=list, max_length=12)
    origin: Literal["text", "ui", "legacy", "reference", "model"] = "ui"
    source_text_span: list[int] | None = Field(None, max_length=2)
    scope: dict[str, str] = Field(default_factory=dict, max_length=4)

    @model_validator(mode="after")
    def check(self):
        if any(k not in ["platform", "difficulty", "mode", "patch"] or len(v) > 80 for k, v in self.scope.items()):
            raise ValueError("无效作用域")
        if self.source_text_span is not None and (
            len(self.source_text_span) != 2
            or self.source_text_span[0] < 0
            or self.source_text_span[1] < self.source_text_span[0]
        ):
            raise ValueError("无效文本定位")
        if self.kind != "leaf":
            if (
                self.feature_key
                or self.target is not None
                or not self.children
                or (self.kind == "not" and len(self.children) != 1)
            ):
                raise ValueError("组合条件结构无效")
            return self
        if self.children or self.feature_key not in FEATURES:
            raise ValueError("未知机制")
        if self.operator not in FEATURES[self.feature_key]["operators"]:
            raise ValueError("该机制不支持此操作符")
        values = self.target if self.operator == "in" else [self.target]
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= 16
            or any(x is None or not valid_value(self.feature_key, x) for x in values)
        ):
            raise ValueError("机制目标类型或范围错误")
        return self


def leaves(p):
    return [p] if p.kind == "leaf" else [leaf for c in p.children for leaf in leaves(c)]


def identity(p):
    d = p.model_dump()
    d.pop("origin")
    d.pop("source_text_span")
    if p.children:
        d["children"] = [json.loads(x) for x in sorted(set(identity(c) for c in p.children))]
    return json.dumps(d, sort_keys=True, ensure_ascii=False)


class Soft(Strict):
    condition: Predicate
    weight: float = Field(1.0, gt=0, le=10)


class DnaQuery(Strict):
    ontology_version: Literal["dna-2"] = VERSION
    discovery_scope: Scope = Field(default_factory=Scope)
    must: list[Predicate] = Field(default_factory=list, max_length=12)
    must_not: list[Predicate] = Field(default_factory=list, max_length=12)
    soft_preferences: list[Soft] = Field(default_factory=list, max_length=12)
    reference_game_ids: list[str] = Field(default_factory=list, max_length=3)
    selected_features: list[str] = Field(default_factory=list, max_length=16)
    unparsed_fragments: list[str] = Field(default_factory=list, max_length=30)
    query_hash: str = ""

    @model_validator(mode="after")
    def validate_query(self):
        def dedup(seq, key):
            return list({key(x): x for x in seq}.values())

        self.must = dedup(self.must, identity)
        self.must_not = dedup(self.must_not, identity)
        self.soft_preferences = dedup(self.soft_preferences, lambda x: identity(x.condition))
        roots = self.must + self.must_not + [s.condition for s in self.soft_preferences]

        def depth(p):
            return 1 + max([depth(c) for c in p.children], default=0)

        if any(depth(p) > 3 for p in roots) or sum(len(leaves(p)) for p in roots) > 12:
            raise ValueError("条件最多 12 个叶子、3 层")
        if any(k not in FEATURES for k in self.selected_features):
            raise ValueError("未知参考维度")
        if (
            any(len(x) > 500 for x in self.unparsed_fragments)
            or len(json.dumps(self.model_dump(), ensure_ascii=False)) > 18000
        ):
            raise ValueError("查询过大")
        # Contradictions in conjunctions, including required negations. Disjunctions are not guessed.
        domains = {}
        seen = {identity(x) for x in self.must}
        if any(identity(x) in seen for x in self.must_not):
            raise ValueError("validation_error: 同一条件同时必须与禁止")

        def constrain(p, neg=False):
            if p.kind == "not":
                return constrain(p.children[0], not neg)
            if p.kind == "all" and not neg:
                for c in p.children:
                    constrain(c)
            if p.kind != "leaf":
                return
            f = FEATURES[p.feature_key]
            key = (p.feature_key, json.dumps(p.scope, sort_keys=True))
            domain = (
                [False, True]
                if f["type"] == "bool"
                else list(range(5))
                if f["type"] == "ordinal"
                else f["values"]
                if f["type"] == "enum"
                else None
            )
            if domain is None:
                points = {0, 1440}
                for root in roots:
                    for leaf in leaves(root):
                        if leaf.feature_key == p.feature_key:
                            vals = leaf.target if leaf.operator == "in" else [leaf.target]
                            for val in vals:
                                for endpoint in val:
                                    points.update(max(0, min(1440, endpoint + d)) for d in [-1, 0, 1])
                domain = [[a, b] for a in sorted(points) for b in sorted(points) if a <= b]
            allowed = [v for v in domain if bool(truth_value(v, p)) != neg]
            current = domains.get(key, domain)
            domains[key] = [v for v in current if v in allowed]
            if not domains[key]:
                raise ValueError("validation_error: 硬条件冲突：" + LABELS[p.feature_key])

        for p in self.must:
            constrain(p)
        for p in self.must_not:
            constrain(p, True)
        data = self.model_dump(exclude={"query_hash"})
        self.query_hash = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
        return self


def truth_value(v, p):
    t = p.target
    op = p.operator
    if op == "eq":
        return v == t
    if op == "ne":
        return v != t
    if op == "gte":
        return v >= t
    if op == "lte":
        return v <= t
    if op == "in":
        return v in t
    return max(v[0], t[0]) <= min(v[1], t[1])


def assessment_for(features, p):
    f = features.get(p.feature_key, {})
    if p.scope:
        scoped = f.get("scope_variants", {}).get(json.dumps(p.scope, sort_keys=True))
        if scoped:
            return scoped
        if any(f.get("scope", {}).get(k) != v for k, v in p.scope.items()):
            return {}
    return f


def known(features, p):
    f = assessment_for(features, p)
    if f.get("evidence_status") != "supported" or f.get("freshness") == "stale" or f.get("value") is None:
        return None
    return f["value"]


def tri(p, features):
    if p.kind == "leaf":
        v = known(features, p)
        return "unknown" if v is None else "pass" if truth_value(v, p) else "fail"
    a = [tri(c, features) for c in p.children]
    if p.kind == "not":
        return {"pass": "fail", "fail": "pass", "unknown": "unknown"}[a[0]]
    if p.kind == "all":
        return "fail" if "fail" in a else "pass" if all(x == "pass" for x in a) else "unknown"
    return "pass" if "pass" in a else "fail" if all(x == "fail" for x in a) else "unknown"


def match(v, p):
    t = p.target
    op = p.operator
    typ = FEATURES[p.feature_key]["type"]
    if op in ["eq", "ne"]:
        if typ == "ordinal":
            m = max(0.0, 1 - abs(v - t) / 4)
        else:
            m = float(v == t)
        return 1 - m if op == "ne" else m
    if op == "gte":
        return 1.0 if v >= t else max(0.0, 1 - (t - v) / 4)
    if op == "lte":
        return 1.0 if v <= t else max(0.0, 1 - (v - t) / 4)
    if op == "in":
        return float(v in t)
    lo, hi = v
    a, b = t
    return float(a <= lo <= b) if lo == hi else max(0, min(hi, b) - max(lo, a)) / (hi - lo)


def interval(p, features):
    if p.kind == "leaf":
        v = known(features, p)
        if v is None:
            return 0.0, 1.0
        m = match(v, p)
        return m, m
    values = [interval(c, features) for c in p.children]
    if p.kind == "not":
        return 1 - values[0][1], 1 - values[0][0]
    fn = min if p.kind == "all" else max
    return fn(x[0] for x in values), fn(x[1] for x in values)


def condition_text(p):
    if p.kind != "leaf":
        return ("不满足：" if p.kind == "not" else "") + (" 且 " if p.kind == "all" else " 或 ").join(
            condition_text(c) for c in p.children
        )
    name = LABELS[p.feature_key]
    f = FEATURES[p.feature_key]
    t = p.target
    if f["type"] == "bool" and p.operator in ["eq", "ne"]:
        return ("有" if (t if p.operator == "eq" else not t) else "没有") + name
    from .ontology import describe

    target = " / ".join(describe(p.feature_key, x) for x in t) if p.operator == "in" else describe(p.feature_key, t)
    return (
        name
        + " "
        + {"eq": "接近", "ne": "避开", "gte": "至少", "lte": "至多", "in": "选择", "overlap": "期望"}[p.operator]
        + "「"
        + target
        + "」"
    )


def compile_query(text="", preferences=(), scope=None, historical_preferences=()):
    must = []
    must_not = []
    soft = []
    unparsed = []
    chunks = list(
        re.finditer(
            r"[^，。；、,\n]+",
            re.sub(
                r"(?:而且|并且|但是|但|且|和|与)(?=(?:必须|绝不|不喜欢|不需要|不想|不要|不能有|没有))",
                lambda m: "，" * len(m.group()),
                text,
            ),
        )
    )
    for chunk in chunks:
        part = chunk.group()
        # Quoted titles are reference examples, not requested mechanics.
        part = re.sub(r"《[^》]*》", lambda m: " " * len(m.group()), part)
        if re.search(r"不是不|除非|要么|如果|或者|不一定|并非|不代表", part):
            unparsed.append(part)
            continue
        matched = []
        for k, f in FEATURES.items():
            if any(a.lower() in part.lower() for a in f["aliases"]):
                matched.append(k)
        if re.search(r"动作(?:游戏|之外|战斗)?|抓时机|自己的操作", part) and "combat_mode" not in matched:
            matched.append("combat_mode")
        if re.search(r"跑图简单|地图简单|路线清晰|不迷路|地图不绕", part):
            matched = [k for k in matched if k != "runback_penalty"]
            if "navigation_complexity" not in matched:
                matched.append("navigation_complexity")
        if "mechanical_difficulty" in matched and re.search(r"难度曲线|练习空间", part) and not re.search(
            r"高难|低难|操作要求|读招|技巧|精准", part
        ):
            matched.remove("mechanical_difficulty")
        # Specific concepts should not trigger their broad parent a second time.
        if "build_diversity" in matched and (
            "deckbuilding" in matched or ("build_randomness" in matched and not re.search("丰富|多样|多种", part))
        ):
            matched.remove("build_diversity")
        if not matched:
            if part.strip():
                unparsed.append(part)
            continue
        for k in matched:
            f = FEATURES[k]
            typ = f["type"]
            # Polarity belongs to this feature's wording, not another clause.
            aliases = sorted(f["aliases"], key=len, reverse=True)
            mention = next((re.search(re.escape(a), part, re.I) for a in aliases if re.search(re.escape(a), part, re.I)), None)
            local = part
            if mention:
                bounds = list(re.finditer(r"和|与|并且|而且|但|还要", part))
                left = max((m.end() for m in bounds if m.end() <= mention.start()), default=0)
                right = min((m.start() for m in bounds if m.start() >= mention.end()), default=len(part))
                local = part[left:right]
            feature_negative = bool(re.search(r"不喜欢|不想|不要|不需要|没有|避免|避开|少|低|绝不|不能有", local))
            if k in {"runback_penalty", "death_penalty", "navigation_complexity", "grind_requirement"}:
                feature_negative |= bool(re.search(r"简单|轻|短|近|宽松|不绕|不迷路", local))
            op = "eq"
            target = None
            if typ == "bool":
                target = True
            elif typ == "ordinal":
                target = 1 if feature_negative else 3
                op = "lte" if feature_negative else "gte"
            elif typ == "range":
                nums = re.findall(r"\d+", part)
                target = (
                    [int(nums[0]), int(nums[1])]
                    if len(nums) >= 2
                    else [0, int(nums[0])]
                    if nums
                    else [0, 30]
                    if re.search(r"半小时|短局", part)
                    else None
                )
                op = "overlap"
            elif k == "combat_mode":
                target = (
                    "turn_based"
                    if re.search("回合|turn.based", part, re.I)
                    else "none"
                    if re.search("无战斗|没有战斗", part)
                    else "realtime"
                    if re.search(r"实时|动作|抓时机|自己的操作", part)
                    else None
                )
            elif k == "exploration_structure":
                target = (
                    "open"
                    if re.search("开放|open", part, re.I)
                    else "linear"
                    if re.search("线性|linear", part, re.I)
                    else "branching"
                    if "分支" in part
                    else None
                )
            if target is None:
                unparsed.append(part)
                continue
            hard = bool(re.search(r"必须|一定要|绝不|不能有", part))
            p = Predicate(
                feature_key=k, operator=op, target=target, origin="text", source_text_span=[chunk.start(), chunk.end()]
            )
            if hard and feature_negative and typ == "bool":
                must_not.append(p)
            elif hard:
                must.append(p)
            else:
                if feature_negative and typ == "bool":
                    p.target = False
                soft.append(Soft(condition=p))
                if k == "grind_requirement" and re.search(r"不需要大量|不能大量|不接受大量|不要大量", local):
                    # "No large mandatory grind" excludes frequent/core-gate grind,
                    # while the softer preference still favors little/no repetition.
                    must.append(p.model_copy(update={"operator": "lte", "target": 2}))
    # Saved preferences fill gaps only; today's explicit text and fields take priority.
    explicit_keys = {p.feature_key for p in must + must_not + [s.condition for s in soft]}
    explicit_keys.update((p.model_dump() if hasattr(p, "model_dump") else p)["feature_key"] for p in preferences)
    for old in historical_preferences:
        if old["feature_key"] not in explicit_keys:
            soft.append(
                Soft(
                    condition=Predicate(
                        feature_key=old["feature_key"],
                        operator="overlap" if FEATURES[old["feature_key"]]["type"] == "range" else "eq",
                        target=old["target"],
                        origin="legacy",
                    ),
                    weight=old.get("weight", 1),
                )
            )
    # Explicit fields take precedence over inferred soft leaves for the same feature.
    for old in preferences:
        old = old.model_dump() if hasattr(old, "model_dump") else old
        k = old["feature_key"]
        soft = [s for s in soft if s.condition.feature_key != k]
        soft.append(
            Soft(
                condition=Predicate(
                    feature_key=k,
                    operator="overlap" if FEATURES[k]["type"] == "range" else "eq",
                    target=old["target"],
                    origin="legacy",
                ),
                weight=old.get("weight", 1),
            )
        )
    roots = must + must_not + [s.condition for s in soft]
    if len(roots) > 12:
        raise ValueError("validation_error: 条件最多 12 项，请删减后再探索；不会自动丢弃必要条件。")
    return DnaQuery(
        discovery_scope=scope or {},
        must=must,
        must_not=must_not,
        soft_preferences=soft,
        unparsed_fragments=list(dict.fromkeys(unparsed))[:30],
    )


def as_legacy(query):
    result = []
    for soft in query.soft_preferences:
        p = soft.condition
        if p.kind == "leaf" and p.operator in ["eq", "gte", "lte", "overlap"]:
            result.append({"feature_key": p.feature_key, "target": p.target, "weight": soft.weight})
    return result


def scope_narrows(old, new):
    return all(not old.get(k) or (bool(new.get(k)) and set(new[k]) <= set(old[k])) for k in ["stores", "platforms"])


def relaxes(old, new):
    return not all(
        {identity(x) for x in getattr(old, k)} <= {identity(x) for x in getattr(new, k)} for k in ["must", "must_not"]
    )
