import {expectOutcome} from './outcome';
import {test,expect,Page} from '@playwright/test';
async function scout(page:Page){await page.goto('/');await page.getByRole('button',{name:'开始探索',exact:true}).click();await expectOutcome(page);}

test('Tag research, full review evidence, export and history',async({page},info)=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await scout(page);
 await expect(page.getByRole('heading',{name:'从玩家评价中找到与你合拍的游戏'})).toBeVisible();
 await expect(page.locator('.tag-result-card').first()).toContainText('烬刃回响');
 await page.locator('.tag-result-card .tag-evidence summary').first().click();
 await page.locator('.tag-result-card .tag-evidence .citation').first().click();
 const dialog=page.getByRole('dialog',{name:'证据详情'});await expect(dialog).toContainText('虚构');
 await expect(dialog.locator('.source-text')).toBeVisible();await expect(dialog.locator('.source-text')).toContainText('这份感受仅供演示');
 await page.getByRole('button',{name:'关闭证据'}).click();
 const download=page.waitForEvent('download');await page.getByRole('link',{name:'Markdown',exact:true}).click();expect((await download).suggestedFilename()).toMatch(/\.md$/);
 await page.reload();await expect(page.locator('.tag-result-card').first()).toContainText('烬刃回响');
 await page.getByRole('button',{name:'任务历史',exact:true}).click();await page.locator('.history-row').first().click();
 await expect(page.locator('.tag-result-card').first()).toBeVisible();
 await page.screenshot({path:`../artifacts/${info.project.name}-tag-report.png`,fullPage:true});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(errors).toEqual([]);
});

test('New composer has no DNA ratings or search before interpretation',async({page,request})=>{
 const calls:string[]=[];page.on('request',r=>calls.push(r.url()));await page.goto('/');
 await expect(page.getByText('明确偏好与硬约束',{exact:false})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'先看候选（不调用模型）'})).toHaveCount(0);
 expect(calls.some(x=>x.includes('/discovery'))).toBe(false);
 const scope=page.getByRole('region',{name:'探索平台范围'});
 await scope.getByRole('button',{name:'Steam',exact:true}).click();await scope.getByRole('button',{name:'GOG',exact:true}).click();
 await scope.getByRole('button',{name:'PC Windows',exact:true}).click();
 const response=page.waitForResponse(r=>r.url().endsWith('/api/tag-research')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'开始探索',exact:true}).click();const tid=(await (await response).json()).task_id;
 await expectOutcome(page);
 const t=await (await request.get('/api/research/'+tid)).json();expect(t.request.discovery).toEqual({stores:['steam','gog'],platforms:['pc']});
 expect(t.report.mode).toBe('player_tags');expect(t.report.results.some((x:any)=>x.group==='unreviewed')).toBe(true);
 await expect(page.getByLabel('调查进度统计')).toBeVisible();
 await expect(page.locator('.tag-result-card')).toHaveCount(t.report.recommendation_ids.length);
 await page.locator('.pending-candidates>summary').click();
 await expect(page.locator('.pending-candidate').first()).toContainText(t.report.results.find((x:any)=>x.group==='unreviewed').diagnosis.label);
});

test('Human verifier decisions persist without blanket acceptance',async({page})=>{
 await page.goto('/');await page.getByText('Agent 与人工分工',{exact:false}).click();
 await page.getByLabel('Verifier · 核验',{exact:true}).selectOption('human');await page.getByRole('button',{name:'开始探索',exact:true}).click();
 await expect(page.getByText('轮到你了 · verifier')).toBeVisible();await page.reload();
 await expect(page.getByText('轮到你了 · verifier')).toBeVisible();await page.getByLabel('核验决定',{exact:true}).selectOption('reject');
 await page.locator('.claim-checkbox input').first().check();await page.getByLabel('你的意见',{exact:true}).fill('这条主张需要更多来源。');
 await page.getByRole('button',{name:'提交并恢复',exact:true}).click();await expect(page.locator('.task-header .status')).toHaveText('部分完成');
 await expect(page.locator('.tag-results')).toContainText('待调查');
});

test('Source rate limit remains an honest partial result',async({page})=>{
 await page.goto('/');await page.getByText('Agent 与人工分工',{exact:false}).click();await page.getByLabel('演示故障',{exact:true}).selectOption('rate_limited');
 await page.getByRole('button',{name:'开始探索',exact:true}).click();await expect(page.locator('.task-header .status')).toHaveText('部分完成');
 await expect(page.locator('.tag-results>.warning').filter({hasText:'rate_limited'})).toContainText('rate_limited');
 await expect(page.getByText('本轮尚未形成可推荐结论',{exact:true})).toBeVisible();
 await expect(page.locator('.tag-result-card')).toHaveCount(0);
 await expect(page.locator('.pending-candidate').filter({hasText:'评价获取失败'}).first()).toBeVisible();
});

test('Interest shortlist still supports local add and remove',async({page})=>{
 await page.goto('/');await page.getByRole('button',{name:'感兴趣的游戏',exact:true}).click();await page.getByRole('combobox',{name:'游戏',exact:true}).selectOption('fx-02');
 await page.getByLabel('哪里吸引你？（可选）').fill('每一步有取舍的策略');await page.getByRole('button',{name:'加入清单',exact:true}).click();
 const card=page.locator('.watch-card').filter({hasText:'星图议会'});await expect(card).toContainText('每一步有取舍');
 await page.getByRole('button',{name:'加入清单',exact:true}).click();await expect(card).toHaveCount(1);
 await card.getByRole('button',{name:'移除星图议会',exact:true}).click();await expect(card).toHaveCount(0);
});

test('Interest card classification and attachments preserve prompt',async({page,request},info)=>{
 const games=Array.from({length:3},(_,n)=>({platform:'manual',name:`独立附件测试${info.project.name}${n}`,collections:[`附件专用主题${info.project.name}`]}));
 expect((await request.post('/api/player/import/file',{data:{format:'json',content:JSON.stringify({games})}})).ok()).toBe(true);
 await page.goto('/');const query=page.getByLabel('游戏需求',{exact:true});const text='喜欢弹反、重视构筑，不喜欢刷装备';await query.fill(text);
 const panel=page.getByRole('region',{name:'兴趣记忆'});await panel.getByRole('button',{name:'整理我的兴趣',exact:true}).click();
 const more=panel.getByRole('button',{name:/展开其余 .* 张兴趣卡片/});if(await more.count())await more.click();
 const candidate=panel.locator('.memory-card').filter({has:page.getByRole('button',{name:'确认这张卡片',exact:true})}).first();
 await expect(candidate).toBeVisible();const card=panel.locator(`[data-card-id="${await candidate.getAttribute('data-card-id')}"]`);await card.getByRole('button',{name:'编辑',exact:true}).click();
 await card.getByLabel('分类标题').fill('读招与变化');await card.getByLabel('概括').fill('我享受观察敌人、把握弹反时机的战斗，也希望构筑能带来变化。');
 await card.getByRole('button',{name:'保存卡片',exact:true}).click();await card.getByRole('button',{name:'确认这张卡片',exact:true}).click();
 const saved=page.locator('.memory-card').filter({has:page.getByRole('heading',{name:'读招与变化',exact:true})}).last();
 await saved.getByRole('button',{name:'附加到研究输入',exact:true}).click();await expect(query).toHaveValue(text);
 await expect(page.getByLabel('本次附加卡片')).toContainText('读招与变化');
 await page.getByRole('button',{name:'移除附件 读招与变化',exact:true}).click();await expect(page.getByLabel('本次附加卡片')).not.toContainText('读招与变化');
 await expect(query).toHaveValue(text);
});

test('Home layout has no overflow or browser errors',async({page},info)=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));await page.goto('/');await expect(page.getByText('下一款游戏。')).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.screenshot({path:`../artifacts/${info.project.name}-tag-home.png`,fullPage:true});expect(errors).toEqual([]);
});
