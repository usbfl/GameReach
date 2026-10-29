"""v0.11 acceptance: synthetic evidence only, actual API, persistence and ranking paths."""
import copy
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.db import Report, DocumentIndex
from app.main import create_app
from app.schemas import ResearchRequest, ConfirmedConditions
from app.interests import game_sources, rule_summary, summarize, collect_sources
from app.interest_features import attention, profile
from app.tags.intent import demo_interpret, confirmed_interpretation
from app.tags.ranking import rank
from app.tags.strategy import evidence_gap
from app.reuse import batch_key
from conftest import finish


def sources():
    tags = [['build_variety', 'boss_practice'], ['build_variety'], ['build_variety'], ['boss_practice'], ['boss_practice']]
    return [dict(id=f's-{n}', kind='library', label=f'合成资料{n}', text='用户导入', game_key=str(n), game_name=f'游戏{chr(65+n)}', tags=t, playtime_minutes=None) for n, t in enumerate(tags)]


def intent(tag='build_variety', **kw):
    return dict(tag_id=tag, polarity='want', priority=3, source_id='current', source_quote='用户确认', scope={}, explicit_redline=False) | kw


async def snapshot(stack):
    _, db, _, engine = stack
    tid = engine.create(ResearchRequest(recommendation_mode='player_tags', query='构筑', game_ids=['fx-02'], recommendation_count=1))
    await finish(engine, tid)
    with db.Session() as session:
        r = copy.deepcopy(session.scalar(select(Report).where(Report.task_id == tid)).data)
    ctx = copy.deepcopy(db.task(tid)['context'])
    return tid, r, ctx


def rows(stack, report, ctx):
    snap = report['snapshot']
    return rank(stack[1], snap['games'], snap['claims'], snap['documents'], ctx)


def test_overlapping_themes_unique_members_no_double_profile_votes():
    src = sources()
    src.append(dict(src[0], id='duplicate', kind='watch', watch=True))
    cards = rule_summary(src).cards
    assert len(cards) == 2
    assert set(cards[0].game_keys) & set(cards[1].game_keys) == {'0'}
    assert all(len(c.game_keys) == len(set(c.game_keys)) == 3 for c in cards)
    assert {tuple(c.theme_ids) for c in cards} == {('build_variety',), ('boss_practice',)}
    assert all(len(row['game_keys']) == 3 for row in profile(game_sources(src)))


def test_synonyms_and_identical_broad_groups_do_not_duplicate():
    src = sources()[:3]
    for s in src:
        s['tags'] = ['build_variety', '构筑', '构筑改变打法', 'Generic One', 'Generic Two']
    cards = rule_summary(src).cards
    assert len(cards) == 1
    assert cards[0].theme_ids == ['build_variety']


@pytest.mark.parametrize('reaction', ['dislike', 'exclude'])
def test_negative_overrides_hours_favorite_and_watch(reaction):
    src = sources()
    src[0] |= {'reaction': reaction, 'playtime_minutes': 60_000, 'favorite': True}
    src.append(dict(src[0], id='watch', reaction='neutral', watch=True))
    assert '0' not in game_sources(src)
    assert attention(src[0])['weight'] == 0
    assert not rule_summary(src).cards  # Two members per theme must not be padded.


def test_bounded_hours_missing_time_and_semantic_tiers():
    assert attention({})['weight'] == 1
    assert attention({'playtime_minutes': 3000}) == attention({'playtime_minutes': 9999999})
    assert attention({'playtime_minutes': 9999999})['weight'] < attention({'reaction': 'like'})['weight']
    assert attention({'reaction': 'like', 'favorite': True}) == attention({'reaction': 'like'})
    assert len(rule_summary(sources()).cards) == 2


def test_dna_clues_do_not_invent_more_specific_mechanics(monkeypatch):
    from app.interest_features import verified_themes
    feature = dict(evidence_status='supported', freshness='fresh', value=4)
    monkeypatch.setattr('app.dna.projection.current_features', lambda *args: {
        'build_diversity': feature, 'mechanical_difficulty': feature, 'strategic_depth': feature,
    })
    # Difficulty need not mean pattern reading; strategy need not mean turn-based play.
    assert [t['id'] for t in verified_themes(None, 'synthetic-v1', 'game')] == ['build_variety']


@pytest.mark.asyncio
async def test_overlap_cache_retains_other_theme_and_reuses_members(stack):
    s, db, registry, _ = stack
    src = sources()
    initial = await summarize(s, registry.http, src, db=db)
    same = await summarize(s, registry.http, src, db=db)
    assert same['cache'] == {'hit': True, 'reused_cards': 2, 'processed_games': 0}
    src[1]['text'] = '仅改变构筑主题成员B的资料'
    changed = await summarize(s, registry.http, src, db=db)
    assert changed['cache']['reused_cards'] == 1 and len(changed['cards']) == 2
    assert next(c for c in changed['cards'] if c['theme_ids'] == ['boss_practice']) == next(c for c in initial['cards'] if c['theme_ids'] == ['boss_practice'])
    assert all('0' in c['game_keys'] for c in changed['cards'])
    assert changed['budget']['external_requests'] == changed['budget']['llm_calls'] == 0


@pytest.mark.asyncio
async def test_configurable_limits_and_expired_cache(stack):
    s, db, registry, _ = stack
    s.interest_min_games = 2
    s.interest_max_cards = 1
    first = await summarize(s, registry.http, sources()[:2], db=db)
    assert len(first['cards']) == 1 and len(first['cards'][0]['game_keys']) == 2
    s.interest_min_games = 3
    second = await summarize(s, registry.http, sources()[:2], db=db)
    assert not second['cache']['hit'] and not second['cards']


def test_old_card_edit_new_members_and_confirmed_text_preserved(stack):
    s, db, _, _ = stack
    old = dict(id='old-v0102', title='我的原卡片', text='这一段已经由用户确认，不应该自动改写。', status='confirmed', version=4, dataset_id='synthetic-v1', created_at='2026-01-01', sources=[])
    db.put('interest_card', old['id'], old)
    games = [dict(platform='fixture', game_id=f'fx-{i:02}', name=db.get('game', f'fx-{i:02}')['name']) for i in [1, 2, 4, 6]]
    with TestClient(create_app(s, db, False)) as c:
        assert c.post('/api/player/import/file', json={'format': 'json', 'content': json.dumps({'games': games})}).status_code == 200
        assert c.post('/api/interests/generate', json={}).status_code == 200
        assert db.get('interest_card', old['id']) == old
        body = dict(version=4, title='编辑旧卡片', text=old['text'], game_keys=['fx-01', 'fx-02', 'fx-04'])
        result = c.patch('/api/interests/'+old['id'], json=body)
        assert result.status_code == 200 and result.json()['version'] == 5
        result = c.patch('/api/interests/'+old['id'], json=body | {'version': 5, 'game_keys': ['fx-01', 'fx-02', 'fx-06']})
        assert result.status_code == 200 and [g['key'] for g in result.json()['related_games']] == ['fx-01', 'fx-02', 'fx-06']
        assert c.patch('/api/interests/'+old['id'], json=body).status_code == 409


def test_parser_soft_negative_hard_and_unmapped():
    parsed = demo_interpret('我想找构筑丰富、战斗操作有深度的游戏，不太喜欢大量刷装备，想用嗅觉识别藏宝地点', [])
    by = {t.tag_id: t for t in parsed.intents}
    assert by['build_variety'].polarity == 'want' and by['build_variety'].priority == 3
    assert by['resource_grind'].polarity == 'avoid' and not by['resource_grind'].explicit_redline
    assert by['read_patterns'].polarity == 'want'
    assert any('嗅觉' in x for x in parsed.unmapped)
    strict = demo_interpret('绝对不能要求刷装备才能正常推进', [])
    assert strict.intents[0].explicit_redline and strict.intents[0].polarity == 'avoid'
    novel = demo_interpret('想用嗅觉识别藏宝地点', [])
    assert not novel.intents and novel.unmapped == ['想用嗅觉识别藏宝地点']


def test_confirmed_platform_must_not_be_erased_by_scope_defaults(stack):
    _, db, _, e = stack
    tid = e.create(ResearchRequest(recommendation_mode='player_tags', query='原输入',
        confirmed_conditions={'intents': [intent()], 'constraints': {'platform': 'PC'}}))
    assert db.task(tid)['context']['constraints']['platform'] == 'PC'


@pytest.mark.asyncio
async def test_grind_evidence_soft_conflict_and_hard_exclusion(stack):
    _, db, _, e = stack
    tid = e.create(ResearchRequest(recommendation_mode='player_tags', query='构筑丰富，不喜欢刷装备', game_ids=['fx-09'], recommendation_count=1))
    await finish(e, tid)
    with db.Session() as session:
        report = copy.deepcopy(session.scalar(select(Report).where(Report.task_id == tid)).data)
    ctx = copy.deepcopy(db.task(tid)['context'])
    soft = rows(stack, report, ctx)[0]
    assert soft['group'] == 'tradeoffs'
    assert next(t for t in soft['tags'] if t['tag_id'] == 'resource_grind')['oppose_count'] == 2
    neutral = copy.deepcopy(ctx)
    neutral['interpretation']['intents'] = [i for i in neutral['interpretation']['intents'] if i['tag_id'] != 'resource_grind']
    assert soft['rank_score'] < rows(stack, report, neutral)[0]['rank_score'] - 20
    for i in ctx['interpretation']['intents']:
        if i['tag_id'] == 'resource_grind': i['explicit_redline'] = True
    assert rows(stack, report, ctx)[0]['group'] == 'excluded'


def test_final_edit_replaces_query_and_cards_even_when_empty():
    cards = [{'id': 'one', 'text': '喜欢构筑和刷装备'}, {'id': 'two', 'text': '喜欢构筑和刷装备'}]
    parsed = demo_interpret('不喜欢刷装备', cards)
    assert len([t for t in parsed.intents if t.tag_id == 'build_variety']) == 1
    assert next(t for t in parsed.intents if t.tag_id == 'resource_grind').polarity == 'avoid'
    final = confirmed_interpretation(ConfirmedConditions(intents=[intent(polarity='avoid')]))
    assert final.intents[0].confirmed_by_user and final.intents[0].polarity == 'avoid'
    assert not confirmed_interpretation(ConfirmedConditions()).intents


def test_preview_confirm_snapshot_and_stale_preview(stack):
    s, db, _, e = stack
    with TestClient(create_app(s, db, False)) as c:
        p = c.post('/api/tag-intent-preview', json={'query': '必须支持中文，构筑丰富'}).json()
        assert p['constraints']['language'] == 'zh-CN' and p['budget']['tool_calls'] == 0
        conditions = {'intents': [intent('quick_retry', polarity='avoid')], 'constraints': p['constraints'], 'custom': []}
        r = c.post('/api/tag-research', json={'query': '必须支持中文，构筑丰富', 'confirmed_conditions': conditions, 'intent_preview_id': p['id']})
        assert r.status_code == 202
        task = db.task(r.json()['task_id'])
        assert task['request']['confirmed_conditions']['intents'][0]['tag_id'] == 'quick_retry'
        assert c.post('/api/tag-research', json={'query': '不同输入', 'confirmed_conditions': conditions, 'intent_preview_id': p['id']}).status_code == 422


@pytest.mark.asyncio
async def test_final_manual_constraints_do_not_restore_removed_language(stack):
    _, db, _, e = stack
    req = ResearchRequest(recommendation_mode='player_tags', query='必须支持中文，构筑', game_ids=['fx-02'], recommendation_count=1,
        confirmed_conditions={'intents': [intent()], 'constraints': {'language': None}})
    tid = e.create(req)
    await e.step(tid)
    assert db.task(tid)['context']['constraints']['language'] is None
    assert db.task(tid)['context']['interpretation']['intents'][0]['confirmed_by_user']
    assert db.task(tid)['context']['budget']['llm_calls'] == 0


@pytest.mark.asyncio
async def test_unrelated_twenty_unknowns_do_not_change_score_or_investigation(stack):
    _, r, ctx = await snapshot(stack)
    before = rows(stack, r, ctx)[0]
    r['snapshot']['games'][0]['unrelated_mechanisms'] = {f'unknown-{i}': None for i in range(20)}
    after = rows(stack, r, ctx)[0]
    assert after['rank_score'] == before['rank_score'] and after['group'] == 'recommended'
    assert not evidence_gap(after, r['snapshot']['documents'], set(ctx['full_reviewed_document_ids']))['needed']


@pytest.mark.asyncio
async def test_soft_unknown_neutral_partial_and_minor_evidence_not_high(stack):
    _, r, ctx = await snapshot(stack)
    ctx['interpretation']['intents'] = [intent()]
    before = rows(stack, r, ctx)[0]
    ctx['interpretation']['intents'].append(intent('quick_retry', priority=1))
    after = rows(stack, r, ctx)[0]
    assert after['rank_score'] == before['rank_score']
    assert after['group'] == 'recommended' and after['partial_match']
    assert after['tags'][1]['match'] is None and after['tags'][1]['support'] == []
    assert after['focus_diagnostics']['unknown_dimensions'] == 1
    ctx['interpretation']['intents'][0]['priority'] = 1
    minor = rows(stack, r, ctx)[0]
    assert minor['rank_score'] < before['rank_score'] and minor['rank_score'] < 75


@pytest.mark.asyncio
@pytest.mark.parametrize('languages,expected', [(None, 'unknown'), (['en'], 'fail'), (['zh-CN'], 'pass')])
async def test_hard_language_three_states(stack, languages, expected):
    _, r, ctx = await snapshot(stack)
    ctx['constraints']['language'] = 'zh-CN'
    r['snapshot']['games'][0]['languages'] = languages
    row = rows(stack, r, ctx)[0]
    assert row['hard_constraint_status'] == expected
    assert row['group'] == {'unknown': 'provisional', 'fail': 'excluded', 'pass': 'recommended'}[expected]


@pytest.mark.asyncio
async def test_explicit_negative_reduces_rank_and_hard_excludes(stack):
    _, r, ctx = await snapshot(stack)
    ctx['interpretation']['intents'] = [intent()]
    positive = rows(stack, r, ctx)[0]
    ctx['interpretation']['intents'][0]['polarity'] = 'avoid'
    negative = rows(stack, r, ctx)[0]
    assert negative['rank_score'] < positive['rank_score'] - 30
    ctx['interpretation']['intents'][0]['explicit_redline'] = True
    assert rows(stack, r, ctx)[0]['group'] == 'excluded'


@pytest.mark.asyncio
async def test_unmapped_hard_requirement_never_passes_and_dictionary_unchanged(stack):
    from app.tags.catalog import VOCABULARY
    _, r, ctx = await snapshot(stack)
    before = copy.deepcopy(VOCABULARY)
    ctx['custom_requirements'] = [{'text': '嗅觉解谜', 'explicit_redline': True, 'status': 'confirmed'}]
    row = rows(stack, r, ctx)[0]
    assert row['group'] == 'provisional' and row['hard_constraint_status'] == 'unknown'
    assert VOCABULARY == before


@pytest.mark.asyncio
async def test_evidence_sources_and_local_rerank_no_external_calls(stack):
    tid, r, ctx = await snapshot(stack)
    s, db, registry, _ = stack
    with TestClient(create_app(s, db, False)) as c:
        for row in r['results']:
            for claim in row['card']['strengths']:
                assert claim['evidence_status'] == 'supported'
                for eid in claim['evidence_ids']:
                    assert c.get('/api/evidence/'+eid).status_code == 200
        result = c.post(f'/api/tag-research/{tid}/rerank', json={'priorities': {'build_variety': 1}})
        assert result.status_code == 200
        assert db.task(tid)['context']['budget'] == ctx['budget']
    with db.Session.begin() as session:
        for d in r['snapshot']['documents']:
            session.get(DocumentIndex, d['id']).revoked = 1
    assert rows(stack, r, ctx)[0]['rank_score'] is None


@pytest.mark.asyncio
async def test_weight_only_reuses_but_new_focus_and_hard_constraint_invalidates(stack):
    s, db, _, e = stack
    _, r, ctx = await snapshot(stack)
    docs = r['snapshot']['documents']
    before = batch_key(s, ctx, docs)
    ctx['interpretation']['intents'][0]['priority'] = 1
    assert batch_key(s, ctx, docs) == before
    ctx['constraints']['language'] = 'zh-CN'
    assert batch_key(s, ctx, docs) != before
    ctx['constraints']['language'] = None
    ctx['interpretation']['intents'].append(intent('quick_retry'))
    assert batch_key(s, ctx, docs) != before
    tid = e.create(ResearchRequest(recommendation_mode='player_tags', query='构筑', game_ids=['fx-02'], recommendation_count=1,
        confirmed_conditions={'intents': [intent(priority=1, source_quote='构筑')], 'constraints': {'platform': ''}}))
    await finish(e, tid)
    assert db.task(tid)['context']['savings']['verified_batches_reused'] == 1


def test_import_exclusion_blocks_watch_and_profile_without_comments(stack):
    s, db, _, _ = stack
    with TestClient(create_app(s, db, False)) as c:
        c.post('/api/shortlist', json={'game_id': 'fx-02'})
        c.post('/api/player/import/file', json={'format': 'json', 'content': json.dumps({'games': [dict(platform='fixture', game_id='fx-02', name='星图议会', reaction='exclude', playtime_minutes=999999)]})})
        assert 'fx-02' not in game_sources(collect_sources(db, 'synthetic-v1'))


def test_draft_confirm_allows_overlap_and_deduplicates_members(stack):
    s, db, _, _ = stack
    src = sources()
    games = []
    for n, row in enumerate(src, 1):
        gid = f'fx-{n:02}'
        g = db.get('game', gid)
        db.put('game', gid, g | {'tags': row['tags']})
        games.append(dict(platform='fixture', game_id=gid, name=g['name']))
    with TestClient(create_app(s, db, False)) as c:
        for n, row in enumerate(src, 1):
            gid = f'fx-{n:02}'
            db.put('game', gid, db.get('game', gid) | {'tags': row['tags']})
        imp = c.post('/api/player/import/file', json={'format': 'json', 'content': json.dumps({'games': games})}).json()
        draft = c.post('/api/player/imports/'+imp['id']+'/analyze', json={'selected_ids': [f'entry-{i}' for i in range(5)]}).json()
        cards = draft['interest_summary']['cards']
        assert len(cards) == 2
        result = c.post('/api/player/drafts/'+draft['id']+'/confirm', json={'interest_cards': [{k: v for k, v in x.items() if k in {'title', 'text', 'source_ids', 'game_keys', 'theme_ids'}} for x in cards]})
        assert result.status_code == 200
        stored = c.get('/api/interests').json()['items']
        assert len(stored) == 2 and all(len(c['related_games']) == 3 for c in stored)
        assert set(stored[0]['game_keys']) & set(stored[1]['game_keys']) == {'fx-01'}
