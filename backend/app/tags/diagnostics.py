"""Explain the exact evidence bottleneck without guessing an external root cause."""

REASONS = {
    "target_reached_skip": ("已达到目标，本轮未继续调查", "为节省预算停止后续候选；该游戏尚未被判断为符合或不符合。"),
    "no_review_adapter": (
        "缺少可用的评价接口或 Steam ID",
        "当前只能分析已关联 Steam ID 的完整评价；目录信息不能代替玩家体验。",
    ),
    "not_selected": ("本轮未安排调查", "该游戏仅被目录召回；本轮优先完整核验其他候选。"),
    "budget_stopped": ("任务预算耗尽，尚未完成", "查看停止原因；缩小候选范围，或在设置中调整相应预算后重新研究。"),
    "source_failed": ("评价获取失败", "查看错误阶段和代码，检查来源网络、限流或响应；随后重新研究。"),
    "no_reviews": ("评价接口返回空结果", "没有把空响应当作不匹配；需重新获取或等待其他评价来源。"),
    "not_started": ("评价获取尚未执行", "检查任务停止原因；目录召回成功不代表评论调查已完成。"),
    "analysis_failed": ("全文分析失败", "检查模型连接、超时和结构化输出错误；不会用模型记忆填补。"),
    "not_read": ("已获取评价，但未完成全文分析", "完整正文超出批次上限或任务提前停止；未阅读文本不计作证据。"),
    "quotes_rejected": ("提取结果未通过引用校验", "查看引用拒绝原因；必须能在对应游戏的完整原文中逐字定位。"),
    "no_findings": ("已分析评价，未提取到相关主张", "当前样本没有提供可用的标签证据；不是该体验不存在。"),
    "verification_failed": ("独立核验调用失败", "待核验主张不会自动通过；检查模型错误并重新研究。"),
    "not_verified": ("主张未获独立核验支持", "查看核验意见：证据不足、被驳回或未提交，均不会计入匹配。"),
    "scope_only": (
        "证据只适用于其他或更窄的场景",
        "展开具体评价查看阶段、模式、难度或版本限定；不把局部结论推广到全程。",
    ),
    "no_tag_evidence": ("已有核验资料，但缺少本次标签证据", "核心玩法描述或无关标签不计作本次偏好匹配。"),
    "matched": ("已形成可追溯的标签证据", "可展开支持与反对意见，查看完整评价和适用场景。"),
}


def diagnose(gid, ctx, documents, claims, details, full_read):
    state = ctx.get("investigations", {}).get(gid, {})
    gc = [c for c in claims if c["game_id"] == gid]
    verified = sum(c["evidence_status"] == "supported" for c in gc)
    errors = state.get("errors", [])
    stages = {e["stage"] for e in errors}
    fetch = state.get("get_reviews", {})
    known = any(t["match"] is not None for t in details)
    code = (
        "matched"
        if known
        else "no_review_adapter"
        if state.get("review_adapter") is False
        else "not_selected"
        if gid not in ctx.get("selected", [])
        else "source_failed"
        if not documents and fetch.get("status") not in {None, "available"}
        else "analysis_failed"
        if "extract" in stages
        and not full_read
        and any(
            e["stage"] == "extract"
            and e["code"] in {"invalid_tag_model_protocol", "read_timeout", "http_500", "http_503", "network_error"}
            for e in errors
        )
        else "target_reached_skip"
        if state.get("stage") == "skipped_target_reached"
        else "budget_stopped"
        if ctx.get("stop_reason") and (not full_read or "verify" in stages)
        else "no_reviews"
        if not documents and fetch.get("status") == "available"
        else "not_started"
        if not documents
        else "analysis_failed"
        if "extract" in stages and not full_read
        else "not_read"
        if not full_read
        else "quotes_rejected"
        if not gc and state.get("extraction_rejections")
        else "no_findings"
        if not gc
        else "verification_failed"
        if not verified and "verify" in stages
        else "not_verified"
        if not verified
        else "scope_only"
        if any(t["contextual"] for t in details)
        else "no_tag_evidence"
    )
    label, action = REASONS[code]
    return {
        "code": code,
        "label": label,
        "next_step": action,
        "stage": state.get("stage", "unknown"),
        "selected": gid in ctx.get("selected", []),
        "extracted": len(gc),
        "verified": verified,
        "rejected": sum(c["evidence_status"] == "rejected" for c in gc),
        "insufficient": sum(c["evidence_status"] in {"insufficient", "pending"} for c in gc),
        "contextual": len({c["id"] for t in details for c in t["contextual"]}),
        "errors": errors,
        "stop_reason": ctx.get("stop_reason"),
        "extraction_rejections": state.get("extraction_rejections", []),
        "source_warnings": state.get("source_warnings", []),
        "review_fetch": fetch,
        "allocation_reason": state.get("stop_or_continue_reason"),
        "reused_batches": state.get("reused_batches", 0),
    }
