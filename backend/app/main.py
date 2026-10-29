import asyncio
import copy
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse, StreamingResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.exceptions import RequestValidationError
from sqlalchemy import select
from .config import Settings, ROOT
from .db import DB, Task, Report, now, uid
from .schemas import ResearchRequest, HumanInput, RoleModes, ShortlistInput, ProfileInput, FeedbackInput, FEATURE_LABELS
from .tools import Registry, Gate
from .engine import Engine, TERMINAL
from .shortlist import ShortlistService
from .discovery import discovery_action, scope_constraints
from .dna.routes import install_routes, add_references
from .player import install_player_routes
from .interests import install_interest_routes
from .tags.routes import install_tag_routes
from .dna.query import compile_query, as_legacy
from .dna.projection import make_snapshot, local_recall
from .ranking import constraints_from_text, hard_filter, extract_preferences


def create_app(settings=None, db=None, run_worker=True):
    settings = settings or Settings()
    db = db or DB(settings.resolved_db_url())
    registry = Registry(settings, db)
    engine = Engine(settings, db, registry)
    shortlist = ShortlistService(settings, db)

    @asynccontextmanager
    async def lifespan(app):
        db.create()
        engine.retrieval.rebuild(force=False)
        if settings.data_mode == "fixtures":
            for g in registry.fixture.games.values():
                db.put("game", g["id"], g)
        if db.get("profile", "default") is None:
            db.put("profile", "default", ProfileInput().model_dump() | {"version": 1, "feedback": []})
        engine.recover()
        workers = []
        if run_worker:
            workers = [asyncio.create_task(engine.worker())]
        yield
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        await registry.close()

    app = FastAPI(title="GameReach", version="0.11.0", lifespan=lifespan)
    app.state.db, app.state.engine, app.state.registry, app.state.shortlist = db, engine, registry, shortlist
    install_tag_routes(app, db, engine)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    @app.middleware("http")
    async def local_only(request, call_next):
        origin = request.headers.get("origin")
        allowed = {
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        }
        if origin and origin not in allowed:
            return JSONResponse({"error": {"code": "origin_denied", "message": "仅允许本地工作台来源"}}, 403)
        if int(request.headers.get("content-length", "0")) > (
            600_000 if request.url.path == "/api/player/import/file" else 100_000
        ):
            return JSONResponse({"error": {"code": "too_large", "message": "请求过大"}}, 413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse(
            {
                "error": error.detail
                if isinstance(error.detail, dict)
                else {"code": str(error.status_code), "message": error.detail}
            },
            error.status_code,
        )

    @app.exception_handler(ValueError)
    async def value_error(request, error):
        return JSONResponse({"error": {"code": "invalid_request", "message": str(error)}}, 422)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse(
            {
                "error": {
                    "code": "validation_error",
                    "message": "请求字段不符合协议",
                    "fields": [{"loc": e["loc"], "msg": e["msg"]} for e in error.errors()],
                }
            },
            422,
        )

    def task_or_404(tid):
        t = db.task(tid)
        if not t:
            raise HTTPException(404, "任务不存在")
        return t

    def report_or_404(tid):
        with db.Session() as s:
            r = s.scalar(select(Report).where(Report.task_id == tid).order_by(Report.version.desc()))
            if not r:
                raise HTTPException(404, "报告尚未生成")
            return r

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "version": "0.11.0"}

    @app.get("/api/settings/public")
    async def public_settings():
        return {
            "data_mode": settings.data_mode,
            "llm_mode": settings.llm_mode,
            "display_timezone": settings.display_timezone,
            "demo_controls": settings.enable_demo_controls and settings.data_mode == "fixtures",
            "missing_config": settings.diagnostics(),
            "model": settings.llm_model or None,
            "feature_labels": FEATURE_LABELS,
            "limits": {
                k: getattr(settings, k)
                for k in [
                    "max_tool_calls",
                    "max_llm_calls",
                    "max_external_requests",
                    "max_active_seconds",
                    "reuse_enabled",
                    "reuse_ttl_seconds",
                    "source_catalog_ttl_seconds",
                    "source_details_ttl_seconds",
                    "source_reviews_ttl_seconds",
                    "tag_scan_limit",
                    "tag_candidate_limit",
                    "tag_investigation_limit",
                    "tag_max_batches_per_game",
                    "tag_review_batch_size",
                    "tag_review_batch_chars",
                    "tag_review_fetch_limit",
                    "max_candidates",
                    "max_investigated_games",
                    "max_reviews_per_game",
                    "review_context_limit",
                    "min_reviews_for_recommendation",
                    "min_cited_reviews",
                    "min_known_mechanics",
                ]
            },
        }

    @app.post("/api/research", status_code=202)
    async def create_research(body: ResearchRequest):
        return {"task_id": engine.create(body)}

    @app.get("/api/research")
    async def tasks(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
        with db.Session() as s:
            rows = s.scalars(select(Task).order_by(Task.created_at.desc()).offset(offset).limit(limit))
            return {
                "items": [
                    {
                        "id": t.id,
                        "status": t.status,
                        "phase": t.phase,
                        "query": t.request["query"],
                        "kind": t.request["kind"],
                        "data_mode": t.context["data_mode"],
                        "llm_mode": t.context["llm_mode"],
                        "created_at": t.created_at,
                    }
                    for t in rows
                ],
                "offset": offset,
                "limit": limit,
            }

    @app.get("/api/research/{tid}")
    async def task(tid: str):
        t = task_or_404(tid)
        t["claims"] = engine.knowledge.claims(tid)
        t["documents"] = engine.knowledge.documents(t["context"]["document_ids"])
        ids = list(dict.fromkeys(t["context"].get("selected", []) + t["context"].get("candidates", [])))
        catalog = [db.get("game", gid) for gid in ids] if ids else db.list("game")
        t["candidate_games"] = [g for g in catalog if g and g["dataset_id"] == t["context"]["dataset_id"]][
            : settings.max_candidates
        ]
        if t["status"] in ["completed", "partial"]:
            t["report"] = report_or_404(tid).data
        return t

    @app.get("/api/research/{tid}/events")
    async def events(tid: str, request: Request, after: int = Query(0, ge=0)):
        task_or_404(tid)
        try:
            after = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(422, "Last-Event-ID 必须为整数")

        async def stream():
            last = after
            heartbeats = 0
            while not await request.is_disconnected():
                rows = db.events(tid, last)
                for e in rows:
                    last = e["id"]
                    yield f"id: {last}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n"
                if db.task(tid)["status"] in TERMINAL and not rows:
                    yield "event: done\ndata: {}\n\n"
                    break
                heartbeats += 1
                if heartbeats % 20 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    @app.post("/api/research/{tid}/cancel")
    async def cancel(tid: str):
        t = task_or_404(tid)
        if t["status"] in TERMINAL:
            raise HTTPException(409, "任务已结束")
        with db.Session.begin() as s:
            r = s.get(Task, tid)
            r.status = "cancelled"
            r.version += 1
            r.updated_at = now()
        db.event(tid, "human", "task_cancelled", "用户取消了任务，已阻止新调用。")
        return {"status": "cancelled"}

    @app.post("/api/research/{tid}/pause")
    async def pause(tid: str):
        t = task_or_404(tid)
        if t["status"] not in ["queued", "running"]:
            raise HTTPException(409, "当前状态不能暂停")
        with db.Session.begin() as s:
            r = s.get(Task, tid)
            r.status = "paused"
            r.version += 1
        db.event(tid, "human", "task_paused", "暂停请求已保存；当前只读调用安全收尾后停止。")
        return {"status": "paused"}

    @app.post("/api/research/{tid}/resume")
    async def resume(tid: str):
        t = task_or_404(tid)
        if t["status"] != "paused" or t["context"].get("pending_human"):
            raise HTTPException(409, "当前状态不能直接恢复；人工节点请提交表单")
        with db.Session.begin() as s:
            r = s.get(Task, tid)
            r.status = "queued"
            r.version += 1
        db.event(tid, "human", "task_resumed", "从最近检查点恢复。")
        return {"status": "queued"}

    @app.patch("/api/research/{tid}/role-modes")
    async def modes(tid: str, body: RoleModes):
        t = task_or_404(tid)
        if t["status"] in TERMINAL:
            raise HTTPException(409, "任务已结束")
        with db.Session.begin() as s:
            r = s.get(Task, tid)
            r.request = r.request | {"role_modes": body.model_dump()}
            r.version += 1
            ctx = copy.deepcopy(r.context)
            if ctx.get("pending_human") and body.model_dump()[ctx["pending_human"]["role"]] == "ai":
                ctx["pending_human"] = None
                r.context = ctx
                r.status = "queued"
        db.event(tid, "human", "role_modes_changed", "角色设置已更新，将在下一个安全节点生效。", body.model_dump())
        return {"status": "updated"}

    @app.post("/api/research/{tid}/human-actions")
    async def human(tid: str, body: HumanInput):
        task_or_404(tid)
        try:
            return engine.human(tid, body)
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.get("/api/research/{tid}/report")
    async def report(tid: str, format: str = Query("json", pattern="^(json|markdown)$")):
        r = report_or_404(tid)
        if format == "markdown":
            return Response(
                r.markdown,
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="GameReach-{tid}.md"'},
            )
        return JSONResponse(r.data, headers={"Content-Disposition": f'attachment; filename="GameReach-{tid}.json"'})

    install_routes(app, db, settings)
    install_player_routes(app, db, settings, registry)
    install_interest_routes(app, db, settings, registry)

    @app.post("/api/discovery")
    async def discover(body: ResearchRequest):
        preferences = {p["feature_key"]: p for p in extract_preferences(body.query)}
        preferences.update({p.feature_key: p.model_dump() for p in body.preferences})
        scope = body.discovery.model_dump()
        try:
            dna_query = body.dna_query or compile_query(
                body.query, body.preferences, scope, db.get("profile", "default", {}).get("preferences", [])
            )
        except ValueError as exc:
            raise HTTPException(422, {"code": "validation_error", "message": str(exc)[:500]})
        if dna_query.discovery_scope.model_dump() != scope:
            raise HTTPException(422, "DNA 查询与平台范围不一致")
        dataset = "synthetic-v1" if settings.data_mode == "fixtures" else "live"
        try:
            dna_query = add_references(db, dataset, dna_query)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        action = discovery_action(body.query, as_legacy(dna_query), scope)
        if settings.data_mode == "fixtures":
            action = {"tool_name": "search_games", "arguments": {"q": ""}}
        result = await registry.call(action, Gate(settings))
        constraints = scope_constraints(constraints_from_text(body.query, body.constraints.model_dump()), scope)
        items = []
        local = local_recall(db, dataset, dna_query, scope, settings.max_candidates)
        catalog = result.data.get("games", [])
        candidates = list({g["id"]: g for g in local + catalog}.values())[: settings.max_candidates]
        for game in candidates:
            state, checks = hard_filter(game, constraints)
            if state != "fail":
                items.append({**game, "constraint_status": state, "constraint_checks": checks})
        snapshot = make_snapshot(db, dataset, items, dna_query, constraints, threshold=settings.min_coverage)
        return {
            "dna_query": dna_query.model_dump(),
            "candidate_snapshot_id": snapshot["id"],
            "dna_results": snapshot["rows"],
            "items": items,
            "status": result.status,
            "error": result.error_code,
            "data_mode": settings.data_mode,
            "scope": scope,
            "preferences": list(preferences.values()),
            "search_info": result.data.get("search_info", {}),
            "note": "候选预览不调用模型；类型/热度仅用于召回，适配结论需要后续证据。"
            + (
                " 演示数据未标注真实商店，商店限制为待确认。"
                if settings.data_mode == "fixtures" and scope["stores"]
                else ""
            ),
        }

    @app.get("/api/games/search")
    async def search(
        q: str = Query("", max_length=120),
        store: str = Query("all", pattern="^(all|steam|gog|epic|itch)$"),
        platform: str = Query("all", pattern="^(all|pc|switch|ps5|xbox_series)$"),
    ):
        if settings.data_mode == "fixtures":
            rows = [
                g
                for g in registry.fixture.games.values()
                if q.lower() in (g["name"] + g["subtitle"] + " ".join(g["tags"])).lower()
            ]
            return {"items": rows, "status": "available"}
        result = await registry.call(
            {"tool_name": "search_games", "arguments": {"q": q, "store": store, "platform": platform}}, Gate(settings)
        )
        return {
            "items": result.data.get("games", []),
            "status": result.status,
            "error": result.error_code,
            "search_info": result.data.get("search_info"),
            "from_cache": result.from_cache,
        }

    @app.get("/api/games/local")
    async def local_games():
        dataset = "synthetic-v1" if settings.data_mode == "fixtures" else "live"
        return {
            "items": [g for g in db.list("game") if g["dataset_id"] == dataset][:200],
            "external_requests": 0,
            "model_calls": 0,
        }

    @app.get("/api/games/{gid}")
    async def game(gid: str):
        g = db.get("game", gid)
        if not g:
            raise HTTPException(404, "游戏未收录")
        return g

    @app.get("/api/evidence/{eid}")
    async def evidence(eid: str):
        e = engine.knowledge.evidence(eid)
        if not e:
            raise HTTPException(404, "证据不存在")
        return e

    @app.get("/api/profile")
    async def profile():
        return db.get("profile", "default") | {
            "revisions": sorted(db.list("revision"), key=lambda r: r["created_at"], reverse=True)
        }

    def update_profile(body, reason, status="applied", replace=False):
        old = db.get("profile", "default")
        new = (body if replace else old | body) | {"version": old["version"] + 1}
        rev = {"id": uid("rev-"), "before": old, "after": new, "reason": reason, "status": status, "created_at": now()}
        db.put("revision", rev["id"], rev)
        if status == "applied":
            db.put("profile", "default", new)
        return rev

    @app.patch("/api/profile")
    async def edit_profile(body: ProfileInput):
        return update_profile(body.model_dump(), "用户显式编辑")

    @app.post("/api/profile/feedback")
    async def feedback(body: FeedbackInput):
        if not db.get("game", body.game_id):
            raise HTTPException(404, "游戏不存在")
        p = db.get("profile", "default")
        feedbacks = (p.get("feedback", []) + [body.model_dump() | {"created_at": now()}])[-200:]
        constraints = copy.deepcopy(p["constraints"])
        if body.reaction == "played":
            constraints["played"] = list(dict.fromkeys(constraints["played"] + [body.game_id]))
        rev = update_profile({"feedback": feedbacks, "constraints": constraints}, "用户显式反馈")
        inferred = extract_preferences(body.reason)
        if inferred:
            prefs = {x["feature_key"]: x for x in p["preferences"]}
            prefs.update({x["feature_key"]: x for x in inferred})
            rev["pending_proposal"] = update_profile(
                {"preferences": list(prefs.values())}, "根据反馈原因推断，需要你确认", "pending"
            )
        return rev

    @app.post("/api/profile/revisions/{rid}/confirm")
    async def confirm_revision(rid: str):
        r = db.get("revision", rid)
        if not r or r["status"] != "pending":
            raise HTTPException(409, "没有待确认的偏好变更")
        current = db.get("profile", "default")
        if current["version"] != r["before"]["version"]:
            raise HTTPException(409, "档案已经变化，请重新编辑偏好")
        db.put("profile", "default", r["after"])
        r["status"] = "confirmed"
        db.put("revision", rid, r)
        return r

    @app.post("/api/profile/revisions/{rid}/revert")
    async def revert_revision(rid: str):
        r = db.get("revision", rid)
        if not r:
            raise HTTPException(404, "版本不存在")
        current = db.get("profile", "default")
        if current["version"] != r["after"]["version"] or r["status"] == "pending":
            raise HTTPException(409, "只能撤销最新已应用版本，避免覆盖后续修改")
        return update_profile({k: v for k, v in r["before"].items() if k != "version"}, "用户撤销 " + rid, replace=True)

    @app.get("/api/shortlist")
    async def interest_games():
        return {"items": shortlist.list(), "external_requests": 0, "model_calls": 0}

    @app.post("/api/shortlist", status_code=201)
    async def add_interest_game(body: ShortlistInput):
        return shortlist.add(body)

    @app.delete("/api/shortlist/{wid}")
    async def remove_interest_game(wid: str):
        shortlist.remove(wid)
        return {"deleted": True}

    @app.get("/api/tools/health")
    async def tools_health():
        return {"items": registry.specs()}

    @app.post("/api/tools/{name}/check")
    async def check_tool(name: str):
        adapter = next((a for a in registry.adapters if a.name == name), None)
        if not adapter:
            raise HTTPException(404, "工具不存在")
        cap = adapter.capabilities[0]
        args = {}
        if cap not in ["search_games"]:
            games = [g for g in db.list("game") if g["dataset_id"] == "live" and g.get("external_ids", {}).get("steam")]
            if not games:
                raise HTTPException(409, "先搜索并选择一款带 Steam ID 的真实游戏，再探测该工具")
            args = {"game_id": games[0]["id"]}
        return (
            await registry.call({"tool_name": cap, "arguments": args}, Gate(settings), force=True, provider=name)
        ).model_dump()

    dist = ROOT / "frontend/dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    async def home():
        if not (dist / "index.html").exists():
            return JSONResponse(
                {
                    "message": "前端尚未构建。运行 cd frontend && npm ci && npm run build。开发模式访问 http://localhost:5173。"
                }
            )
        return FileResponse(dist / "index.html")

    return app


app = create_app()
