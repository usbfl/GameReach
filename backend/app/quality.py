"""Publication gate: research candidates are not recommendations."""

from urllib.parse import urlsplit


def public_link(value):
    try:
        p = urlsplit(value or "")
        return bool(p.scheme == "https" and p.hostname and not p.username and not p.password)
    except ValueError:
        return False


def quality_policy(settings, dataset):
    from .dna.ontology import RUBRIC

    demo = dataset == "synthetic-v1"
    return {
        "version": "complete-card-1",
        "rubric_version": RUBRIC,
        "synthetic": demo,
        "min_reviews": 1 if demo else settings.min_reviews_for_recommendation,
        "min_cited_reviews": 1 if demo else settings.min_cited_reviews,
        "min_known_mechanics": settings.min_known_mechanics,
        "review_sample_limit": settings.max_reviews_per_game,
        "review_context_limit": settings.review_context_limit,
    }


def assess_card(row, policy):
    reading = row.get("reading", {})
    stats = reading.get("diagnosis", {})
    reasons = []

    def require(ok, code, message):
        if not ok:
            reasons.append({"code": code, "message": message})

    require(
        not policy.get("unresolved_requirements"),
        "unparsed_requirement",
        "仍有无法可靠识别的必要要求：" + "、".join(policy.get("unresolved_requirements", [])),
    )
    require(row["hard_status"] == "pass", "constraints", "必要条件未全部通过")
    require(row.get("coverage") in (None, 1), "preference_unknown", "仍有未查清的兴趣条件")
    require(row.get("fit_known") is None or row["fit_known"] >= 60, "low_fit", "已知玩法与兴趣匹配不足")
    require(bool(reading.get("intro") and reading.get("intro_evidence_ids")), "intro", "缺少有依据的玩法介绍")
    require(bool(reading.get("strengths")), "strengths", "缺少核验通过的具体优点")
    require(bool(reading.get("tradeoffs")), "tradeoffs", "缺少核验通过的具体取舍")
    require(stats.get("reviews_read", 0) >= policy["min_reviews"], "reviews", "实际阅读的有效评论不足")
    require(stats.get("cited_reviews", 0) >= policy["min_cited_reviews"], "citations", "独立评论引用不足")
    require(stats.get("known_mechanics", 0) >= policy["min_known_mechanics"], "mechanics", "可确认的机制画像不足")
    if not policy["synthetic"]:
        game = row["game"]
        require(
            public_link(game.get("source_url")) or any(public_link(u) for u in game.get("store_links", {}).values()),
            "source_link",
            "缺少有效游戏来源链接",
        )
        require(reading.get("citations_have_links", False), "evidence_link", "引用缺少来源链接")
        require(len(reading.get("source_providers", [])) >= 2, "source_diversity", "尚未交叉参考目录说明与玩家评论")
    return {"eligible": not reasons, "reasons": reasons, "policy_version": policy["version"]}


def publishable(rows, policy):
    if not policy:
        return rows, []
    ready, withheld = [], []
    for row in rows:
        row["qualification"] = assess_card(row, policy)
        if row["qualification"]["eligible"]:
            ready.append(row)
        else:
            withheld.append(
                {
                    "game_id": row["game"]["id"], "name": row["game"]["name"],
                    "reasons": row["qualification"]["reasons"],
                    "diagnosis": row.get("reading", {}).get("diagnosis", {}),
                    "unmet_conditions": [c for c in row.get("checks", []) if c["status"] != "pass"],
                    "unknown_preferences": [p["label"] for p in row.get("contributions", []) if p["match"] is None],
                }
            )
    return ready, withheld


def exploration_outcome(ready, withheld, errors, policy):
    """Describe execution and evidence separately from whether a game actually fits."""
    from collections import Counter

    investigated = [w for w in withheld if w.get("diagnosis", {}).get("documents", 0) > 0]
    count = len(ready) + len(withheld)
    reason_counts = Counter(r["code"] for w in investigated for r in w["reasons"])
    if ready:
        code, title = "qualified", f"找到 {len(ready)} 款资料完整的游戏"
        detail = "其余候选未通过条件或资料尚未齐全，未加入推荐。"
    elif policy.get("unresolved_requirements"):
        code, title = "requirements_unresolved", "有必要条件还需要明确"
        detail = "程序还不能可靠判断：" + "、".join(policy["unresolved_requirements"]) + "。本轮未花费模型或联网预算。"
    elif any("预算" in e or "时间已达" in e for e in errors):
        code, title = "budget_exhausted", "调查预算用尽，尚未形成完整推荐"
        detail = "这不表示没有符合兴趣的游戏；本轮未能完成所需资料和核验。"
    elif errors:
        code, title = "execution_error", "本次调查中断，尚不能判断是否匹配"
        detail = (
            "模型返回格式未通过校验，相关判断没有被当作成功。"
            if "invalid_model_protocol" in errors
            else "网络代理阻止了请求；已保存完成的调查，没有换用其他网络路线或模拟结果。"
            if "proxy_blocked" in errors
            else "来源或模型调用未成功，请结合下方具体错误检查连接；没有切换为模拟数据。"
        )
    elif not count:
        code, title = "no_candidates", "本轮有限目录搜索没有召回候选"
        detail = "这是目录召回结果，不代表整个游戏市场没有匹配游戏。"
    else:
        code, title = "no_qualified_cards", "暂时没有资料齐全且通过条件的推荐"
        unknown = list(dict.fromkeys(p for w in investigated for p in w.get("unknown_preferences", [])))
        detail = (
            "已调查的候选仍缺少对“" + "、".join(unknown[:3]) + "”的可靠判断；未查清不等于不适合你。"
            if unknown else "候选的条件或证据尚未达到要求；不会把未查清的游戏说成不适合你。"
        )
    return {
        "code": code, "title": title, "message": detail,
        "candidate_count": count, "investigated_count": len(ready) + len(investigated),
        "recommendation_count": len(ready), "reason_counts": dict(reason_counts),
    }
