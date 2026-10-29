from .query import tri, interval, leaves, known, condition_text, identity, assessment_for
from .ontology import FEATURES, describe

RANKING_VERSION = "dna-interval-2"


def rank_game(game, features, query, constraints, threshold=0.6):
    from ..ranking import hard_filter

    state, checks = hard_filter(game, constraints)
    for p in query.must:
        checks.append({"constraint": "必须 " + condition_text(p), "status": tri(p, features)})
    for p in query.must_not:
        checks.append(
            {
                "constraint": "必须避开 " + condition_text(p),
                "status": {"pass": "fail", "fail": "pass", "unknown": "unknown"}[tri(p, features)],
            }
        )
    states = [x["status"] for x in checks]
    hard = "fail" if "fail" in states else "unknown" if "unknown" in states else "pass"
    W = sum(s.weight for s in query.soft_preferences)
    lo = hi = known_weight = score = ew = 0.0
    parts = []
    for soft in query.soft_preferences:
        p = soft.condition
        lower, upper = interval(p, features)
        lo += soft.weight * lower
        hi += soft.weight * upper
        if abs(lower - upper) < 1e-10:
            known_weight += soft.weight
            score += soft.weight * lower
        ls = list({identity(x): x for x in leaves(p)}.values())
        ew += soft.weight * sum(known(features, x) is not None for x in ls) / len(ls)
        evidence = list(dict.fromkeys(eid for x in ls for eid in assessment_for(features, x).get("evidence_ids", [])))
        parts.append(
            {
                "feature_key": p.feature_key or identity(p),
                "label": condition_text(p),
                "target": p.target,
                "value": known(features, p) if p.kind == "leaf" else None,
                "weight": soft.weight,
                "match": lower if lower == upper else None,
                "interval": [lower, upper],
                "evidence_ids": evidence,
                "condition": p.model_dump(),
                "actual_text": describe(p.feature_key, known(features, p))
                if p.kind == "leaf"
                else "组合偏好已满足"
                if lower == 1
                else "组合偏好部分符合",
                "opposing_evidence_ids": list(
                    dict.fromkeys(e for x in ls for e in assessment_for(features, x).get("opposing_evidence_ids", []))
                ),
                "reason": "还需要了解这一点"
                if lower != upper
                else "符合你的期待"
                if lower == 1
                else "较接近你的期待"
                if lower >= 0.75
                else "可能需要做些取舍",
            }
        )
    coverage = known_weight / W if W else None
    group = (
        "excluded"
        if hard == "fail"
        else "needs_confirmation"
        if hard == "unknown"
        else "discovery"
        if not W
        else "recommended"
        if coverage >= threshold
        else "insufficient"
    )
    positives = [
        p["actual_text"] + ("（较接近你的期待）" if p["match"] < 1 else "")
        for p in parts
        if p["match"] is not None and p["match"] >= 0.75
    ]
    if not positives:
        positives = ["符合必须条件：" + condition_text(p) for p in query.must if tri(p, features) == "pass"]
        positives += ["避开了：" + condition_text(p) for p in query.must_not if tri(p, features) == "fail"]
    tradeoffs = [
        p["label"] + "；资料显示" + p["actual_text"] for p in parts if p["match"] is not None and p["match"] < 0.75
    ]
    unknown = [p["label"] for p in parts if p["match"] is None]
    profile = [
        {
            "key": k,
            "label": FEATURES[k]["label"],
            "text": describe(k, f.get("value")),
            "category": FEATURES[k]["category"],
            "known": f.get("value") is not None and f.get("evidence_status") == "supported",
            "evidence_ids": f.get("evidence_ids", []),
        }
        for k, f in features.items()
        if k in FEATURES
    ]
    return {
        "game": game,
        "hard_status": hard,
        "checks": checks,
        "fit_score": round(100 * score / known_weight, 2) if known_weight else None,
        "fit_known": round(100 * score / known_weight, 2) if known_weight else None,
        "coverage": coverage,
        "evidence_coverage": ew / W if W else None,
        "match_lower": round(100 * lo / W, 2) if W else None,
        "match_upper": round(100 * hi / W, 2) if W else None,
        "group": group,
        "contributions": parts,
        "features": features,
        "risks": [],
        "claims": [],
        "unknowns": [FEATURES[k]["label"] for k, f in features.items() if f.get("value") is None],
        "story": {
            "headline": "有这些合拍之处"
            if positives
            else "值得继续了解"
            if group != "excluded"
            else "与你的必要条件冲突",
            "fits": positives[:3],
            "tradeoffs": tradeoffs[:3],
            "unknowns": unknown[:3],
            "summary": "它与你的必要条件冲突；其他合拍之处不能抵消这个限制。"
            if group == "excluded"
            else ("你在意的 " + "、".join(positives[:2]) + " 有依据支持。")
            if positives
            else "目前还不足以判断它是否适合你；可以先看看玩法画像。",
        },
        "portrait": profile,
    }


def sort_rows(rows):
    order = {"recommended": 0, "discovery": 1, "needs_confirmation": 2, "insufficient": 3, "excluded": 4}
    return sorted(
        rows,
        key=lambda r: (
            order[r["group"]],
            -(r["match_lower"] if r["match_lower"] is not None else -1),
            -(r["coverage"] or 0),
            -(r["fit_known"] if r["fit_known"] is not None else -1),
            r["game"]["id"],
        ),
    )
