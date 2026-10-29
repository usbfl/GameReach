#!/usr/bin/env python3
"""Reproducible offline cases and v0.10.2/v0.11 rule comparison. No paid/live calls."""
import asyncio
import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.config import Settings
from app.db import DB, Report
from app.main import create_app
from app.schemas import ResearchRequest
from app.tools import Registry
from app.engine import Engine
from app.interests import collect_sources, rule_summary
from app.tags.ranking import rank
from app.tags.strategy import evidence_gap
from upgrade_v011 import copy_database


def legacy(name, namespace):
    spec = importlib.util.spec_from_file_location(namespace, ROOT / 'evaluations' / 'legacy_0102' / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def finish(engine, tid):
    for _ in range(180):
        t = engine.db.task(tid)
        if t['status'] in {'completed', 'partial', 'failed', 'cancelled'}:
            assert t['status'] != 'failed', t
            return
        await engine.step(tid)
    raise AssertionError('Task failed to reach a terminal state')


def stored_report(db, tid):
    with db.Session() as s:
        return copy.deepcopy(s.scalar(select(Report).where(Report.task_id == tid)).data)


def intent(tag, priority=3, **kwargs):
    return dict(tag_id=tag, polarity='want', priority=priority, source_id='current', source_quote='合成实验最终条件', scope={}, explicit_redline=False) | kwargs


async def run():
    old_rank = legacy('ranking.py', 'app.tags.legacy_rank').rank
    old_group = legacy('interests.py', 'app.legacy_interests').rule_summary
    checks = []

    def check(name, condition):
        checks.append({'name': name, 'passed': bool(condition)})
        assert condition, name

    with tempfile.TemporaryDirectory(prefix='gamereach-v011-') as folder:
        s = Settings(_env_file=None, data_mode='fixtures', llm_mode='mock', database_url='sqlite:///'+folder+'/demo.db')
        db = DB(s.resolved_db_url())
        app = create_app(s, db, False)
        with TestClient(app) as client:
            data = (ROOT/'data/fixtures/v011_player_library.json').read_text(encoding='utf-8')
            imported = client.post('/api/player/import/file', json={'format':'json','content':data})
            check('导入固定演示数据', imported.status_code == 200)
            generated = client.post('/api/interests/generate', json={}).json()
            cards = generated['cards']
            check('两个不同主题卡片', len(cards) == 2)
            overlap = set(cards[0]['game_keys']) & set(cards[1]['game_keys'])
            check('A跨主题共享', len(overlap) == 1 and any('游戏a' in k for k in overlap))
            check('高时长排除F不重返正向画像', all(not any('游戏f' in k for k in c['game_keys']) for c in cards))
            repeated = client.post('/api/interests/generate', json={}).json()
            check('重复整理复用两张卡片', repeated['cache']['hit'] and repeated['cache']['reused_cards'] == 2)
            src = collect_sources(db, 'synthetic-v1')
            old_cards, new_cards = old_group(src).cards, rule_summary(src).cards
            case1 = {'cards':cards, 'unassigned_games':generated['unassigned_games'], 'cache':repeated['cache'], 'budget':generated['budget'],
                     'comparison':{'old_theme_cards':len(old_cards),'new_theme_cards':len(new_cards),
                         'old_game_theme_memberships':sum(len(c.game_keys) for c in old_cards), 'new_game_theme_memberships':sum(len(c.game_keys) for c in new_cards)}}

        registry = Registry(s, db)
        engine = Engine(s, db, registry)
        tid = engine.create(ResearchRequest(recommendation_mode='player_tags', query='构筑', game_ids=['fx-02'], recommendation_count=1))
        await finish(engine, tid)
        report = stored_report(db, tid)
        ctx = copy.deepcopy(db.task(tid)['context'])
        snap = report['snapshot']
        ctx['interpretation']['intents'] = [intent('build_variety')]

        def rank_with(fn, context=ctx, snapshot=snap):
            return fn(db, snapshot['games'], snapshot['claims'], snapshot['documents'], context)

        before, old_before = rank_with(rank)[0], rank_with(old_rank)[0]
        snap['games'][0]['unrelated_unknowns'] = {f'mechanism-{i}':None for i in range(20)}
        after, old_after = rank_with(rank)[0], rank_with(old_rank)[0]
        check('20项无关未知不改变得分', before['rank_score'] == after['rank_score'])
        check('不为无关未知追加调查', not evidence_gap(after, snap['documents'], set(ctx['full_reviewed_document_ids']))['needed'])
        ctx['interpretation']['intents'].append(intent('quick_retry', 1))
        partial, old_partial = rank_with(rank)[0], rank_with(old_rank)[0]
        check('普通软偏好未知仍可推荐', partial['group'] == 'recommended' and partial['partial_match'])
        check('普通未知不直接扣分', partial['rank_score'] == before['rank_score'])
        check('未知未虚构成支持', partial['tags'][1]['match'] is None and not partial['tags'][1]['support'])
        case2 = {'baseline_score':before['rank_score'], 'with_20_unrelated_unknowns':after['rank_score'],
                 'partial':partial, 'comparison':{'old_score_before':old_before['rank_score'], 'old_score_irrelevant':old_after['rank_score'],
                     'old_score_soft_unknown':old_partial['rank_score'], 'new_score_soft_unknown':partial['rank_score'],
                     'old_irrelevant_downgrades':int(old_before['rank_score'] != old_after['rank_score']), 'new_irrelevant_downgrades':int(before['rank_score'] != after['rank_score'])}}

        states, old_violations, new_violations = [], 0, 0
        ctx['constraints']['language'] = 'zh-CN'
        for langs, expected in [(None,'unknown'),(['en'],'fail'),(['zh-CN'],'pass')]:
            snap['games'][0]['languages'] = langs
            row, old = rank_with(rank)[0], rank_with(old_rank)[0]
            check('语言硬约束_'+expected, row['hard_constraint_status'] == expected)
            check('非通过不混入推荐_'+expected, expected == 'pass' or row['group'] not in {'recommended','tradeoffs'})
            old_violations += int(expected != 'pass' and old['group'] in {'recommended','tradeoffs'})
            new_violations += int(expected != 'pass' and row['group'] in {'recommended','tradeoffs'})
            states.append({'languages':langs, 'expected':expected, 'actual':row['hard_constraint_status'], 'group':row['group'], 'checks':row['checks']})
        case3 = {'language_states':states,'old_hard_violations':old_violations,'new_hard_violations':new_violations}

        # Same real pipeline, fixed authored fixture corpus: no input answers are supplied to a live model.
        comparison_request = ResearchRequest(recommendation_mode='player_tags', query='构筑丰富，不喜欢刷装备',
            game_ids=['fx-01','fx-02','fx-09'], recommendation_count=3)
        comparison_tid = engine.create(comparison_request)
        await finish(engine, comparison_tid)
        comparison_report = stored_report(db, comparison_tid)
        comparison_ctx = db.task(comparison_tid)['context']
        old_rows = old_rank(db, **{k:comparison_report['snapshot'][k] for k in ['games','claims','documents']}, ctx=comparison_ctx)
        new_rows = comparison_report['results']
        def metrics(items):
            recommended = [r for r in items if r['group'] in {'recommended','tradeoffs'} and r['card']['complete']]
            claims = [c for r in recommended for c in r['card']['strengths']]
            docs = {d['id']:d for d in comparison_report['snapshot']['documents']}
            return {'recommendation_count':len(recommended), 'order':[r['game']['id'] for r in items],
                    'scores':{r['game']['id']:r['rank_score'] for r in items}, 'reason_count':len(claims),
                    'traceable_reason_count':sum(bool(c['evidence_ids']) and c['quote'] in docs[c['document_id']]['text'] for c in claims),
                    'model_calls_in_snapshot_ranking':0, 'tool_calls_in_snapshot_ranking':0}
        comparison = {'old':metrics(old_rows), 'new':metrics(new_rows), 'pipeline_budget':comparison_report['budget'],
                      'note':'同一固定证据快照比较确定性分组和排序；两种排序本身都不调用模型或工具。不是在线模型质量或节省比例实验。'}
        repeat_tid = engine.create(comparison_request)
        await finish(engine, repeat_tid)
        repeat = stored_report(db, repeat_tid)
        check('历史报告快照未改写', stored_report(db, comparison_tid) == comparison_report)
        check('再次研究复用核验结果', repeat['savings'].get('verified_batches_reused',0) > 0)
        backup = copy_database(folder+'/demo.db', folder+'/backup.db')
        restored = copy_database(folder+'/backup.db', folder+'/restored.db')
        check('升级备份和回退各表数量一致', backup['table_counts'] == restored['table_counts'])
        return {'label':'OFFLINE SYNTHETIC RULE EXPERIMENT — NOT LIVE MODEL VALIDATION', 'case1_overlap':case1,
                'case2_focus':case2, 'case3_hard_constraints':case3, 'comparison':comparison,
                'repeat_budget':repeat['budget'], 'reuse':repeat['savings'], 'migration':backup|{'source':'temporary/demo.db','destination':'temporary/backup.db'},
                'assertions':checks, 'passed':len(checks), 'failed':sum(not c['passed'] for c in checks),
                'live_services':'not_run', 'evidence_semantics':'synthetic authored annotations; not a human relevance evaluation'}


if __name__ == '__main__':
    out = Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'docs/validation_v011/offline_experiments.json'
    result = asyncio.run(run())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output':str(out),'passed':result['passed'],'failed':result['failed']}, ensure_ascii=False))
