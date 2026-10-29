"""Deterministic, review-backed ranking. Catalog labels never become evidence."""

import hashlib
import math
import re
from .catalog import TAGS
from .diagnostics import diagnose
from ..ranking import hard_filter
from ..retrieval import usable

POLICY = {
    "version": "focus-rank-2",
    "base": 15,
    "signed_match_weight": 40,
    "verified_weight_bonus": 30,
    "verified_weight_saturation": 6,
    "quantity_weight": 10,
    "rating_weight": 5,
    "conflict_penalty": 25,
    "quantity_saturation_per_tag": 3,
    "note": "未知项不进入得分分母；已核验权重最多6点饱和。初始工程公式，未经真实偏好实验校准。",
}


def independent_ids(documents):
    parent = {d["id"]: d["id"] for d in documents}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    seen = {}
    for d in documents:
        text = re.sub(r"\W+", "", d["text"].casefold())
        keys = [("text", hashlib.sha256(text.encode()).hexdigest())]
        if d.get("author_key"):
            keys.append(("author", d["provider"], d["author_key"]))
        for key in keys:
            if key in seen:
                parent[root(d["id"])] = root(seen[key])
            else:
                seen[key] = d["id"]
    return {did: root(did) for did in parent}


def rating_value(rating):
    if not rating:
        return None
    p, n = rating.get("positive"), rating.get("negative")
    if type(p) is not int or type(n) is not int or min(p, n) < 0 or p + n == 0:
        return None
    total, z = p + n, 1.96
    share = p / total
    lower = (share + z * z / (2 * total) - z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total))) / (
        1 + z * z / total
    )
    return dict(rating, total=total, positive_rate=round(share, 4), wilson_lower=round(lower, 4))


def rank(db, games, claims, documents, ctx, priorities=None):
    intents = ctx.get("interpretation", {}).get("intents", [])
    priorities = priorities or {}
    ids = independent_ids([d for d in documents if d["kind"] == "reviews"])
    allowed = set(ctx.get("full_reviewed_document_ids", []))
    with db.Session() as s:
        valid = {d["id"] for d in documents if usable(s, d["id"], ctx["dataset_id"])}
    verified = [c for c in claims if c["evidence_status"] == "supported" and c["document_id"] in valid]
    rows = []
    for game in games:
        gid = game["id"]
        gc = [c for c in verified if c["game_id"] == gid]
        details = []
        all_support, all_oppose = set(), set()
        for intent in intents:
            support, oppose, contextual = [], [], []
            for c in gc:
                if (
                    c.get("tag_id") != intent["tag_id"]
                    or c["document_id"] not in allowed
                    or c["document_id"] not in ids
                ):
                    continue
                cs, target = c.get("scope", {}), intent.get("scope", {})
                # A claim about endgame cannot establish a property of the entire game.
                if any(cs.get(k) != v for k, v in target.items()) or any(k not in target for k in cs):
                    contextual.append(c)
                    continue
                present = c.get("tag_polarity") == "present"
                (support if present == (intent["polarity"] == "want") else oppose).append(c)
            si = {ids[c["document_id"]] for c in support}
            oi = {ids[c["document_id"]] for c in oppose}
            all_support |= si
            all_oppose |= oi
            details.append(
                dict(
                    intent,
                    label=TAGS[intent["tag_id"]]["label"],
                    priority=priorities.get(intent["tag_id"], intent["priority"]),
                    support=support,
                    oppose=oppose,
                    contextual=contextual,
                    support_count=len(si),
                    oppose_count=len(oi),
                    match=round(len(si) / (len(si) + len(oi)), 4) if si or oi else None,
                )
            )
        total = sum(i["priority"] for i in details)
        known = sum(i["priority"] for i in details if i["match"] is not None)
        coverage = known / total if total else None
        match = sum(i["priority"] * i["match"] for i in details if i["match"] is not None) / known if known else None
        quantity = sum(i["priority"] * min(i["support_count"], 3) / 3 for i in details) / known if known else 0
        conflict = sum(i["priority"] * (1 - i["match"]) for i in details if i["match"] is not None)
        verified_bonus = min(known / 6, 1)
        rating = rating_value(ctx.get("ratings", {}).get(gid))
        rating_prior = rating["wilson_lower"] if rating else 0.5
        score = (
            round(
                max(
                    0,
                    min(
                        100,
                        15
                        + 40 * (2 * match - 1)
                        + 30 * verified_bonus
                        + 10 * quantity
                        + 5 * rating_prior
                        - 25 * min(conflict / 3, 1),
                    ),
                ),
                2,
            )
            if known
            else None
        )
        factual, checks = hard_filter(game, ctx.get("constraints", {}))
        red_fail = [
            i for i in details if i.get("explicit_redline", False) and i["oppose_count"] and not i["support_count"]
        ]
        red_unknown = [
            i for i in details if i.get("explicit_redline", False) and (i["match"] is None or i["oppose_count"])
        ]
        custom_hard = [
            c["text"]
            for c in ctx.get("custom_requirements", [])
            if c.get("explicit_redline") and c.get("status") != "ignored"
        ]
        checks += [
            {
                "constraint": "玩法：" + i["label"],
                "status": "fail" if i in red_fail else "unknown" if i in red_unknown else "pass",
            }
            for i in details
            if i.get("explicit_redline")
        ]
        checks += [{"constraint": "自定义：" + text, "status": "unknown"} for text in custom_hard]
        hard_status = (
            "fail"
            if factual == "fail" or red_fail
            else "unknown"
            if factual == "unknown" or red_unknown or custom_hard
            else "pass"
        )
        wanted = [i for i in details if i["polarity"] == "want" and i.get("source_id", "current") == "current"]
        wanted_missing = bool(wanted) and not any(i["support_count"] for i in wanted)
        group = (
            "excluded"
            if factual == "fail" or red_fail
            else "unreviewed"
            if not known
            else "provisional"
            if hard_status == "unknown" or not all_support or wanted_missing
            else "tradeoffs"
            if all_oppose
            else "recommended"
        )
        gdocs = [d for d in documents if d["game_id"] == gid and d["kind"] == "reviews"]
        full_read = sum(d["id"] in allowed for d in gdocs)
        diagnosis = diagnose(gid, ctx, gdocs, claims, details, full_read)
        introduction = [c for c in gc if c.get("aspect") == "core_loop"]
        strengths = [c for c in gc if c.get("aspect") == "strength"]
        tradeoffs = [c for c in gc if c.get("aspect") == "tradeoff"]

        def unique_claims(items):
            return list({c["id"]: c for c in items}.values())

        extra_strengths, extra_tradeoffs = strengths, tradeoffs
        strengths = unique_claims([c for t in details for c in t["support"]])
        tradeoffs = unique_claims([c for t in details for c in t["oppose"]])
        missing = []
        if not all_support:
            missing.append("尚无支持本次偏好的完整评价")
        card = {
            "introduction": introduction,
            "strengths": strengths,
            "tradeoffs": tradeoffs,
            "complete": not missing,
            "missing": missing,
            "additional_strengths": extra_strengths,
            "additional_tradeoffs": extra_tradeoffs,
            "limitations": ([] if introduction else ["核心玩法介绍尚无已核验原文"])
            + ([] if tradeoffs else ["当前样本尚未核验出明确取舍，不代表没有缺点"])
            + [t["label"] + "：尚待证实" for t in details if t["match"] is None],
        }
        rows.append(
            {
                "game": game,
                "card": card,
                "group": group,
                "rank_score": score,
                "match": match,
                "coverage": coverage if full_read else None,
                "quantity": round(quantity, 4),
                "rating": rating,
                "rating_missing_neutral_prior": rating is None,
                "supporting_reviews": len(all_support),
                "opposing_reviews": len(all_oppose),
                "tags": details,
                "checks": checks,
                "hard_constraint_status": hard_status,
                "partial_match": any(i["match"] is None for i in details),
                "focus_diagnostics": {
                    "verified_dimensions": sum(i["match"] is not None for i in details),
                    "unknown_dimensions": sum(i["match"] is None for i in details),
                    "verified_weight": known,
                    "requested_weight": total,
                    "conflict_weight": conflict,
                },
                "critical_unknowns": [c["constraint"] for c in checks if c["status"] == "unknown"],
                "claims": gc,
                "diagnosis": diagnosis,
                "reading": {
                    "fetched": len(gdocs),
                    "full_read": full_read,
                    "deferred": sum(d["id"] not in allowed for d in gdocs),
                    "sampling": ctx.get("sampling", {}).get(gid),
                },
                "unknowns": [i["label"] for i in details if i["match"] is None],
                "excluded_reasons": [c["constraint"] for c in checks if c["status"] == "fail"]
                + [i["label"] for i in red_fail],
            }
        )
    order = {"recommended": 0, "tradeoffs": 0, "provisional": 1, "unreviewed": 2, "excluded": 3}
    return sorted(rows, key=lambda r: (order[r["group"]], -(r["rank_score"] or 0), r["game"]["id"]))
