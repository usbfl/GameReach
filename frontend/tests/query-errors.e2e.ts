import {test,expect} from '@playwright/test';

test('An interrupted investigation explains failure without claiming no matching games',async({page,request},testInfo)=>{
 const query='格式中断页面回归：构筑 '+testInfo.project.name;
 const created=await request.post('/api/research',{data:{query,kind:'scout'}});
 const tid=(await created.json()).task_id;
 await expect.poll(async()=>{const task=await (await request.get('/api/research/'+tid)).json();return !!task.report;},{timeout:30000}).toBe(true);
 const task=await (await request.get('/api/research/'+tid)).json();
 const interrupted={...task,status:'partial',report:{...task.report,status:'partial',results:[],errors:['invalid_model_protocol'],outcome:{
  code:'execution_error',title:'本次调查中断，尚不能判断是否匹配',message:'模型返回格式未通过校验，相关判断没有被当作成功。',
  candidate_count:20,investigated_count:0,recommendation_count:0,
 }}};
 await page.route('**/api/research/'+tid,route=>route.fulfill({json:interrupted}));
 await page.goto('/');
 await page.getByRole('button',{name:'任务历史',exact:true}).click();
 await page.locator('.history-row').filter({hasText:query}).click();
 await expect(page.getByRole('heading',{name:'本次调查中断，尚不能判断是否匹配'})).toBeVisible();
 await expect(page.getByRole('status').filter({hasText:'模型返回格式未通过校验'})).toContainText('查到 20 款目录候选');
 await expect(page.getByText('这些条件下还没有合适的候选',{exact:true})).toHaveCount(0);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
