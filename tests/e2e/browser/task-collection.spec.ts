import { expect, test } from '@playwright/test';

test('task list opens one whole request; final reports and intermediate files stay in that request', async ({page},testInfo) => {
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  const jobs=['杭州三日旅行','苏州两日旅行'].map((title,i)=>({jobId:`job-${i}`,title,goal:title,status:'SUCCEEDED',createdAt:1793000000+i*3600,
    plan:{tasks:[{key:'attractions'},{key:'report'}]},finalTaskKeys:['report'],
    chain:{deposit:'100000000',agentCredits:'59000000',withdrawn:'0',refunded:'0',refundCredit:'41000000',finalized:true},
    tasks:[{key:'attractions',taskId:0,title:'筛选景点',dependencies:[],onchain:{state:2,attempt:0,acceptedPrice:'18000000',resultHash:`places-${i}`,config:{}},checkpoint:{status:'SETTLED'}},
      {key:'report',taskId:1,title:'生成旅行方案',dependencies:['attractions'],onchain:{state:2,attempt:0,acceptedPrice:'9000000',resultHash:`report-${i}`,config:{}},checkpoint:{status:'SETTLED'}}]}));
  let outputRequests:string[]=[];
  await page.route('**/api/**',route=>{
    const path=new URL(route.request().url()).pathname;
    if(path==='/api/config')return route.fulfill({json:{scenarios:[],tokenSymbol:'USDC'}});
    if(path==='/api/jobs')return route.fulfill({json:jobs});
    if(path.endsWith('/planner'))return route.fulfill({json:{configured:true}});
    for(let i=0;i<2;i++){
      const id=`job-${i}`;
      if(path===`/api/jobs/${id}`)return route.fulfill({json:jobs[i]});
      if(path.endsWith(`/reviews/${id}`))return route.fulfill({json:[{id:`review-${i}`,jobId:id,taskId:1,status:'ACCEPTED',price:'9000000',deadline:1794000000,changes:[],decisions:[],drafts:[{resultHash:`report-${i}`,checks:[]}]}]});
      if(path.endsWith(`/drafts/report-${i}`))return route.fulfill({json:{files:[{name:'travel-report.md',text:`# ${jobs[i].title}\n完整旅行方案，仅属于${id}。`}]}});
      if(path===`/api/jobs/${id}/artifacts`)return route.fulfill({json:[
        {resultHash:`places-${i}`,manifest:{taskId:0,artifacts:[{name:'attractions.json'}]}},
        {resultHash:`report-${i}`,manifest:{taskId:1,artifacts:[{name:'travel-report.md'}]}}]});
      if(path.startsWith(`/api/workspace/jobs/${id}/tasks/`)){
        outputRequests.push(path);
        return route.fulfill({json:{stage:'delivered',files:[{name:'places.md',text:`# ${jobs[i].title}景点\n${i?'苏州博物馆':'杭州西湖'}`} ]}});
      }
    }
    if(path.endsWith('/settlements'))return route.fulfill({json:{events:[]}});
    if(path.endsWith('/events'))return route.fulfill({body:'',contentType:'text/event-stream'});
    return route.fulfill({json:[]});
  });
  await page.setViewportSize({width:1440,height:1000});
  await page.goto('/');
  await page.getByLabel('访问令牌').fill('test');
  await page.getByRole('button',{name:/连接工作区/}).click();
  const nav=page.locator('.sidebar nav');
  await expect(nav.getByRole('button')).toHaveCount(5);
  await expect(nav.getByRole('button',{name:/执行与验收|协作任务|成果与证据/})).toHaveCount(0);
  await expect(page.getByRole('heading',{name:'项目与成果',exact:true})).toBeVisible();
  await expect(page.locator('.task-list:visible > li')).toHaveCount(2);
  await page.getByRole('button',{name:'任务成果',exact:true}).click();
  await expect(page.locator('.task-list:visible > li')).toHaveCount(2);
  await expect(page.locator('.task-output')).toHaveCount(0);
  await page.screenshot({path:testInfo.outputPath('task-results-list-desktop.png'),fullPage:true});
  await page.getByRole('searchbox').fill('杭州');
  await expect(page.locator('.task-list:visible > li')).toHaveCount(1);
  await page.locator('.task-list-main:visible').focus();await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/job=job-0/);
  await expect(page.locator('.review-inbox .report-body')).toContainText('杭州三日旅行');
  await expect(page.locator('.review-inbox .report-body')).not.toContainText('苏州');
  const intermediate=page.getByRole('region',{name:'中间成果',exact:true});
  await expect(intermediate.locator(':scope > .content-section')).toHaveCount(1);
  await expect(page.locator('details,summary,[aria-expanded]')).toHaveCount(0);
  await expect(page.getByRole('region',{name:'最终交付文件',exact:true})).toHaveCount(0);
  expect(outputRequests).toContain('/api/workspace/jobs/job-0/tasks/0/outputs');
  await expect(intermediate.locator('.task-output')).toContainText('杭州西湖');
  const download=page.waitForEvent('download');
  await page.getByRole('button',{name:'下载旅行方案',exact:true}).click();
  expect((await download).suggestedFilename()).toBe('travel-report.md');
  await page.screenshot({path:testInfo.outputPath('whole-task-results-desktop.png'),fullPage:true});
  await page.reload();
  await expect(page.locator('.review-inbox .report-body')).toContainText('杭州三日旅行');
  await page.getByRole('button',{name:'← 全部任务成果',exact:true}).click();
  await page.locator('.task-list-main:visible').filter({hasText:'苏州两日旅行'}).click();
  await expect(page.locator('.review-inbox .report-body')).toContainText('苏州两日旅行');
  await expect(page.locator('.review-inbox .report-body')).not.toContainText('杭州');
  await expect(intermediate.locator('.task-output')).toContainText('苏州博物馆');
  expect(outputRequests).toContain('/api/workspace/jobs/job-1/tasks/0/outputs');
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('whole-task-results-mobile.png'),fullPage:true});
  await page.getByRole('button',{name:'← 全部任务成果',exact:true}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('task-results-list-mobile.png'),fullPage:true});
  expect(errors).toEqual([]);
});
