import {expectOutcome} from './outcome';
import {test,expect} from '@playwright/test';
test('Keyboard priority changes rerank frozen evidence without calls',async({page,request})=>{
 await page.goto('/');await page.getByLabel('游戏需求',{exact:true}).fill('重视战斗技巧和构筑，不需要大量刷装备');
 const created=page.waitForResponse(r=>r.url().endsWith('/api/tag-research')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'开始探索',exact:true}).click();const tid=(await (await created).json()).task_id;
 await expectOutcome(page);const task=await (await request.get('/api/research/'+tid)).json();
 await page.getByText('调整标签重要度，在已有证据上重排',{exact:true}).click();
 const priority=page.getByLabel('重要度 build_variety',{exact:true});await priority.focus();await priority.selectOption('3');
 await page.getByRole('button',{name:'按当前重要度重排',exact:true}).click();await expect(page.getByText('显示本地重排结果；导出文件仍为最初生成的报告。')).toBeVisible();
 expect((await (await request.get('/api/research/'+tid)).json()).context.budget).toEqual(task.context.budget);
 await page.reload();await expect(page.getByText('显示本地重排结果；导出文件仍为最初生成的报告。')).toHaveCount(0);
});
