import {labels,value,ontology,Preference,RoleModes} from '../api';

export function PreferenceEditor({prefs,onChange}:{prefs:Preference[];onChange:(p:Preference[])=>void}){
  function set(key:string,target:any){const next=prefs.filter(p=>p.feature_key!==key);if(target!==null)next.push({feature_key:key,target,weight:prefs.find(p=>p.feature_key===key)?.weight||1});onChange(next);}
  const options:Record<string,{label:string;target:any}[]>={
    combat_mode:['realtime','turn_based','hybrid','none'].map(v=>({label:value(v),target:v})),
    parry:[{label:'支持',target:true},{label:'不需要',target:false}],dodge:[{label:'支持',target:true},{label:'不需要',target:false}],
    session_length_minutes:[{label:'30 分钟以内',target:[0,30]},{label:'30–60 分钟',target:[30,60]},{label:'60–120 分钟',target:[60,120]}]
  };
  return <div className="preference-grid">{Object.keys(labels).map(k=>{const p=prefs.find(x=>x.feature_key===k);const meta=(ontology.features as any)[k];const opts=options[k]||(meta.type==='bool'?[{label:'有',target:true},{label:'没有',target:false}]:meta.type==='enum'?meta.values.map((v:string,i:number)=>({label:meta.anchors[i],target:v})):[0,1,2,3,4].map(n=>({label:`${n} · ${['低 / 无','少','中','高','很高'][n]}`,target:n})));return <div className="preference-field" key={k}><label htmlFor={'p-'+k}>{labels[k]}</label><select id={'p-'+k} value={p?JSON.stringify(p.target):''} onChange={e=>set(k,e.target.value?JSON.parse(e.target.value):null)}><option value="">不限 / 未指定</option>{opts.map(o=><option key={o.label} value={JSON.stringify(o.target)}>{o.label}</option>)}</select>{p&&<label className="weight">权重<select aria-label={labels[k]+'权重'} value={p.weight} onChange={e=>onChange(prefs.map(x=>x.feature_key===k?{...x,weight:Number(e.target.value)}:x))}>{[1,2,3].map(n=><option key={n}>{n}</option>)}</select></label>}</div>})}</div>
}

export function RoleEditor({roles,onChange}:{roles:RoleModes;onChange:(r:RoleModes)=>void}){
  const names={planner:'Planner · 规划',researcher:'Researcher · 调查',verifier:'Verifier · 核验'};
  return <div className="roles">{(Object.keys(names) as (keyof RoleModes)[]).map(k=><label key={k}>{names[k]}<select aria-label={names[k]} value={roles[k]} onChange={e=>onChange({...roles,[k]:e.target.value})}><option value="ai">Agent 执行</option><option value="human">由我接管</option></select></label>)}</div>
}
