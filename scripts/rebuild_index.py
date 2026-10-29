"""Local maintenance: indexes are disposable; original quotes are preserved."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import Settings
from app.db import DB, Document
from app.retrieval import Retrieval

parser = argparse.ArgumentParser(description="重建本地索引，或撤销指定来源的当前使用权限。")
group = parser.add_mutually_exclusive_group(required=True)
group.add_argument("--rebuild", action="store_true")
group.add_argument("--revoke", metavar="DOCUMENT_ID")
args = parser.parse_args()
db = DB(Settings().resolved_db_url())
db.create()
r = Retrieval(db)
if args.revoke:
    with db.Session() as session:
        if not session.get(Document, args.revoke):
            raise SystemExit("来源不存在；未执行撤销。")
    r.rebuild()
    r.revoke(args.revoke)
    print("来源已停止参与检索与当前画像；原始历史引用保留。")
else:
    r.rebuild()
    print("本地全文与兴趣历史索引已重建；未发起外部请求或模型调用。")
