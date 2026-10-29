"""A local interest shortlist. Adding or viewing a game never fetches sources."""

from sqlalchemy import select, text
from .db import Watch, now, uid


class ShortlistService:
    def __init__(self, settings, db):
        self.db = db
        self.dataset = "synthetic-v1" if settings.data_mode == "fixtures" else "live"

    def list(self):
        with self.db.Session() as session:
            return [
                w.data
                for w in session.scalars(select(Watch))
                if w.data.get("game", {}).get("dataset_id") == self.dataset and w.data.get("enabled", True)
            ]

    def add(self, body):
        game = self.db.get("game", body.game_id)
        if not game or game["dataset_id"] != self.dataset:
            raise ValueError("游戏未收录或不在当前数据模式")
        with self.db.Session.begin() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            for row in session.scalars(select(Watch)):
                if row.data["game_id"] == body.game_id and row.data.get("game", {}).get("dataset_id") == self.dataset:
                    row.data = row.data | {"enabled": True, "reason": body.reason, "game": game}
                    return row.data
            data = {
                "id": uid("interest-game-"),
                "game_id": body.game_id,
                "game": game,
                "reason": body.reason,
                "enabled": True,
                "created_at": now(),
            }
            session.add(Watch(id=data["id"], data=data))
            return data

    def remove(self, wid):
        with self.db.Session.begin() as session:
            row = session.get(Watch, wid)
            if not row or row.data.get("game", {}).get("dataset_id") != self.dataset:
                raise ValueError("兴趣清单中没有这款游戏")
            session.delete(row)
