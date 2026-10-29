"""Budget regressions use authored data only; never call paid providers."""
import copy
import time
import pytest
from sqlalchemy import select
from app.db import Report, Record, DocumentIndex
from app.schemas import ResearchRequest
from app.tools import Gate
from app.tags.strategy import policy, evidence_gap, useful_unread
from app.tags.workflow import batches
from app.reuse import batch_key, get, put
from app.interests import summarize
from conftest import finish


def request(**kwargs):
    return ResearchRequest(recommendation_mode='player_tags', query='构筑', game_ids=['fx-02'], recommendation_count=1, **kwargs)


def report(db, tid):
    with db.Session() as session:
        return session.scalar(select(Report).where(Report.task_id == tid)).data


@pytest.mark.parametrize('name,scan,initial,maximum,batch', [('quick',150,3,3,1),('balanced',500,5,10,3),('thorough',500,5,20,3)])
def test_strategy_caps_respect_settings(stack, name, scan, initial, maximum, batch):
    s, *_ = stack
    r = policy(s, name, 5)
    assert (r['scan_limit'], r['initial_games'], r['investigation_limit'], r['max_batches']) == (scan,initial,maximum,batch)
    s.tag_scan_limit, s.tag_investigation_limit, s.tag_max_batches_per_game = 80, 2, 1
    r = policy(s, name, 10)
    assert r['scan_limit'] == 80 and r['investigation_limit'] == 2 and r['max_batches'] == 1


@pytest.mark.asyncio
async def test_repeated_research_reuses_verified_whole_text_without_altering_old_report(stack):
    s, db, registry, e = stack
    first = e.create(request())
    await finish(e, first)
    before = copy.deepcopy(report(db, first))
    assert db.list('verified_batch_cache')
    second = e.create(request())
    await finish(e, second)
    after = report(db, second)
    assert after['target']['met']
    assert after['recommendation_ids'] == before['recommendation_ids']
    assert after['savings']['verified_batches_reused'] == 1
    assert after['savings']['source_cache_hits'] == 2
    assert all(x['mode'] == 'verified_cache' and x['origin_task_id'] == first for x in after['reading_ledger'])
    assert report(db, first) == before
    assert all(c['evidence_status'] == 'supported' for c in after['snapshot']['claims'])


@pytest.mark.asyncio
async def test_revoked_review_cannot_reenter_recommendations_from_reuse(stack):
    s, db, registry, e = stack
    first = e.create(request())
    await finish(e, first)
    docs = report(db, first)['snapshot']['documents']
    with db.Session.begin() as session:
        for d in docs:
            state = session.get(DocumentIndex, d['id'])
            state.revoked = 1
            state.active = 0
    second = e.create(request())
    await finish(e, second)
    after = report(db, second)
    assert after['savings'].get('verified_batches_reused', 0) == 0
    assert not after['recommendation_ids']


@pytest.mark.asyncio
async def test_cache_expiry_and_changed_intents_force_reanalysis(stack):
    s, db, registry, e = stack
    first = e.create(request())
    await finish(e, first)
    with db.Session.begin() as session:
        for row in session.scalars(select(Record).where(Record.kind == 'verified_batch_cache')):
            row.data = row.data | {'expires': 0}
    second = e.create(request())
    await finish(e, second)
    assert report(db, second)['savings'].get('verified_batches_reused', 0) == 0
    third = e.create(ResearchRequest(recommendation_mode='player_tags', query='战斗技巧', game_ids=['fx-02'], recommendation_count=1))
    await finish(e, third)
    assert report(db, third)['savings'].get('verified_batches_reused', 0) == 0


@pytest.mark.asyncio
async def test_disabled_verification_never_creates_or_uses_evidence_cache(stack):
    s, db, registry, e = stack
    s.enable_verifier = False
    tid = e.create(request())
    await finish(e, tid)
    assert not db.list('verified_batch_cache')
    assert not report(db, tid)['recommendation_ids']


def test_batch_key_includes_scope_text_identity_completeness_and_model(stack):
    s, *_ = stack
    ctx = {'dataset_id':'synthetic-v1','interpretation': {'intents': [{'tag_id':'build_variety','scope':{}}]}}
    docs = [{'id':'d','content_hash':'one','complete':True,'scope':{}}]
    key = batch_key(s, ctx, docs)
    for field, value in [('content_hash','two'),('scope',{'mode':'coop'}),('complete',False),('author_key','a')]:
        assert batch_key(s, ctx, [docs[0] | {field:value}]) != key
    s.llm_model = 'changed'
    assert batch_key(s, ctx, docs) != key


def sources(n):
    return [{'id':str(i),'kind':'library','label':str(i),'game_key':str(i),'game_name':f'Authored {i}', 'text':'收藏','tags':['策略']} for i in range(n)]


@pytest.mark.asyncio
async def test_interest_exact_and_incremental_cache_with_forced_regroup(stack):
    s, db, registry, e = stack
    first = await summarize(s, registry.http, sources(3), db=db)
    exact = await summarize(s, registry.http, sources(3), db=db)
    assert exact['cache'] == {'hit':True,'reused_cards':1,'processed_games':0}
    assert exact['budget']['external_requests'] == 0
    incremental = await summarize(s, registry.http, sources(6), db=db)
    assert incremental['cache']['reused_cards'] == 1
    assert incremental['cache']['processed_games'] == 3
    assert len(incremental['cards']) == 2
    assert incremental['cards'][0] == first['cards'][0]
    regrouped = await summarize(s, registry.http, sources(6), db=db, regroup=True)
    assert regrouped['cache']['reused_cards'] == 0
    assert len(regrouped['cards']) == 1 and len(regrouped['cards'][0]['game_keys']) == 6
    removed = await summarize(s, registry.http, sources(2), db=db)
    assert not removed['cards'] and len(removed['unassigned_games']) == 2


@pytest.mark.asyncio
async def test_interest_metadata_context_changes_invalidate_reuse(stack):
    s, db, registry, e = stack
    await summarize(s, registry.http, sources(3), db=db)
    changed = sources(3)
    changed[0]['text'] = '理由已变更'
    result = await summarize(s, registry.http, changed, db=db)
    assert not result['cache']['hit'] and result['cache']['reused_cards'] == 0
    changed.append({'id':'current','kind':'input','label':'当前','text':'更喜欢安静的节奏'})
    result = await summarize(s, registry.http, changed, db=db)
    assert not result['cache']['hit'] and result['cache']['reused_cards'] == 0


def review(id, text, author=None, stratum='recent'):
    return dict(id=id,text=text,author_key=author,provider='test',game_id='g',kind='reviews',provenance_type='synthetic',complete=True,sampling_stratum=stratum)


def test_whole_text_batches_deduplicate_author_and_body_before_spending():
    docs = [review('a','A full text','same'),review('b','Another full text','same'),review('c','A full text','different'),review('d','Independent full text','other')]
    ids = batches(docs, 4, 16000, [])[0]
    assert len(ids) == 2 and 'd' in ids


def test_gap_logic_does_not_spend_reviews_on_missing_rating_or_secondary_unknowns():
    tag = dict(tag_id='build_variety',priority=3,source_id='current',match=1,support_count=2,oppose_count=0,explicit_redline=False)
    row = {'group':'recommended','supporting_reviews':2,'tags':[tag,dict(tag,tag_id='exploration_discovery',priority=1,match=None,support_count=0)],'card':{'introduction':[{}]}}
    docs = [review('a','One','a','positive_helpful'),review('b','Two','b','negative_helpful')]
    assert not evidence_gap(row,docs,{'a','b'})['needed']
    row['tags'][0]['oppose_count'] = 1
    gap = evidence_gap(row,docs,{'a','b'})
    assert gap['needed']
    extra = [review('c','character customization matters','c'),review('d','unrelated shipping text','d')]
    assert [d['id'] for d in useful_unread(docs+extra,{'a','b'},[tag],gap)] == ['c']


@pytest.mark.asyncio
async def test_quick_plan_and_balanced_waves_are_persisted(stack):
    s, db, registry, e = stack
    for name in ['quick','balanced']:
        tid = e.create(ResearchRequest(recommendation_mode='player_tags',query='构筑',recommendation_count=10,research_strategy=name))
        await e.step(tid)
        while db.task(tid)['phase'] == 'plan':
            await e.step(tid)
        ctx = db.task(tid)['context']
        assert len(ctx['selected']) <= (3 if name=='quick' else 5)
        assert ctx['strategy']['name'] == name
        assert ctx['catalog_scan']['rows'] <= ctx['strategy']['scan_limit']
        await finish(e,tid)
        r = report(db,tid)
        assert len(db.task(tid)['context']['selected']) <= r['strategy']['investigation_limit']
        if name=='balanced':
            assert any(x['action']=='expand' for x in r['strategy_decisions'])


@pytest.mark.asyncio
async def test_source_cache_ttl_and_partial_retrieval_not_cached(stack, monkeypatch):
    s, db, registry, e = stack
    action = {'tool_name':'get_reviews','arguments':{'game_id':'fx-02'}}
    first = await registry.call(action,Gate(s))
    assert not first.from_cache
    second = await registry.call(action,Gate(s))
    assert second.from_cache
    s.source_reviews_ttl_seconds += 1
    third = await registry.call(action,Gate(s))
    assert not third.from_cache
    original = registry.fixture.call
    async def partial(*args, **kwargs):
        data = await original(*args, **kwargs)
        return data | {'sampling':{'errors':['authored partial failure']}}
    monkeypatch.setattr(registry.fixture,'call',partial)
    s.source_reviews_ttl_seconds += 1
    assert not (await registry.call(action,Gate(s))).from_cache
    assert not (await registry.call(action,Gate(s))).from_cache


def test_reuse_cache_storage_is_bounded_and_expired_rows_removed(stack):
    s, db, *_ = stack
    for n in range(205):
        db.put('interest_summary_cache',str(n),{'expires':time.time()+60})
    put(db,'interest_summary_cache','latest',{'value':'new'},60)
    assert len(db.list('interest_summary_cache')) == 200
    put(db,'interest_summary_cache','expired',{},-1)
    assert get(db,'interest_summary_cache','expired') is None

@pytest.mark.asyncio
async def test_reuse_skips_workflow_extract_and_verify_calls_with_accounting(stack, monkeypatch):
    from pydantic import SecretStr
    from app.tags import workflow
    from app.tags.intent import demo_interpret
    from app.tags.models import Extraction, Verification
    s, db, registry, e = stack
    s.llm_mode = 'live'
    s.llm_base_url = 'https://authored.invalid/v1'
    s.llm_model = 'authored-contract'
    s.llm_api_key = SecretStr('authored-only')
    stages = []
    async def authored(engine, tid, ctx, gate, stage, payload):
        stages.append(stage)
        gate.use('llm_calls')
        gate.use('external_requests')
        if stage == 'interpret':
            return demo_interpret(payload['query'], payload['attachments'])
        if stage == 'extract':
            return Extraction(summary='authored', findings=[{k:v for k,v in f.items() if k!='feature_key'} for f in workflow.demo_findings(payload['documents'],payload['intents'])])
        return Verification(summary='authored',verdicts=[dict(claim_id=c['id'],verdict='supported',reason='authored full review check',evidence_ids=c['evidence_ids']) for c in payload['claims']])
    monkeypatch.setattr(workflow,'model_call',authored)
    first = e.create(request())
    await finish(e,first)
    assert stages == ['interpret','extract','verify']
    stages.clear()
    second = e.create(request())
    await finish(e,second)
    assert stages == ['interpret']
    assert report(db,second)['budget']['llm_calls'] == 1
    assert report(db,second)['recommendation_ids'] == report(db,first)['recommendation_ids']
    s.reuse_enabled = False
    stages.clear()
    third = e.create(request())
    await finish(e,third)
    assert stages == ['interpret','extract','verify']


@pytest.mark.asyncio
async def test_target_stop_is_explained_for_unvisited_selected_games(stack):
    s, db, registry, e = stack
    tid = e.create(ResearchRequest(recommendation_mode='player_tags',query='构筑',game_ids=['fx-01','fx-02'],recommendation_count=1))
    await finish(e,tid)
    rows = report(db,tid)['results']
    skipped = [r for r in rows if r['reading']['fetched']==0]
    assert len(skipped)==1 and skipped[0]['diagnosis']['code']=='target_reached_skip'
    assert skipped[0]['match'] is None
