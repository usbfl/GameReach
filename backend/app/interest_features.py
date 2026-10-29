"""Local interest clues reuse the tag vocabulary; they are never recommendation evidence."""

import math
from .tags.catalog import TAGS


def attention(game):
    """Semantic tiers, not accumulated likes. Time only breaks ties within a tier."""
    reaction = game.get("reaction", "neutral")
    if reaction in {"dislike", "exclude"}:
        return {"tier": "excluded", "weight": 0.0, "time_bonus": 0.0, "reason": "明确负面/排除优先于时长与收藏"}
    hours = max(0, game.get("playtime_minutes") or 0) / 60
    bonus = round(0.25 * min(math.log1p(hours) / math.log(51), 1), 4)
    tier, base = (
        ("liked", 2.0)
        if reaction == "like"
        else ("saved", 1.5)
        if game.get("favorite") or game.get("watch")
        else ("library", 1.0)
    )
    return {
        "tier": tier,
        "weight": base + bonus,
        "time_bonus": bonus,
        "reason": {"liked": "明确喜欢", "saved": "收藏/关注表示想了解", "library": "资料入库，尚未推断喜欢"}[tier]
        + "；时长只增加最多0.25的关注权重",
    }


def canonical_theme(label):
    text = label.strip().casefold()
    text = {
        "构筑丰富": "build_variety",
        "构筑变化": "build_variety",
        "boss练习": "boss_practice",
        "boss 练习": "boss_practice",
    }.get(text, text)
    for key, tag in TAGS.items():
        if text in {x.casefold() for x in [key, tag["label"], *tag["aliases"]]}:
            return key
    return "catalog:" + text


def themes(game):
    out = {}
    for tag in game.get("tags", []):
        key = canonical_theme(tag)
        out.setdefault(
            key,
            {
                "id": key,
                "label": TAGS[key]["label"] if key in TAGS else tag,
                "basis": "catalog",
                "reason": "目录标签：" + tag + "（未做评论核验）",
            },
        )
    for name in game.get("collections", []):
        key = canonical_theme(name)
        out.setdefault(
            key,
            {
                "id": key,
                "label": TAGS[key]["label"] if key in TAGS else "收藏分类：" + name,
                "basis": "user_collection",
                "reason": "用户分类：" + name,
            },
        )
    # These entries are populated only from current_features, whose source validity is checked.
    for clue in game.get("verified_themes", []):
        out[clue["id"]] = clue
    return list(out.values())


def verified_themes(db, dataset, gid):
    if not gid:
        return []
    from .dna.projection import current_features

    mapping = {
        "build_diversity": "build_variety",
    }
    out = []
    for key, f in current_features(db, dataset, gid).items():
        tag = mapping.get(key)
        if (
            tag in TAGS
            and f.get("evidence_status") == "supported"
            and f.get("freshness") != "stale"
            and isinstance(f.get("value"), (int, float))
            and f["value"] >= 3
        ):
            out.append(
                {
                    "id": tag,
                    "label": TAGS[tag]["label"],
                    "basis": "verified_dna",
                    "reason": "已有核验机制：" + key,
                    "feature": f,
                }
            )
    return out


def profile(games):
    """One vote per game and theme, independent of the number of cards."""
    rows = {}
    for game in games.values():
        sig = game["attention"]
        if not sig["weight"]:
            continue
        for theme in themes(game):
            row = rows.setdefault(
                theme["id"], {"theme_id": theme["id"], "label": theme["label"], "game_keys": [], "attention_weight": 0}
            )
            row["game_keys"].append(game["key"])
            row["attention_weight"] = round(row["attention_weight"] + sig["weight"], 4)
    return list(rows.values())
