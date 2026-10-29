export const strategies = [
 {id:'quick',label:'快速探索',text:'扫描最多150条目录，先查最多3款，每款1批；适合先试探方向。'},
 {id:'balanced',label:'均衡精选',text:'广泛扫描目录，初筛30款；先查最多5款，未达目标再逐款递补，按重要缺口追加。'},
 {id:'thorough',label:'扩大调查',text:'保留最多80款候选，最多调查20款；对重要缺口投入更多预算。'},
];
export default function ResearchStrategy({value,onChange}:{value:string;onChange:(v:string)=>void}){
 return <fieldset className="research-strategy"><legend>探索策略</legend><div className="scope-options">{strategies.map(s=><button type="button" key={s.id} aria-pressed={value===s.id} onClick={()=>onChange(s.id)}>{s.label}</button>)}</div><p className="muted small">{strategies.find(s=>s.id===value)?.text}所有档位受设置中的预算上限约束；未达到推荐数量会说明原因。有效来源和已核验证据会自动复用。</p></fieldset>
}
