"""Human-readable cards are projections of checked claims, never another fact generator."""

from .knowledge import Knowledge
from .db import Claim, Document, Task
from sqlalchemy import select
from .retrieval import usable
from .quality import public_link


def reader_card(db, task_id, row):
    knowledge = Knowledge(db)
    task = db.task(task_id) if task_id else None
    ctx = task["context"] if task else {}
    gid = row["game"]["id"]
    documents = [d for d in knowledge.documents(ctx.get("document_ids", [])) if d["game_id"] == gid]
    claims = [c for c in knowledge.claims(task_id) if c["game_id"] == gid] if task_id else []
    supported = [c for c in claims if c["evidence_status"] == "supported"]
    # Reuse checked experience text together with cached DNA, within the same dataset.
    cached = []
    if task:
        with db.Session() as session:
            history = session.scalars(select(Claim).where(Claim.game_id == gid)).all()
            for record in history:
                c = record.data
                if c.get("evidence_status") != "supported":
                    continue
                doc = session.get(Document, c["document_id"])
                if not doc or not usable(session, doc.id, ctx.get("dataset_id")):
                    continue
                cached.append(c)
    all_supported = sorted({c["id"]: c for c in cached}.values(), key=lambda c: c.get("assessed_at", ""), reverse=True)

    def by_aspect(aspect):
        return [
            {
                "text": (" / ".join(c.get("scope", {}).values()) + "：" if c.get("scope") else "") + c["claim_text"],
                "evidence_ids": c["evidence_ids"],
                "scope": c.get("scope", {}),
            }
            for c in all_supported
            if c.get("aspect") == aspect
        ][:3]

    known = [p["text"] for p in row["portrait"] if p["known"]]
    loops = by_aspect("core_loop")
    intro = loops[0]["text"] if loops else "；".join(known[:3]) + "。" if known else ""
    reviews = [d for d in documents if d.get("kind") == "reviews"]
    current_reviewed = set(ctx.get("reviewed_document_ids", []))
    reviewed_ids = set(current_reviewed)
    # Reuse only auditable, still-current input samples. Old fetched-count-only reports
    # cannot establish that a reviewer actually saw a document.
    with db.Session() as session:
        histories = session.scalars(select(Task).order_by(Task.created_at.desc()).limit(200)).all()
        for history in histories:
            if history.context.get("dataset_id") == ctx.get("dataset_id"):
                reviewed_ids.update(history.context.get("reviewed_document_ids", []))
        valid_reviews = {}
        for did in reviewed_ids:
            doc = session.get(Document, did)
            if (
                doc
                and doc.game_id == gid
                and doc.data.get("kind") == "reviews"
                and usable(session, did, ctx.get("dataset_id"))
            ):
                valid_reviews[did] = doc.data
    review_ids = set(valid_reviews)
    # Identical pasted reviews are one sample, regardless of how many author IDs exist.
    read_count = len({d["content_hash"] for d in valid_reviews.values()})
    displayed = loops[:1] + by_aspect("strength") + by_aspect("tradeoff") + by_aspect("audience")
    displayed_evidence = {e for item in displayed for e in item["evidence_ids"]}
    displayed_evidence.update(e for p in row["portrait"] if p["known"] for e in p["evidence_ids"])
    used_documents = {}
    for eid in displayed_evidence:
        source = knowledge.evidence(eid)
        if source:
            used_documents[source["document"]["id"]] = source["document"]
    cited_reviews = [d for d in used_documents.values() if d.get("kind") == "reviews"]
    providers = sorted({d["provider"] for d in used_documents.values()})
    investigated = gid in ctx.get("selected", []) and bool(documents or ctx.get("errors"))
    if not task:
        reason, stage = "这是目录候选；点击开始探索后才会调查评论。", "catalog"
    elif known and not documents:
        reason, stage = "沿用此前已核验的玩法资料，本轮没有重新读取评论；可在证据抽屉查看原文与时间。", "cached"
    elif not investigated:
        reason, stage = "本轮有限调查未覆盖这款游戏。它只是扩展候选，尚未检查玩家评价。", "not_investigated"
    elif ctx.get("llm_mode") == "mock" and ctx.get("data_mode") == "live":
        reason, stage = (
            "真实资料已经读取，但规则模型不能解释真实评论。请把 LLM_MODE 设置为 live 后重新探索。",
            "model_required",
        )
    elif not reviews:
        reason, stage = (
            (
                "没有可用玩家评论："
                + (
                    "尚未核对 Steam ID。"
                    if not row["game"].get("external_ids", {}).get("steam")
                    else "请检查本次来源错误、限流或空样本记录。"
                )
            ),
            "reviews_missing",
        )
    elif not claims:
        reason, stage = "已读到评论，但提取尚未完成。请查看模型错误或预算记录，再重试探索。", "extraction_incomplete"
    elif not supported:
        reason, stage = "已提取主张，但尚无逐项核验通过的结论；不会把猜测填成画像。", "verification_incomplete"
    elif not known:
        reason, stage = (
            "评论能描述部分体验，但没有足够信息判定这次量表中的机制，或证据存在作用域、冲突、时效限制。",
            "mechanics_missing",
        )
    else:
        reason, stage = "已经读到部分玩法依据；没有提及的机制仍保留空白。", "researched"
    return {
        "intro": intro,
        "intro_evidence_ids": loops[0]["evidence_ids"]
        if loops
        else list(dict.fromkeys(e for p in row["portrait"] if p["known"] for e in p["evidence_ids"]))[:3],
        "strengths": by_aspect("strength"),
        "tradeoffs": by_aspect("tradeoff"),
        "audience": by_aspect("audience"),
        "style": ctx.get("profile_snapshot", {}).get("reading_style", "plain"),
        "diagnosis": {
            "stage": stage,
            "message": reason,
            "documents": len(documents),
            "reviews": len(reviews),
            "reviews_read": read_count,
            "reused_reviews": len(review_ids - current_reviewed),
            "cited_reviews": len({d["content_hash"] for d in cited_reviews}),
            "claims": len(claims),
            "verified_claims": len(supported),
            "known_mechanics": len(known),
        },
        "source_providers": providers,
        "source_kinds": sorted({d["kind"] for d in used_documents.values()}),
        "citation_document_ids": list(used_documents),
        "citations_have_links": bool(used_documents)
        and all(public_link(d.get("url")) for d in used_documents.values()),
        "sample_note": "按好评、差评、近期评论分层取样，数量不用于推算好评率；玩家观点不代表共识。体验评价目前来自 Steam，目录说明来自 IGDB。"
        if ctx.get("data_mode") == "live"
        else "合成资料演示，不代表真实游戏或真实玩家。",
        "video_url": row["game"].get("store_links", {}).get("steam"),
        "video_label": "去 Steam 商店看预告与玩法展示",
        "catalog_description": row["game"].get("summary", "")[:400] if not intro else "",
    }
