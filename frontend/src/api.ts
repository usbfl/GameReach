export async function api<T=any>(path:string,method='GET',body?:unknown,options?:{timeoutMs:number}):Promise<T>{
  const controller=new AbortController();
  const timer=options?setTimeout(()=>controller.abort(),options.timeoutMs):undefined;
  try{
    let r:Response;
    try{r=await fetch('/api'+path,{method,signal:controller.signal,headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined})}
    catch(e){if(controller.signal.aborted)throw new Error('请求等待超时，请检查后端和网络。可先查看上次的导入，再重试。');throw new Error('无法连接本地后端，请确认服务已启动，再重试。')}
    let data:any;
    try{data=await r.json()}catch(e){throw new Error(controller.signal.aborted?'请求等待超时，请检查后端和网络。':`后端返回了无法读取的响应（${r.status}），请检查服务终端。`)}
    if(!r.ok)throw new Error(data.error?.message||(typeof data.detail==='string'?data.detail:data.detail?.message)||`请求失败 ${r.status}`);
    return data;
  }finally{if(timer!==undefined)clearTimeout(timer)}
}
import ontology from './dna-ontology.json';
export const labels:Record<string,string>=Object.fromEntries(Object.entries(ontology.features).map(([k,v])=>[k,v.label]));
export {ontology};
export const states:Record<string,string>={queued:'排队中',running:'调查中',paused:'等待处理',completed:'研究完成',partial:'部分完成',failed:'任务失败',cancelled:'已取消'};
export const groups:Record<string,string>={recommended:'证据相对充分',discovery:'候选展示',needs_confirmation:'硬约束待确认',insufficient:'证据不足',excluded:'已排除'};
export function value(v:unknown):string{if(v===null||v===undefined)return '未知';if(typeof v==='boolean')return v?'支持':'不支持';if(Array.isArray(v))return v.join('–');return ({realtime:'实时',turn_based:'回合制',hybrid:'混合',none:'无战斗'} as Record<string,string>)[String(v)]||String(v);}
export const time=(t:string|null)=>t?new Date(t).toLocaleString('zh-CN',{timeZone:'Asia/Taipei',hour12:false}):'未知';
export type RoleModes={planner:'ai'|'human';researcher:'ai'|'human';verifier:'ai'|'human'};
export type Preference={feature_key:string;target:any;weight:number};
export type Game={store_links?:Record<string,string>;source_url?:string;id:string;name:string;subtitle:string;tags:string[];dataset_id:string;kind:string|null};
export const defaultConstraints={platform:'PC',released_only:true,language:null as string|null,max_price:null as number|null,currency:'CNY',region:'CN',played:[] as string[]};
export const defaultRoles:RoleModes={planner:'ai',researcher:'ai',verifier:'ai'};
