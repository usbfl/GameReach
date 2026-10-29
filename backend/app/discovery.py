"""Transparent candidate recall. Genre hints are not verified gameplay evidence."""

import re

PLATFORMS = {
    "pc": (6, "PC"),
    "switch": (130, "Nintendo Switch"),
    "ps5": (167, "PlayStation 5"),
    "xbox_series": (169, "Xbox Series X|S"),
}
GENRES = {
    5: "射击",
    13: "模拟",
    31: "冒险",
    12: "角色扮演",
    15: "策略",
    16: "回合制策略",
    24: "战术",
    9: "解谜",
    8: "平台动作",
    25: "砍杀动作",
    32: "独立",
}


def intent_text(query):
    """Reference titles describe taste, not mandatory genres or platforms."""
    return re.sub(r"《[^》]*》", lambda m: " " * len(m.group()), query)


def discovery_action(query, preferences, scope):
    ids = set()
    query = intent_text(query)
    query = re.sub(r"(?:不想|不要|排除|不喜欢|不玩|不接受)[^，。；,\n但]*", "", query)
    hints = []
    if re.search(r"肉鸽|rogue", query, re.I):
        hints += ["roguelike", "roguelite", "rogue-lite"]
    if re.search(r"魂|souls", query, re.I):
        hints += ["soulslike", "souls-like"]
    if re.search(r"卡牌构筑|牌组构筑|牌組構築|deck.?build", query, re.I) or any(
        p["feature_key"] == "deckbuilding" and p["target"] is True for p in preferences
    ):
        hints += ["deckbuilding"]
    # These are broad recall hints only; exclusions remain preferences for evidence-based ranking.
    for pattern, genres in [
        (r"策略|战术|strategy|tactic", [15, 24]),
        (r"回合|turn.based", [16]),
        (r"解谜|谜题|puzzle", [9]),
        (r"魂|战斗|招架|弹反|动作|souls|action", [25, 12]),
        (r"肉鸽|rogue", [12, 15, 32]),
        (r"平台跳跃|platformer", [8]),
    ]:
        if re.search(pattern, query, re.I):
            ids.update(genres)
    for p in preferences:
        if p["feature_key"] == "strategic_depth" and isinstance(p["target"], (int, float)) and p["target"] >= 3:
            ids.update([15, 24])
        if p["feature_key"] == "combat_mode" and p["target"] == "turn_based":
            ids.add(16)
        if (
            not ids
            and p["feature_key"] == "build_diversity"
            and isinstance(p["target"], (int, float))
            and p["target"] >= 3
        ):
            ids.update([12, 15])
    if not ids and re.search(r"构筑|build", query, re.I):
        ids.update([12, 15])
    action_intent = re.search(r"动作|战斗|读招|技巧|action|combat", query, re.I)
    if action_intent and not hints:
        hints += ["action-rpg", "action-roguelike"]
    platforms = scope.get("platforms", [])
    if not platforms and re.search(r"\bPC\b|电脑|Windows", query, re.I):
        platforms = ["pc"]
    return {
        "tool_name": "search_games",
        "arguments": {
            "q": "",
            "discover": True,
            "stores": list(dict.fromkeys(scope.get("stores", []))),
            "platforms": list(dict.fromkeys(platforms)),
            "keyword_hints": hints,
            "genre_ids": sorted(ids),
        },
    }


def scope_constraints(constraints, scope, text=""):
    text_platform = "PC" if re.search(r"\bPC\b|电脑|Windows", intent_text(text), re.I) else constraints.get("text_platform", "")
    return {
        **constraints,
        "platform": text_platform,
        "text_platform": text_platform,
        "stores": scope.get("stores", []),
        "platforms_any": [PLATFORMS[x][1] for x in scope.get("platforms", [])],
    }
