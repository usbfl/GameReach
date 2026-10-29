import {useEffect,useRef,useState} from 'react';
import {api} from '../api';

export type EditableIntent={tag_id:string;polarity:'want'|'avoid';priority:number;source_id:string;source_quote:string;scope:Record<string,string>;explicit_redline:boolean;confirmed_by_user?:boolean};
export type CustomNeed={text:string;polarity:'want'|'avoid';priority:number;explicit_redline:boolean;status:'unmapped'|'confirmed'|'ignored'};
export type FinalConditions={intents:EditableIntent[];custom:CustomNeed[];constraints:any};

export function IntentRows({intents,tags,onChange}:{intents:EditableIntent[];tags:{id:string;label:string}[];onChange:(rows:EditableIntent[])=>void}){
 function patch(i:number,changes:Partial<EditableIntent>){onChange(intents.map((t,n)=>n===i?{...t,...changes}:t))}
 return <div className="intent-editor-rows">{intents.map((t,i)=><div className="intent-edit-row" key={i}>
  <label>玩法标签<select aria-label={`玩法标签 ${i+1}`} value={t.tag_id} onChange={e=>patch(i,{tag_id:e.target.value})}>{tags.map(x=><option key={x.id} value={x.id}>{x.label}</option>)}</select></label>
  <label>方向<select aria-label={`偏好方向 ${i+1}`} value={t.polarity} onChange={e=>patch(i,{polarity:e.target.value as 'want'|'avoid'})}><option value="want">喜欢</option><option value="avoid">避开</option></select></label>
  <label>重要度<select aria-label={`偏好重要度 ${i+1}`} value={t.priority} onChange={e=>patch(i,{priority:Number(e.target.value)})}><option value={1}>补充</option><option value={2}>普通</option><option value={3}>高</option></select></label>
  <label>条件类型<select aria-label={`条件类型 ${i+1}`} value={t.explicit_redline?'hard':'soft'} onChange={e=>patch(i,{explicit_redline:e.target.value==='hard'})}><option value="soft">普通偏好</option><option value="hard">{t.polarity==='want'?'必须满足':'不能接受'}</option></select></label>
  <button aria-label={`移除标签 ${i+1}`} onClick={()=>onChange(intents.filter((_,n)=>n!==i))}>移除</button>
  {Object.keys(t.scope).length>0&&<small className="intent-scope">适用场景：{Object.values(t.scope).join(' / ')}</small>}
 </div>)}</div>
}

export default function IntentEditor({query,attachments,constraints,onConfirm,onPending}:{query:string;attachments:{id:string;version:number}[];constraints:any;onConfirm:(value:FinalConditions|null,id?:string)=>void;onPending:(pending:boolean)=>void}){
 const [tags,setTags]=useState<{id:string;label:string}[]>([]),[draft,setDraft]=useState<FinalConditions|null>(null),[id,setId]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState(false),[adding,setAdding]=useState('build_variety');
 const signature=JSON.stringify([query,attachments]),generation=useRef(0);
 useEffect(()=>{api('/tags').then(r=>setTags(r.tags)).catch(e=>setError(e.message))},[]);
 useEffect(()=>{generation.current++;setDraft(null);setSaved(false);setBusy(false);onConfirm(null);onPending(false)},[signature]);
 function edit(value:FinalConditions){setDraft(value);setSaved(false);onConfirm(null);onPending(true)}
 async function preview(){const n=++generation.current;setBusy(true);setError('');onPending(true);try{const r=await api('/tag-intent-preview','POST',{query,attachments,constraints},{timeoutMs:100000});if(n!==generation.current)return;setDraft({intents:r.interpretation.intents,custom:r.custom,constraints:r.constraints});setId(r.id);setSaved(false);onConfirm(null)}catch(e:any){if(n===generation.current){setError(e.message);onPending(false)}}finally{if(n===generation.current)setBusy(false)}}
 return <section className="intent-editor" aria-label="编辑本次需求"><div className="section-heading"><div><h3>本次玩法与约束</h3><p className="muted small">可以直接开始探索，也可以先检查解析结果。最终确认的修改优先于兴趣卡片。</p></div><button disabled={busy||(!query.trim()&&!attachments.length)} onClick={preview}>{busy?'解析中…':'解析并编辑需求'}</button></div>
 {draft&&<><IntentRows intents={draft.intents} tags={tags} onChange={intents=>edit({...draft,intents})}/><div className="actions"><label>添加已有标签<select aria-label="添加已有标签" value={adding} onChange={e=>setAdding(e.target.value)}>{tags.map(t=><option key={t.id} value={t.id}>{t.label}</option>)}</select></label><button disabled={draft.intents.length>=12||draft.intents.some(t=>t.tag_id===adding)} onClick={()=>edit({...draft,intents:[...draft.intents,{tag_id:adding,polarity:'want',priority:2,explicit_redline:false,source_id:'current',source_quote:'用户手动添加：'+(tags.find(t=>t.id===adding)?.label||adding),scope:{}}]})}>添加标签</button></div>
 {draft.custom.map((c,i)=><div className="custom-need" key={i}><label>自定义表达<input aria-label={`自定义表达 ${i+1}`} value={c.text} maxLength={500} onChange={e=>edit({...draft,custom:draft.custom.map((x,n)=>n===i?{...x,text:e.target.value}:x)})}/></label><label>处理方式<select aria-label={`自定义处理 ${i+1}`} value={c.status} onChange={e=>edit({...draft,custom:draft.custom.map((x,n)=>n===i?{...x,status:e.target.value as CustomNeed['status']}:x)})}><option value="unmapped">待确认</option><option value="confirmed">保留本次需求</option><option value="ignored">忽略</option></select></label><label><input type="checkbox" checked={c.explicit_redline} onChange={e=>edit({...draft,custom:draft.custom.map((x,n)=>n===i?{...x,explicit_redline:e.target.checked}:x)})}/> 设为硬约束</label><small>暂无正式标签或目录映射，不能保证召回及核验；硬约束保持未知。</small></div>)}
 <p className="muted small">语言要求：{draft.constraints.language||'不限'}；可在下方“语言与购买预算”继续修改。</p>
 <div className="actions"><button className="primary" disabled={draft.custom.some(c=>!c.text.trim())} onClick={()=>{onConfirm(draft,id);onPending(false);setSaved(true)}}>确认本次条件</button><button onClick={()=>{setDraft(null);setSaved(false);onConfirm(null);onPending(false)}}>取消编辑，使用自动解析</button></div>{saved&&<p role="status">本次条件已确认，开始探索时生效。</p>}</>}
 {error&&<p className="error" role="alert">{error}</p>}</section>
}
