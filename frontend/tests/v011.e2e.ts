import {test,expect} from '@playwright/test';
import {expectOutcome} from './outcome';

test('v0.11 editable final conditions persist, partial matches and local reorder',async({page,request},info)=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/');await page.getByLabel('游戏需求',{exact:true}).fill('构筑丰富，不太喜欢大量刷装备');
 await page.getByRole('button',{name:'解析并编辑需求',exact:true}).click();
 await expect(page.getByLabel('玩法标签 1',{exact:true})).toBeVisible();
 await page.getByLabel('偏好方向 2',{exact:true}).selectOption('want');
 await page.getByLabel('条件类型 2',{exact:true}).selectOption('hard');
 await page.getByLabel('移除标签 2',{exact:true}).click();
 await page.getByLabel('添加已有标签',{exact:true}).selectOption('quick_retry');
 await page.getByRole('button',{name:'添加标签',exact:true}).click();
 await page.getByLabel('偏好重要度 2',{exact:true}).selectOption('1');
 await page.getByRole('button',{name:'确认本次条件',exact:true}).click();
 await page.getByLabel('期望推荐数量',{exact:true}).selectOption('1');
 await page.screenshot({path:`../artifacts/v011-editor-${info.project.name}.png`,fullPage:true});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 const response=page.waitForResponse(r=>r.url().endsWith('/api/tag-research')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'开始探索',exact:true}).click();
 const tid=(await (await response).json()).task_id;
 const task=await expectOutcome(page);
 expect(task.context.interpretation.intents.map((i:any)=>i.tag_id)).toEqual(['build_variety','quick_retry']);
 expect(task.context.interpretation.intents.every((i:any)=>i.confirmed_by_user)).toBe(true);
 expect(task.report.recommendation_ids.length).toBe(1);
 await page.getByText('调整标签重要度，在已有证据上重排',{exact:true}).click();
 await page.getByLabel('重要度 build_variety',{exact:true}).selectOption('1');
 await page.getByRole('button',{name:'按当前重要度重排',exact:true}).click();
 await expect(page.getByText('显示本地重排结果；导出文件仍为最初生成的报告。')).toBeVisible();
 expect((await (await request.get('/api/research/'+tid)).json()).context.budget).toEqual(task.context.budget);
 expect(errors).toEqual([]);
});

test('v0.11 overlap cards permit actual member editing and persist confirmation',async({page,request},info)=>{
 // A separate manual dataset has two distinct user-defined themes and one excluded high-hour game.
 const prefix='v011-'+info.project.name;
 const games=[['A',['甲主题'+prefix,'乙主题'+prefix]],['B',['甲主题'+prefix]],['C',['甲主题'+prefix]],['D',['乙主题'+prefix]],['E',['乙主题'+prefix]],['F',['甲主题'+prefix]]].map(([name,collections])=>({platform:'manual',name:prefix+name,collections,reaction:name==='F'?'exclude':'neutral',playtime_minutes:name==='F'?60000:null}));
 expect((await request.post('/api/player/import/file',{data:{format:'json',content:JSON.stringify({games})}})).ok()).toBe(true);
 const generated=await (await request.post('/api/interests/generate',{data:{regroup:true}})).json();
 const cards=generated.items.filter((c:any)=>c.related_games?.some((g:any)=>g.name===prefix+'A'));
 expect(cards.length).toBe(2);
 expect(cards.every((c:any)=>c.related_games.length===3&&!c.related_games.some((g:any)=>g.name===prefix+'F'))).toBe(true);
 await page.goto('/');
 const expand=page.getByRole('button',{name:/展开其余 .* 张兴趣卡片/});if(await expand.count())await expand.click();
 const card=page.locator(`[data-card-id="${cards[0].id}"]`);
 await card.getByRole('button',{name:'编辑',exact:true}).click();
 await card.getByLabel('分类标题').fill('手动确认的重叠主题');
 const firstOther=cards[0].related_games.find((g:any)=>g.name!==prefix+'A');
 await card.getByLabel('成员 '+firstOther.name,{exact:true}).uncheck();
 const replacement=games.find(g=>!cards[0].related_games.some((x:any)=>x.name===g.name)&&g.reaction!=='exclude')!;
 await card.getByLabel('成员 '+replacement.name,{exact:true}).check();
 await card.getByRole('button',{name:'保存卡片',exact:true}).click();
 await card.getByRole('button',{name:'确认这张卡片',exact:true}).click();
 const stored=(await (await request.get('/api/interests')).json()).items.find((c:any)=>c.id===cards[0].id);
 expect(stored.status).toBe('confirmed');expect(stored.version).toBe(3);
 expect(stored.related_games.map((g:any)=>g.name)).toContain(replacement.name);
 expect(stored.related_games.map((g:any)=>g.name)).not.toContain(firstOther.name);
 await page.reload();const more=page.getByRole('button',{name:/展开其余 .* 张兴趣卡片/});if(await more.count())await more.click();
 await expect(page.getByRole('heading',{name:'手动确认的重叠主题',exact:true})).toBeVisible();
});

test('v0.11 custom hard expression stays unknown and cannot qualify a candidate',async({page})=>{
 await page.goto('/');await page.getByLabel('游戏需求',{exact:true}).fill('构筑丰富，想用嗅觉识别藏宝地点');
 await page.getByRole('button',{name:'解析并编辑需求',exact:true}).click();
 await expect(page.getByLabel('自定义表达 1',{exact:true})).toHaveValue('想用嗅觉识别藏宝地点');
 await page.getByLabel('自定义处理 1',{exact:true}).selectOption('confirmed');
 await page.getByLabel('设为硬约束',{exact:true}).check();
 await page.getByRole('button',{name:'确认本次条件',exact:true}).click();
 await page.getByLabel('期望推荐数量',{exact:true}).selectOption('1');
 await page.getByRole('button',{name:'开始探索',exact:true}).click();
 const task=await expectOutcome(page);
 expect(task.status).toBe('partial');expect(task.report.recommendation_ids).toEqual([]);
 expect(task.report.results.some((r:any)=>r.group==='provisional'&&r.hard_constraint_status==='unknown')).toBe(true);
 await page.getByRole('button',{name:/需要进一步确认/}).click();
 await expect(page.locator('.tag-result-card .warning').filter({hasText:'硬约束待确认'}).first()).toBeVisible();
});
