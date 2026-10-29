import re


def extract_preferences(text):
    # One parser for exploration and feedback; a dislike must not become a positive preference.
    from .dna.query import compile_query, as_legacy

    return as_legacy(compile_query(text))


def constraints_from_text(text, constraints):
    out = dict(constraints)
    if not out.get("language") and any(word in text for word in ["必须中文", "必须支持中文", "要求中文"]):
        out["language"] = "zh-CN"
    m = re.search(r"(?:预算|不超过|低于|最多)\s*(\d+(?:\.\d+)?)\s*(?:元|块)?", text)
    if m and out.get("max_price") is None:
        out["max_price"] = float(m.group(1))
    return out


def hard_filter(game, constraints):
    checks = []

    def add(name, value):
        checks.append({"constraint": name, "status": "unknown" if value is None else ("pass" if value else "fail")})

    platform = constraints.get("platform", "PC")
    if platform:
        add(f"平台 {platform}", None if game.get("platforms") is None else platform in game["platforms"])
    selected_platforms = constraints.get("platforms_any", [])
    if selected_platforms:
        add(
            "运行平台任一：" + " / ".join(selected_platforms),
            None if game.get("platforms") is None else bool(set(selected_platforms).intersection(game["platforms"])),
        )
    stores = constraints.get("stores", [])
    if stores:
        links = game.get("store_links", {})
        add("商店任一：" + " / ".join(stores), True if set(stores).intersection(links) else None)
    if constraints.get("released_only", True):
        add("正式发售", game.get("released"))
    add("游戏本体", None if game.get("kind") is None else game["kind"] in ["base", "remake", "remaster"])
    add("未玩过", game["id"] not in constraints.get("played", []) and game["name"] not in constraints.get("played", []))
    if constraints.get("language"):
        langs = game.get("languages")
        add(f"语言 {constraints['language']}", None if langs is None else constraints["language"] in langs)
    if constraints.get("max_price") is not None:
        p = game.get("price")
        known = p and p.get("currency") == constraints.get("currency") and p.get("region") == constraints.get("region")
        add(
            f"预算 ≤ {constraints['max_price']} {constraints.get('currency')}",
            p["amount"] <= constraints["max_price"] if known else None,
        )
    statuses = [c["status"] for c in checks]
    return ("fail" if "fail" in statuses else "unknown" if "unknown" in statuses else "pass"), checks
