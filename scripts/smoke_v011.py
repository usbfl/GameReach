#!/usr/bin/env python3
"""Start a real local HTTP server with an isolated fixture DB; never enable live services."""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    assertions = []
    def check(name, condition):
        assertions.append({'name':name,'passed':bool(condition)})
        assert condition, name
    with tempfile.TemporaryDirectory(prefix='gamereach-http-') as tmp:
        # OS-selected free port, shared network namespace with this child server.
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(path, body=None, method=None):
            req = urllib.request.Request(f'http://127.0.0.1:{port}'+path,
                data=json.dumps(body).encode() if body is not None else None,
                headers={'Content-Type':'application/json'}, method=method)
            with opener.open(req,timeout=15) as r:
                return json.load(r)
        def terminal(tid, paused=False):
            for _ in range(400):
                task = request('/api/research/'+tid)
                if task['status'] in {'completed','partial','failed','cancelled'} or (paused and task['status']=='paused'):
                    return task
                time.sleep(.05)
            raise AssertionError('HTTP task timed out')
        log = open(Path(tmp)/'server.log','w+')
        process = subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--app-dir',str(ROOT/'backend'),
            '--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=ROOT,
            env=os.environ|{'DATA_MODE':'fixtures','LLM_MODE':'mock','DATABASE_URL':'sqlite:///'+tmp+'/http.db'},stdout=log,stderr=log)
        try:
            for _ in range(100):
                try: health=request('/healthz');break
                except OSError: time.sleep(.1)
            else: raise AssertionError('HTTP server did not become ready')
            check('health_v011',health['version']=='0.11.0')
            request('/api/player/import/file',{'format':'json','content':(ROOT/'data/fixtures/v011_player_library.json').read_text(encoding='utf-8')})
            grouped=request('/api/interests/generate',{})
            check('two_overlapping_cards',len(grouped['cards'])==2 and bool(set(grouped['cards'][0]['game_keys'])&set(grouped['cards'][1]['game_keys'])))
            check('card_cache_reused',request('/api/interests/generate',{})['cache']['hit'])
            card=grouped['cards'][0]
            saved=request('/api/interests/'+card['id'],{'version':1,'title':card['title'],'text':card['text'],'game_keys':card['game_keys'],'confirm':True},'PATCH')
            check('confirmed_card_persisted',saved['status']=='confirmed' and saved['version']==2)
            query='构筑丰富，比较偏好快速重试'
            preview=request('/api/tag-intent-preview',{'query':query})
            check('preview_does_not_retrieve',preview['budget']['tool_calls']==0)
            intents=preview['interpretation']['intents']
            for item in intents:
                item['priority']=3 if item['tag_id']=='build_variety' else 1
            conditions={'intents':intents,'custom':[],'constraints':preview['constraints']}
            tid=request('/api/tag-research',{'query':query,'confirmed_conditions':conditions,'intent_preview_id':preview['id'],'game_ids':['fx-02'],'recommendation_count':1})['task_id']
            task=terminal(tid);report=task['report'];row=report['results'][0]
            check('partial_preference_match_completes_target',task['status']=='completed' and report['target']['achieved']==1 and row['partial_match'])
            check('unknown_retry_not_claimed',next(t for t in row['tags'] if t['tag_id']=='quick_retry')['match'] is None)
            check('final_user_conditions_used',all(i['confirmed_by_user'] for i in task['context']['interpretation']['intents']))
            for claim in row['card']['strengths']:
                e=request('/api/evidence/'+claim['evidence_ids'][0])
                check('exact_citation_'+claim['id'],e['quote']==e['document']['text'][e['start']:e['end']])
            reranked=request('/api/tag-research/'+tid+'/rerank',{'priorities':{'build_variety':1}})
            check('local_rerank_no_external_work',request('/api/research/'+tid)['context']['budget']==task['context']['budget'] and reranked['results'])
            hard=request('/api/tag-research',{'query':'构筑','game_ids':['fx-02'],'recommendation_count':1,'constraints':{'max_price':1}})['task_id']
            hard_task=terminal(hard)
            check('unknown_price_blocks_confirmed_group',not hard_task['report']['recommendation_ids'] and hard_task['report']['results'][0]['hard_constraint_status']!='pass')
            human=request('/api/tag-research',{'query':'构筑','game_ids':['fx-02'],'recommendation_count':1,'role_modes':{'planner':'human'}})['task_id']
            paused=terminal(human,True)
            check('human_checkpoint_paused',paused['status']=='paused' and paused['context']['pending_human']['role']=='planner')
            request('/api/research/'+human+'/human-actions',{'action_id':uuid.uuid4().hex,'state_version':paused['version'],'decision':'accept','note':'离线HTTP验收：确认计划'})
            check('human_resume_completed',terminal(human)['status']=='completed')
            check('history_report_immutable',request('/api/research/'+tid)['report']==report)
            check('fixture_mock_zero_external',task['context']['budget']['external_requests']==0 and task['context']['budget']['llm_calls']==0)
            result={'transport':'real local HTTP + uvicorn','health':health,'passed':len(assertions),'failed':0,'assertions':assertions,
                    'report_target':report['target'],'budget':task['context']['budget'],'hard_constraint_checks':hard_task['report']['results'][0]['checks'],
                    'modes':['fixtures','mock'],'live_services':'not_run'}
            out=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/'docs/validation_v011/http_smoke.json'
            out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'passed':len(assertions),'failed':0,'output':str(out)}))
        except Exception:
            log.flush();log.seek(0);print(log.read(),file=sys.stderr);raise
        finally:
            process.terminate();process.wait(timeout=10);log.close()


if __name__=='__main__': main()
