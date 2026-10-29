import {expect,Page} from '@playwright/test';
// The fixed corpus can legitimately provide fewer than five cards. Check the
// persisted target and status together rather than asserting a false completion.
export async function expectOutcome(page:Page){
 await expect.poll(()=>page.evaluate(()=>localStorage.getItem('gamereach-task'))).not.toBeNull();
 const tid=await page.evaluate(()=>localStorage.getItem('gamereach-task'));
 expect(tid).toBeTruthy();
 await expect.poll(async()=>{const t=await (await page.request.get('/api/research/'+tid)).json();return Boolean(t.report);}).toBe(true);
 const task=await (await page.request.get('/api/research/'+tid)).json();
 expect(task.report.target.achieved).toBe(task.report.recommendation_ids.length);
 expect(task.report.target.met).toBe(task.report.target.achieved>=task.report.target.requested);
 expect(task.status).toBe(task.report.target.met&&!task.report.errors.length?'completed':'partial');
 await expect(page.locator('.task-header .status')).toHaveText(task.status==='completed'?'研究完成':'部分完成');
 return task;
}
