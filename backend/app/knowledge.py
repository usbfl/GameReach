import hashlib
import json
from sqlalchemy import select
from .db import Document, Span, Claim, now
from .retrieval import index_document, usable


class Knowledge:
    def __init__(self, db):
        self.db = db

    def ingest(self, documents):
        ids = []
        with self.db.Session.begin() as s:
            for original in documents:
                d = dict(original)
                text = d.get("text", "")
                h = hashlib.sha256(text.encode()).hexdigest()
                # Immutable source versions: an update can never rewrite a previous quote.
                identity = hashlib.sha256(
                    json.dumps(
                        {
                            k: d.get(k)
                            for k in [
                                "scope",
                                "visibility",
                                "owner_id",
                                "published_at",
                                "updated_at",
                                "kind",
                                "provenance_type",
                            ]
                        },
                        sort_keys=True,
                    ).encode()
                ).hexdigest()[:12]
                did = f"{d['dataset_id']}:{d['game_id']}:{d['provider']}:{d['external_id']}:{h[:12]}:{identity}"
                d |= {"id": did, "fetched_at": now(), "content_hash": h}
                if not s.get(Document, did):
                    s.add(Document(id=did, game_id=d["game_id"], dataset_id=d["dataset_id"], content_hash=h, data=d))
                    s.flush()
                index_document(s, s.get(Document, did).data)
                ids.append(did)
        return list(dict.fromkeys(ids))

    def documents(self, ids):
        with self.db.Session() as s:
            return [s.get(Document, i).data for i in dict.fromkeys(ids) if s.get(Document, i)]

    def extract(self, task_id, findings, allowed_docs, dataset, windows=None):
        accepted, rejected = [], []
        with self.db.Session.begin() as s:
            for f in findings:
                f = f.model_dump() if hasattr(f, "model_dump") else f
                d = s.get(Document, f["document_id"])
                if not d or d.id not in allowed_docs or d.game_id != f["game_id"] or d.dataset_id != dataset:
                    rejected.append("对象、数据集或来源不匹配")
                    continue
                if not usable(s, d.id, dataset):
                    rejected.append("来源已失效、过期或不可访问")
                    continue
                document_scope = d.data.get("scope", {})
                # The ontology's unscoped baseline already means main/default play.
                # LLM-created names for that baseline must not hide every assessment.
                f = dict(f)
                if f.get("feature_key") and f.get("value") is not None:
                    # Verify exactly the property we will publish. The model often adds
                    # unrelated rule details to its prose, which obscures a valid type
                    # classification. Values still require independent semantic checks.
                    from .dna.ontology import FEATURES, describe

                    f["claim_text"] = (
                        FEATURES[f["feature_key"]]["label"] + "：" + describe(f["feature_key"], f["value"]) + "。"
                    )
                f["scope"] = {
                    k: v
                    for k, v in f.get("scope", {}).items()
                    if k in document_scope
                    or not (
                        k == "mode"
                        and v.lower()
                        in {"main", "main_game", "standard", "default", "主流程", "标准模式", "默认模式", "标准爬塔"}
                    )
                }
                finding_scope = f.get("scope", {})
                if (
                    dataset == "live"
                    and f.get("aspect") in {"tag", "strength", "tradeoff"}
                    and d.data.get("kind") != "reviews"
                ):
                    rejected.append("玩家体验必须引用玩家评论，不能将商店宣传写成玩家评价")
                    continue
                if any(finding_scope.get(k) != v for k, v in document_scope.items()):
                    rejected.append("引用作用域不匹配；限定平台、难度、模式或版本的资料不能泛化")
                    continue
                start = -1
                ranges = (
                    [(0, len(d.data["text"]))]
                    if windows is None
                    else [(w.get("start", 0), w.get("end", len(d.data["text"]))) for w in windows if w["id"] == d.id]
                )
                for left, right in ranges:
                    found = d.data["text"].find(f["quote"], left, right)
                    if found >= 0 and f["quote"].strip():
                        start = found
                        break
                if start < 0:
                    rejected.append("引用片段无法定位")
                    continue
                h = hashlib.sha256((d.id + str(start) + f["quote"]).encode()).hexdigest()[:20]
                eid = "ev-" + h
                if not s.get(Span, eid):
                    s.add(Span(id=eid, document_id=d.id, start=start, end=start + len(f["quote"]), quote=f["quote"]))
                cid = (
                    "cl-"
                    + hashlib.sha256(
                        (task_id + f["game_id"] + str(f.get("feature_key")) + f["claim_text"] + h).encode()
                    ).hexdigest()[:20]
                )
                data = {
                    **f,
                    "id": cid,
                    "evidence_ids": [eid],
                    "evidence_status": "pending",
                    "schema_version": 2,
                    "assessed_at": now(),
                    "support": [eid],
                    "oppose": [],
                    "provenance_type": d.data["provenance_type"],
                    "applicable_at": d.data.get("published_at"),
                }
                if not s.get(Claim, cid):
                    s.add(Claim(id=cid, task_id=task_id, game_id=f["game_id"], data=data))
                accepted.append(cid)
        return accepted, rejected

    def claims(self, task_id):
        with self.db.Session() as s:
            return [c.data for c in s.scalars(select(Claim).where(Claim.task_id == task_id).order_by(Claim.id))]

    def verify(self, task_id, rejected_ids=(), verifier_enabled=True, live_verified=False, verdicts=None):
        # live_verified is retained only for old callers; it never grants blanket approval.
        claims = self.claims(task_id)
        decisions = {
            v["claim_id"]: v for v in [x.model_dump() if hasattr(x, "model_dump") else x for x in (verdicts or [])]
        }
        for c in claims:
            v = decisions.get(c["id"])
            valid = (
                v
                and bool(v.get("reason"))
                and bool(v.get("evidence_ids"))
                and set(c["evidence_ids"]).issubset(v["evidence_ids"])
                and set(v["evidence_ids"]).issubset(c["evidence_ids"] + c.get("oppose", []))
            )
            c["evidence_status"] = (
                "rejected"
                if c["id"] in rejected_ids
                else v["verdict"]
                if valid and verifier_enabled
                else "insufficient"
            )
            c["verification"] = {
                "version": "verdict-2",
                "verdict": c["evidence_status"],
                "reason": v.get("reason") if valid else "缺少逐项语义核验，不能因未驳回而通过。",
                "evidence_ids": v.get("evidence_ids", []) if valid else [],
            }
            with self.db.Session() as session:
                if not usable(session, c["document_id"]):
                    c["evidence_status"] = "insufficient"
                    c["verification"].update(verdict="insufficient", reason="来源已失效、过期或不可访问，需要补查。")
        groups = {}
        for c in claims:
            if c.get("feature_key") and c["evidence_status"] == "supported":
                groups.setdefault(
                    (c["game_id"], c["feature_key"], json.dumps(c.get("scope", {}), sort_keys=True)), []
                ).append(c)
        for group in groups.values():
            if len({json.dumps(c.get("value"), sort_keys=True) for c in group}) > 1:
                for c in group:
                    c["evidence_status"] = "disputed"
                    c["oppose"] = list(
                        {
                            eid
                            for other in group
                            if other.get("value") != c.get("value")
                            for eid in other["evidence_ids"]
                        }
                    )
        with self.db.Session.begin() as session:
            for c in claims:
                session.get(Claim, c["id"]).data = c
        from .dna.projection import persist_assessments

        persist_assessments(self.db, claims, self.documents([c["document_id"] for c in claims]))
        return claims

    def evidence(self, eid):
        with self.db.Session() as s:
            span = s.get(Span, eid)
            if not span:
                return None
            d = s.get(Document, span.document_id)
            return {
                "id": eid,
                "quote": span.quote,
                "start": span.start,
                "end": span.end,
                "document": d.data,
                "claims": [
                    c.data
                    for c in s.scalars(select(Claim))
                    if eid in c.data.get("evidence_ids", []) or eid in c.data.get("oppose", [])
                ],
            }
