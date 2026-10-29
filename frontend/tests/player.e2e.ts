import {expectOutcome} from './outcome';
import {test,expect} from '@playwright/test';

test('Library selection, preference draft, explicit confirmation and undo',async({page,request},testInfo)=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/');await page.getByRole('button',{name:'我的游戏资料',exact:true}).click();
 await page.getByRole('button',{name:'体验虚构游戏库示例',exact:true}).click();
 await expect(page.getByRole('heading',{name:'哪些游戏最能代表现在的你？'})).toBeVisible();
 await page.getByLabel('游戏库分类').selectOption('周末最爱');
 await page.getByRole('button',{name:'仅分析当前范围',exact:true}).click();
 await expect(page.getByText('3 / 7 已选',{exact:true})).toBeVisible();
 const before=(await (await request.get('/api/profile')).json()).version;
 await page.getByRole('button',{name:'分析所选游戏，生成兴趣草稿',exact:true}).click();
 await expect(page.getByRole('heading',{name:'我们眼中的你的游戏口味'})).toBeVisible();
 expect((await (await request.get('/api/profile')).json()).version).toBe(before);
 await page.getByLabel('介绍风格',{exact:true}).selectOption('vivid');
 await page.screenshot({path:`../artifacts/player-import-${testInfo.project.name}.png`,fullPage:true});
 await page.getByRole('button',{name:'确认并加入游戏探索',exact:true}).click();
 await expect(page.getByRole('button',{name:'带着这些兴趣去探索',exact:true})).toBeVisible();
 expect((await (await request.get('/api/profile')).json()).reading_style).toBe('vivid');
 expect((await (await request.get('/api/interests')).json()).items.some((c:any)=>c.status==='confirmed')).toBe(true);
 await page.getByRole('button',{name:'撤销刚才应用的阅读风格',exact:true}).click();
 await expect(page.getByText('已撤销本次阅读风格；兴趣卡片可在探索页单独删除。',{exact:true})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect(errors).toEqual([]);
});

test('File imports give actionable errors and recommendations show readable evidence',async({page},testInfo)=>{
 await page.goto('/');await page.getByRole('button',{name:'我的游戏资料',exact:true}).click();
 await page.getByLabel('选择游戏资料文件').setInputFiles({name:'bad.json',mimeType:'application/json',buffer:Buffer.from('{')});
 await expect(page.getByRole('alert')).toContainText('JSON 格式不正确');
 await page.getByRole('button',{name:'游戏探索',exact:true}).click();
 await page.getByLabel('游戏需求',{exact:true}).fill('重视策略和构筑，喜欢回合制');
 await page.getByRole('button',{name:'开始探索',exact:true}).click();
 await expectOutcome(page);
 await expect(page.locator('.tag-result-card').first()).toBeVisible();
 await page.locator('.tag-result-card .citation').first().click();
 await expect(page.getByRole('dialog',{name:'证据详情'})).toBeVisible();
 await page.getByRole('button',{name:'关闭证据',exact:true}).click();
 await page.screenshot({path:`../artifacts/player-reading-${testInfo.project.name}.png`,fullPage:true});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('Steam import shows inline progress, failure and empty-library success',async({page})=>{
 await page.goto('/');await page.getByRole('button',{name:'我的游戏资料',exact:true}).click();
 await page.getByLabel('Steam 账号标识').fill('76561190000000000');
 // Real backend rejects fixture mode, and the error stays next to the button.
 await page.getByRole('button',{name:'读取我的 Steam 游戏库',exact:true}).click();
 const card=page.locator('.import-sources > section').first();
 await expect(card.getByRole('alert')).toContainText('DATA_MODE=live');
 let release!:()=>void;
 const gate=new Promise<void>(r=>release=r);
 await page.route('**/api/player/import/steam',async route=>{await gate;await route.fulfill({json:{id:'test-empty',games:[]}})});
 await page.getByRole('button',{name:'读取我的 Steam 游戏库',exact:true}).click();
 await expect(card.getByRole('button',{name:'正在读取 Steam 游戏库…'})).toBeDisabled();
 await expect(card.getByRole('status')).toContainText('正在连接 Steam');
 release();
 await expect(card.getByRole('status')).toContainText('读取完成：0 款游戏');
 await expect(card.getByRole('alert')).toHaveCount(0);
 await page.unroute('**/api/player/import/steam');
 await page.route('**/api/player/import/steam',route=>route.abort());
 await page.getByRole('button',{name:'读取我的 Steam 游戏库',exact:true}).click();
 await expect(card.getByRole('alert')).toContainText('无法连接本地后端');
});
