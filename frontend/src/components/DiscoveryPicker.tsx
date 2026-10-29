import {useEffect,useState} from 'react';
import {DnaQuery} from './DnaEditor';
import {api,Preference} from '../api';
export type Scope={stores:string[];platforms:string[]};
const stores=[['steam','Steam'],['gog','GOG'],['epic','Epic Games'],['itch','itch.io']];
const platforms=[['pc','PC Windows'],['switch','Nintendo Switch'],['ps5','PlayStation 5'],['xbox_series','Xbox Series']];
export default function DiscoveryPicker({scope,onChange,query,prefs,constraints,dna}:{dna?:DnaQuery|null;scope:Scope;onChange:(v:Scope)=>void;query:string;prefs:Preference[];constraints:any}){
 const [result,setResult]=useState<any>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const fingerprint=JSON.stringify([scope,query,prefs,constraints,dna]);
 const [resultFor,setResultFor]=useState('');
 const [expanded,setExpanded]=useState(false);
 useEffect(()=>{setError('');setExpanded(false)},[fingerprint]);
 function toggle(key:'stores'|'platforms',value:string){const v=scope[key];onChange({...scope,[key]:v.includes(value)?v.filter(x=>x!==value):[...v,value]})}
 async function preview(){setBusy(true);setError('');const key=fingerprint;try{const r=await api('/discovery','POST',{query,preferences:prefs,dna_query:dna,constraints,discovery:scope});setResult(r);setResultFor(key);if(r.status!=='available')setError('来源暂不可用：'+(r.error||r.status))}catch(e:any){setError(e.message)}finally{setBusy(false)}}
 return <section className="discovery-scope" aria-label="探索平台范围"><h3>在哪里找游戏？</h3><p className="muted small">默认全部。可多选，同组内满足任意一个即可；商店与运行平台条件同时生效。</p>
 {([['stores','商店平台',stores],['platforms','运行平台',platforms]] as const).map(([key,title,options])=><fieldset key={key}><legend>{title}</legend><div className="scope-options"><button type="button" aria-pressed={scope[key].length===0} onClick={()=>onChange({...scope,[key]:[]})}>全部{title}</button>{options.map(([v,label])=><button type="button" key={v} aria-pressed={scope[key].includes(v)} onClick={()=>toggle(key,v)}>{scope[key].includes(v)?'✓ ':''}{label}</button>)}</div></fieldset>)}
 <p className="muted small">“全部”表示不限制已收录的平台，并不代表抓取所有商店。Steam / GOG / Epic / itch.io 由 IGDB 收录链接识别。</p>
 <button type="button" onClick={preview} disabled={busy||!query.trim()||!dna}>{busy?'正在检索候选…':'先看候选（不调用模型）'}</button>
 {error&&<p role="alert" className="error">{error}</p>}
 {result&&resultFor!==fingerprint&&<p role="status">条件已修改，请重新查看候选。开始探索会使用当前条件。</p>}
 {result&&resultFor===fingerprint&&<div aria-label="探索候选预览"><h3>找到 {result.items.length} 个候选 <small>· {result.data_mode==='live'?'真实数据':'合成演示数据'}</small></h3><p className="muted small">{result.note}</p>{result.search_info?.genre_hints?.length>0&&<p>检索类型线索：{result.search_info.genre_hints.join(' / ')}</p>}{result.search_info?.keyword_hints?.length>0&&<p>玩法检索词：{result.search_info.keyword_hints.join(" / ")}</p>}{result.search_info?.candidates_scanned!==undefined&&<p className="muted small">检查 {result.search_info.candidates_scanned} 条来源记录；最多展示 20 个。{result.search_info.recall_order}。</p>}
 {!result.items.length&&<p>本次没有找到符合范围的候选。可减少平台限制或调整偏好；不会自动扩大你指定的范围。</p>}
 <div className="discovery-cards">{result.items.slice(0,expanded?20:4).map((g:any)=><article className="discovery-card" key={g.id}><h4>{g.name}</h4><small>{g.id} · {g.constraint_status==='pass'?'已满足已检查硬约束':'部分条件待确认'}</small><p>{(g.tags||[]).join(' / ')||'类型未知'}</p><p className="muted small">{(g.platforms||[]).join(' / ')||'运行平台未知'}</p><div>{g.source_url&&<a href={g.source_url} target="_blank" rel="noreferrer">IGDB 来源</a>}{Object.entries(g.store_links||{}).map(([k,v])=><a key={k} href={String(v)} target="_blank" rel="noreferrer"> · {k}</a>)}</div></article>)}</div>{result.items.length>4&&<button type="button" onClick={()=>setExpanded(!expanded)}>{expanded?'收起候选':`展开其余 ${result.items.length-4} 个候选`}</button>}<p className="muted small">这些是候选，不是已验证的推荐。“开始探索”将自动选取有限候选，调查机制证据并解释适配与未知项。</p></div>}
 </section>
}
