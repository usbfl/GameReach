"""Ground every interpreted preference in the current text or an attached card."""

import re
from .models import Interpretation, Intent
from .catalog import TAGS

REDLINE = re.compile(r"必须|绝对不能|绝对不要|绝不|不能接受|不接受|仅限|一定要|must\b|never\b", re.I)


def ground_interpretation(result, query, cards):
    sources = {"current": query, **{c["id"]: c["text"] for c in cards}}
    accepted = []
    for intent in result.intents:
        intent.confirmed_by_user = False
        if intent.source_id not in sources or intent.source_quote not in sources[intent.source_id]:
            raise ValueError("关键词未定位到本次输入或所附卡片")
        # Card titles are classification labels, not additional demands.
        if intent.explicit_redline and (intent.source_id != "current" or not REDLINE.search(intent.source_quote)):
            raise ValueError("仅当前输入明确的必要要求可成为排除依据")
        if intent.source_id != "current":
            intent.priority = min(intent.priority, 1)
        accepted.append(intent)
    explicit = {i.tag_id for i in accepted if i.source_id == "current"}
    accepted = [i for i in accepted if i.source_id == "current" or i.tag_id not in explicit]
    unique = {}
    for i in accepted:
        # Repetition, aliases and duplicate attached cards never increase weight.
        key = (i.tag_id, i.polarity, tuple(sorted(i.scope.items())))
        if key not in unique or i.priority > unique[key].priority:
            unique[key] = i
    result.intents = list(unique.values())
    for tag in {i.tag_id for i in result.intents}:
        group = [i for i in result.intents if i.tag_id == tag and i.source_id == "current"]
        if len({i.polarity for i in group}) > 1 and len({tuple(sorted(i.scope.items())) for i in group}) == 1:
            result.unmapped.append(f"当前输入对{TAGS[tag]['label']}有相反意愿，请明确场景")
            result.intents = [i for i in result.intents if i.tag_id != tag]
    return result


def demo_interpret(query, cards):
    """Explicit offline demonstration only; never a live-model fallback."""
    intents, unmapped = [], []
    for source_id, text in [("current", query)] + [(c["id"], c["text"]) for c in cards]:
        masked = re.sub(r"《[^》]*》", lambda m: " " * len(m[0]), text)
        for chunk in re.finditer(r"[^，。；,;\n]+", masked):
            segment = chunk[0]
            # Preserve opposition across a comma, but local '但/而且' resets it.
            for clause in re.split(r"但是|但|而且|并且|同时|以及", segment):
                if not clause.strip():
                    continue
                matched = False
                for key, tag in TAGS.items():
                    hits = [
                        a
                        for a in [tag["label"]]
                        + tag["aliases"]
                        + {
                            "read_patterns": ["战斗操作有深度", "操作深度"],
                            "build_variety": ["构筑丰富", "构筑变化"],
                        }.get(key, [])
                        if a.casefold() in clause.casefold()
                    ]
                    if not hits:
                        continue
                    matched = True
                    alias = max(hits, key=len)
                    pos = clause.casefold().find(alias.casefold())
                    prefix, suffix = clause[max(0, pos - 24) : pos], clause[pos + len(alias) :]
                    avoided = bool(
                        re.search(
                            r"不太喜欢|绝对不能|绝不|不需要|不想|不要|不喜欢|不喜歡|不接受|不能接受|讨厌|排除|避开|避免|减少|更少|少点|低|no |without |avoid ",
                            prefix,
                            re.I,
                        )
                    )
                    if key in {
                        "death_loss",
                        "runback",
                        "navigation",
                        "resource_grind",
                        "reading_load",
                        "time_pressure",
                    }:
                        avoided |= bool(re.match(r"(?:很)?(?:低|少|简单|短|轻)", suffix))
                    original = text[chunk.start() : chunk.end()]
                    intents.append(
                        Intent(
                            tag_id=key,
                            polarity="avoid" if avoided else "want",
                            priority=1
                            if re.search(r"顺便|次要|稍微", clause)
                            else 3
                            if re.search(r"重视|丰富|深度|最|主要|核心", clause) and not avoided
                            else 2,
                            source_id=source_id,
                            source_quote=original,
                            explicit_redline=bool(source_id == "current" and REDLINE.search(clause)),
                        )
                    )
                if source_id == "current" and not matched:
                    residual = re.sub(
                        r"(?:我)?(?:想|希望|要)?找(?:几款|一些)?|游戏|PC|必须支持中文|必须中文|要求中文|。",
                        "",
                        clause,
                        flags=re.I,
                    ).strip()
                    if residual and re.search(r"[\w\u4e00-\u9fff]", residual):
                        unmapped.append(clause.strip())
    result = Interpretation(
        summary="离线规则演示：真实模式在检索之前由 LLM 理解本次文本和附件。",
        intents=intents[:12],
        unmapped=list(dict.fromkeys(unmapped))[:12],
    )
    if not intents and not result.unmapped:
        result.unmapped = ["当前文本尚未匹配演示词表，可补充具体体验；真实模式由 LLM 理解语义。"]
    return ground_interpretation(result, query, cards)


def confirmed_interpretation(conditions):
    """An explicit final snapshot replaces inferred conditions, including removals."""
    unique = {}
    for item in conditions.intents:
        intent = item.model_copy(deep=True)
        intent.source_id = "current"
        intent.confirmed_by_user = True
        key = (intent.tag_id, tuple(sorted(intent.scope.items())))
        if key in unique and unique[key].polarity != intent.polarity:
            raise ValueError("同一场景的标签方向冲突，请先保留一个最终条件")
        unique[key] = intent
    return Interpretation(
        summary="已采用用户本次确认的最终条件；历史兴趣不会覆盖这些修改。",
        intents=list(unique.values()),
        unmapped=[c.text for c in conditions.custom if c.status != "ignored"],
    )
