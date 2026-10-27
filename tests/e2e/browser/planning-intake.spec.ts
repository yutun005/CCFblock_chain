import { expect, test } from '@playwright/test';
import { setup } from './design-fixture';

test('new task has one goal entry; configuration can be rechecked without changing views', async ({page}, testInfo) => {
  await setup(page, false);
  await expect(page.getByRole('heading',{name:'你希望完成什么？'})).toBeVisible();
  await expect(page.locator('.design-canvas')).toBeHidden();
  await expect(page.getByText('模型尚未配置',{exact:true})).toBeVisible();
  await page.route('**/api/workspace/planner',route=>route.fulfill({json:{configured:true}}));
  await page.getByRole('button',{name:'重新检查模型配置'}).click();
  await page.getByLabel('告诉 AI 你的目标或修改意见').fill('帮我安排三天杭州旅行');
  await expect(page.getByRole('button',{name:'发送给 AI',exact:true})).toBeEnabled();
  await page.screenshot({path:testInfo.outputPath('new-task-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('new-task-mobile.png'),fullPage:true});
});

test('sales data upload and budget authorization lead from the confirmed design into execution', async ({page}) => {
  await setup(page,false,2,false,{execution:{scenario:'cross_org_analysis',outputs:['report']}});
  let material:any=null, created=false, confirmed=0;
  const recipe={scenario:'cross_org_analysis',outputs:['report'],budget:'100',materialIds:['sales']};
  await page.route('**/api/workspace/materials',async route=>{
    if(route.request().method()==='POST'){
      const body=route.request().postDataJSON();
      expect(body.scenario).toBe('cross_org_analysis');
      expect(body.content).toContain('organization,order_id');
      material={id:'sales',name:body.name,scenario:'cross_org_analysis'};
      return route.fulfill({json:material});
    }
    return route.fulfill({json:material?[material]:[]});
  });
  await page.route('**/api/workspace/designs/design1/execution-check',route=>{
    expect(route.request().postDataJSON().settings).toMatchObject({materialIds:['sales'],budget:'100'});
    return route.fulfill({json:{id:'ready',status:'ready',canConfirm:true,recipe,gaps:[],issues:[],nodes:[]}});
  });
  await page.route('**/api/workspace/designs/design1/execution-confirm',route=>route.fulfill({json:{recipe}}));
  await page.route('**/api/workspace/comparisons',route=>route.fulfill({json:{options:[{proposal:{id:'price',confirmationHash:'frozen',budget:'100',estimatedPrice:'59',estimatedSeconds:30,work:[]}}]}}));
  await page.route('**/api/workspace/confirm',route=>{
    confirmed++;
    expect(route.request().postDataJSON()).toEqual({proposalId:'price',confirmationHash:'frozen'});
    created=true;return route.fulfill({json:{jobId:'sales-job'}});
  });
  await page.route('**/api/jobs',route=>route.fulfill({json:created?[{jobId:'sales-job',title:'我的销售分析',designId:'design1',status:'ACTIVE'}]:[]}));
  await page.route('**/api/jobs/sales-job',route=>route.fulfill({json:{jobId:'sales-job',goal:'我的销售分析',status:'ACTIVE',tasks:[],chain:{deposit:'100000000',agentCredits:'0',withdrawn:'0',refunded:'0',refundCredit:'0',finalized:false}}}));
  await page.route('**/api/jobs/sales-job/settlements',route=>route.fulfill({json:{events:[]}}));
  await page.getByRole('button',{name:'模板库',exact:true}).click();
  await page.getByRole('button',{name:'按此模板生成 DAG'}).click();
  await expect(page.getByRole('button',{name:'获取服务报价',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'确认这版任务图',exact:true}).click();
  await expect(page.locator('.design-graph-section')).not.toHaveAttribute('open','');
  await page.getByText('销售数据',{exact:true}).click();
  await page.getByLabel('上传销售 CSV').setInputFiles({name:'我的销售.csv',mimeType:'text/csv',buffer:Buffer.from('organization,order_id,date,category,quantity,unit_price_cents\n甲,1,2026-10-01,book,2,500')});
  await expect(page.getByLabel('我的销售.csv',{exact:true})).toBeChecked();
  await page.getByRole('button',{name:'获取服务报价',exact:true}).click();
  await page.getByRole('button',{name:'选择此方案',exact:true}).click();
  expect(confirmed).toBe(0);
  await page.getByRole('button',{name:'确认并托管服务预算',exact:true}).click();
  await expect(page.getByRole('heading',{name:'我的销售分析',exact:true})).toBeVisible();
  await expect(page).toHaveURL(/job=sales-job/);
  await page.getByRole('button',{name:'项目与成果',exact:true}).click();
  await expect(page.locator('.task-list-main')).toHaveCount(1);
  await expect(page.locator('.draft-collection')).toHaveCount(0);
  expect(confirmed).toBe(1);
});
