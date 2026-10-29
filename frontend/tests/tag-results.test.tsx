import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import TagResults from '../src/components/TagResults';
const candidate={game:{id:'test',name:'Authored test candidate'},group:'unreviewed',rank_score:null,match:null,coverage:null,supporting_reviews:0,opposing_reviews:0,tags:[],reading:{fetched:0,full_read:0,deferred:0},excluded_reasons:[],unknowns:[],diagnosis:{label:'评价获取失败',next_step:'检查来源连接。',extracted:0,verified:0,errors:[{stage:'retrieve',code:'rate_limited'}]}};
function report(rows:any[]){return {task_id:'test',results:rows,note:'Test report',data_mode:'fixtures',errors:[],mappings:[],interpretation:{intents:[]},assumptions:[],unknowns:[],budget:{},investigation_limit:1,diagnostics:{recalled:rows.length,selected:1,with_reviews:0,full_read:0,with_findings:0,verified:0,with_matches:0}}}
describe('No-data report presentation',()=>{
 it('renders an honest empty state and cause without a scored card or 0% metrics',()=>{
  const html=renderToStaticMarkup(<TagResults report={report([candidate])} notify={()=>{}}/>);
  expect(html).toContain('本轮尚未形成可推荐结论');
  expect(html).toContain('评价获取失败');
  expect(html).toContain('rate_limited');
  expect(html).not.toContain('class="tag-result-card panel"');
  expect(html).not.toContain('class="tag-metrics"');
  expect(html).not.toContain('排序分 / 100');
 });
 it('keeps supported results as cards and pending candidates as a separate list',()=>{
  const supported={...candidate,game:{id:'supported',name:'Supported test'},group:'recommended',rank_score:80,match:1,coverage:.5,reading:{fetched:3,full_read:3,deferred:0}};
  const html=renderToStaticMarkup(<TagResults report={report([supported,candidate])} notify={()=>{}}/>);
  expect(html.match(/class="tag-result-card panel"/g)?.length).toBe(1);
  expect(html).toContain('pending-candidate');
  expect(html).toContain('50%');
  expect(html).not.toContain('本轮尚未形成可推荐结论');
 });
});
