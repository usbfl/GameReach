"""Local allocation heuristics: no model routing, prices, or evidence shortcuts."""

from .catalog import catalog_priority, catalog_matches, TAGS
from .ranking import independent_ids

VERSION = "budget-strategy-2"
LABELS = {"quick": "快速探索", "balanced": "均衡精选", "thorough": "扩大调查"}


def policy(settings, name="balanced", target=5):
    quick = name == "quick"
    return {
        "version": VERSION,
        "name": name,
        "label": LABELS[name],
        "scan_limit": min(settings.tag_scan_limit, 150) if quick else settings.tag_scan_limit,
        "shortlist_limit": min(settings.tag_candidate_limit, 20 if quick else 30 if name == "balanced" else 80),
        "initial_games": min(settings.tag_investigation_limit, 3 if quick else 5),
        "investigation_limit": min(
            settings.tag_investigation_limit, 3 if quick else max(5, target * 2) if name == "balanced" else 20
        ),
        "max_batches": 1 if quick else settings.tag_max_batches_per_game,
        "reserve_next_game": name != "thorough",
    }


def diversified(db, ids, intents):
    # Current-input relevance stays the first key. Diversity only breaks relevance ties.
    remaining = sorted(set(ids))
    games = {gid: db.get("game", gid) for gid in remaining}
    scores = {gid: catalog_priority(game, intents) for gid, game in games.items()}
    matches = {gid: set(catalog_matches(game, intents)) for gid, game in games.items()}
    positions = {gid: n for n, gid in enumerate(remaining)}
    ordered, covered = [], set()
    while remaining:
        gid = max(remaining, key=lambda g: (scores[g][0], len(matches[g] - covered), scores[g][1], -positions[g]))
        ordered.append(gid)
        covered.update(matches[gid])
        remaining.remove(gid)
    return ordered


def evidence_gap(row, documents, reviewed):
    """Return actionable gaps; unknown ratings/platform facts cannot be fixed by more reviews."""
    if not row or row["group"] == "excluded":
        return {"needed": False, "reason": "已排除或没有调查对象", "intents": []}
    tags = row["tags"]
    primary = [t for t in tags if t.get("source_id", "current") == "current"] or tags
    top = max((t["priority"] for t in primary), default=0)
    relevant = sorted(
        [t for t in tags if t.get("explicit_redline") or (t in primary and t["priority"] == top)],
        key=lambda t: (not t.get("explicit_redline", False), -t["priority"]),
    )
    gaps = [t for t in relevant if t["match"] is None or (t["support_count"] and t["oppose_count"])]
    reviews = [d for d in documents if d["kind"] == "reviews" and d["id"] in reviewed]
    independent = len(set(independent_ids(reviews).values()))
    # Avoid stopping on one positive voice when independent counterexamples are available.
    weak = bool(row["supporting_reviews"]) and independent < 2
    negative_pending = any(
        d.get("sampling_stratum", "").startswith("negative") and d["id"] not in reviewed for d in documents
    )
    negative_read = any(d.get("sampling_stratum", "").startswith("negative") for d in reviews)
    missing_intro = not row["card"]["introduction"] and not row["supporting_reviews"]
    need_support = not row["supporting_reviews"]
    reasons = []
    if gaps:
        reasons.append("主要需求未知或存在相反观点")
    if missing_intro:
        reasons.append("缺少有来源的玩法介绍")
    if need_support:
        reasons.append("尚无偏好支持")
    if weak:
        reasons.append("仅有一位独立玩家")
    if negative_pending and not negative_read:
        reasons.append("尚未读取已有差评样本")
    return {
        "needed": bool(reasons),
        "reason": "；".join(reasons) or "卡片及主要证据已足够，保留次要未知项",
        "intents": gaps or relevant,
        "needs_intro": missing_intro,
        "needs_support": need_support,
        "needs_balance": weak or (negative_pending and not negative_read),
    }


def useful_unread(documents, reviewed, intents, gap):
    """Keep whole texts, deduplicate already-read authors/text, prioritize relevant gaps."""
    reviews = [d for d in documents if d["kind"] == "reviews"]
    identities = independent_ids(reviews)
    used = {identities[d] for d in reviewed if d in identities}
    terms = {
        x.casefold()
        for i in gap["intents"]
        for x in TAGS[i["tag_id"]]["aliases"] + TAGS[i["tag_id"]]["igdb"]["keywords"]
    }
    selected = []
    # Relevant first; retain opposing/recent strata through the existing balanced batch selector.
    for d in sorted(reviews, key=lambda d: (-sum(t in d["text"].casefold() for t in terms), d["id"])):
        if d["id"] in reviewed or identities[d["id"]] in used:
            continue
        if (
            gap["needs_intro"]
            or gap["needs_support"]
            or gap["needs_balance"]
            or any(t in d["text"].casefold() for t in terms)
        ):
            selected.append(d)
            used.add(identities[d["id"]])
    if gap["needs_intro"]:
        selected += [d for d in documents if d["kind"] == "details"]
    return selected
