import {Scope} from './DiscoveryPicker';
const stores=[['steam','Steam'],['gog','GOG'],['epic','Epic Games'],['itch','itch.io']];
const platforms=[['pc','PC Windows'],['switch','Nintendo Switch'],['ps5','PlayStation 5'],['xbox_series','Xbox Series']];
export default function TagScope({scope,onChange}:{scope:Scope;onChange:(s:Scope)=>void}){
 return <section className="discovery-scope" aria-label="探索平台范围"><h3>在哪里找游戏？</h3><p className="muted small">同组内满足任意一个即可。开始探索后，先由模型理解输入，再按标签映射检索。</p>{([['stores','商店',stores],['platforms','运行平台',platforms]] as const).map(([key,label,options])=><fieldset key={key}><legend>{label}</legend><div className="scope-options"><button type="button" aria-pressed={!scope[key].length} onClick={()=>onChange({...scope,[key]:[]})}>全部{label}</button>{options.map(([v,n])=><button key={v} type="button" aria-pressed={scope[key].includes(v)} onClick={()=>onChange({...scope,[key]:scope[key].includes(v)?scope[key].filter(x=>x!==v):[...scope[key],v]})}>{n}</button>)}</div></fieldset>)}<p className="muted small">四个商店通过 IGDB 的目录链接召回。当前完整玩家评价接口为 Steam；其他商店的独占游戏可能只有目录信息，会保留为待调查。</p></section>
}
