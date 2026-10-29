from fastapi.testclient import TestClient
from app.main import create_app


def test_api_security_validation_exports_profile(stack):
    s, db, _, _ = stack
    app = create_app(s, db, False)
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        public = c.get("/api/settings/public").json()
        assert not any("key" in k or "secret" in k for k in public)
        assert (
            c.post("/api/research", json={"query": "test"}, headers={"Origin": "https://evil.example"}).status_code
            == 403
        )
        assert c.get("/healthz", headers={"Host": "evil.example"}).status_code == 400
        assert c.post("/api/research", json={"query": "test", "llm_base_url": "http://evil"}).status_code == 422
        tid = c.post("/api/research", json={"query": "策略"}).json()["task_id"]
        assert c.post(f"/api/research/{tid}/pause").json()["status"] == "paused"
        assert c.post(f"/api/research/{tid}/resume").json()["status"] == "queued"
        assert c.post(f"/api/research/{tid}/cancel").json()["status"] == "cancelled"
        events = c.get(f"/api/research/{tid}/events").text
        assert "id:" in events and "task_cancelled" in events
        assert c.get(f"/api/research/{tid}/report").status_code == 404
        assert c.post(f"/api/research/{tid}/resume").status_code == 409
        old = c.get("/api/profile").json()
        rev = c.patch("/api/profile", json={"preferences": [{"feature_key": "parry", "target": True}]}).json()
        assert c.get("/api/profile").json()["version"] == old["version"] + 1
        assert c.post("/api/profile/revisions/" + rev["id"] + "/revert").status_code == 200
        feedback = c.post(
            "/api/profile/feedback", json={"game_id": "fx-01", "reaction": "like", "reason": "喜欢策略和构筑"}
        ).json()
        assert feedback["pending_proposal"]["status"] == "pending"
        assert c.post("/api/profile/revisions/" + feedback["pending_proposal"]["id"] + "/confirm").status_code == 200
