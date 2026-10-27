import { expect, test } from '@playwright/test';
import { setup } from './design-fixture';

test('execution scope gaps require opt-in; budget and graph edits invalidate the preview', async ({page}, testInfo) => {
  await setup(page, false);
  let checks=0, scopes=0;
  await page.route('**/api/workspace/designs/design1/execution-check', route => {
    checks++;
    const body=route.request().postDataJSON();
    expect(body.settings.budget).toBe(checks===1?'100':'80');
    return route.fulfill({json:{id:`check${checks}`,status:'capability_gap',canConfirm:true,
      issues:['尚无预测服务；可交付历史分析'],nodes:[{key:'statistics',title:'历史汇总',sourceKeys:['research'],scope:{supported:'按月份统计历史销售',validation:'按原始数据复算'},inputSources:['用户销售 CSV']}],
      gaps:[{reason:'没有预测能力'}]}});
  });
  await page.route('**/api/workspace/designs/design1/execution-confirm', route => {
    expect(route.request().postDataJSON()).toEqual({checkId:'check1',acceptPartial:true});scopes++;
    return route.fulfill({json:{recipe:{outputs:['statistics'],groupBy:'month',budget:'100'}}});
  });
  await page.route('**/api/workspace/comparisons', route=>route.fulfill({json:{options:[],unavailable:[{reason:'当前服务繁忙，稍后重新估价'}]}}));
  await page.getByRole('button',{name:'模板库',exact:true}).click();
  await page.getByRole('button',{name:'按此模板生成 DAG'}).click();
  await page.getByRole('button',{name:'确认这版任务图',exact:true}).click();
  await page.getByRole('button',{name:'获取服务报价',exact:true}).click();
  await expect(page.getByText('尚无预测服务；可交付历史分析')).toBeVisible();
  const scope=page.getByRole('button',{name:'按选定范围获取报价'});
  await expect(scope).toBeDisabled();
  await page.getByLabel(/我同意只执行上面支持的部分/).check();
  await scope.click();
  await expect(page.getByRole('alert')).toContainText('当前服务繁忙');expect(scopes).toBe(1);
  await page.getByLabel('Agent 服务费上限（USDC）').fill('80');
  await expect(scope).toHaveCount(0);
  await page.getByRole('button',{name:'获取服务报价',exact:true}).click();
  await expect(page.getByLabel(/我同意只执行上面支持的部分/)).not.toBeChecked();
  await page.screenshot({path:testInfo.outputPath('execution-conditions.png'),fullPage:true});
  await page.getByText('已确认的任务图 · 2 项工作',{exact:true}).click();
  await page.getByRole('button', {name:'编辑所选任务',exact:true}).click();
  await page.getByLabel('任务名称',{exact:true}).fill('改动后的工作');
  await expect(page.getByRole('button',{name:'获取服务报价',exact:true})).toHaveCount(0);
  await expect(scope).toHaveCount(0);
});

test('arbitrary goal, clarification, outline discussion, explicit DAG expansion, edit and final version', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await setup(page);
  await expect(page.getByRole('heading', { name: '你希望完成什么？' })).toBeVisible();
  await expect(page.getByLabel('任务类型', { exact: true })).not.toBeVisible();
  const send = async (message: string) => {
    await page.getByLabel('告诉 AI 你的目标或修改意见').fill(message);
    await page.getByRole('button', { name: '发送给 AI', exact: true }).click();
  };
  await send('我想办校园读书节');
  await expect(page.getByRole('log')).toContainText('主要面向谁，预算多少？');
  await expect(page.getByRole('button', { name: '先生成草稿 DAG' })).toBeEnabled();
  await send('面向全校，预算一万元');
  await expect(page.getByRole('button', { name: '先生成草稿 DAG' })).toBeEnabled();
  await send('规模太大，先做小规模试点');
  await expect(page.getByRole('heading', { name: '先做小规模试点' })).toBeVisible();
  await page.getByRole('button', { name: '先生成草稿 DAG' }).click();
  await page.getByRole('button', {name:'编辑所选任务',exact:true}).click();
  await page.getByLabel('任务名称', { exact: true }).fill('调查读书兴趣');
  await page.getByRole('button', { name: '保存任务图修改' }).click();
  await page.getByRole('button', {name:'编辑所选任务',exact:true}).click();
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue('调查读书兴趣');
  await page.getByRole('button', { name: '确认这版任务图' }).click();
  await expect(page.getByRole('button', { name: '此版已确认' })).toBeDisabled();
  await page.getByText('保存为可复用模板', { exact: true }).click();
  await page.getByLabel('模板名称', { exact: true }).fill('我的校园策划');
  await page.getByRole('button', { name: '保存到模板库', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('模板已保存');
  await page.getByText('历史确认版本 · 1',{exact:true}).click();
  await page.getByRole('button', { name: '查看第 1 版' }).click();
  await expect(page.getByRole('button', { name: '导出此确认版本' })).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: testInfo.outputPath('desktop-open-planning.png') });
  await page.getByRole('button', { name: '模板库', exact: true }).click();
  await expect(page.getByRole('heading', { name: '我的校园策划' })).toBeVisible();
  expect(errors).toEqual([]);
});

test('templates need no model; dirty edits survive navigation and failed saves', async ({ page }) => {
  const mock = await setup(page, false);
  await page.getByRole('button', { name: '模板库', exact: true }).click();
  await page.getByRole('button', { name: '预览任务图' }).click();
  await expect(page.locator('.design-template-preview').getByText('明确预算和日程')).toBeVisible();
  await page.getByRole('button', { name: '关闭模板预览' }).click();
  await page.getByRole('button', { name: '按此模板生成 DAG' }).click();
  await page.getByRole('button', {name:'编辑所选任务',exact:true}).click();
  await page.getByLabel('任务名称', { exact: true }).fill('保留这段未保存编辑');
  await page.getByRole('button', { name: '模板库', exact: true }).click();
  await expect(page.getByRole('button', { name: '按此模板生成 DAG' })).toBeDisabled();
  await page.getByRole('button', { name: '项目与成果', exact: true }).click();
  await page.getByRole('button',{name:/继续编辑：/}).click();
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue('保留这段未保存编辑');
  mock.failNextEdit();
  await page.getByRole('button', { name: '保存任务图修改' }).click();
  await expect(page.getByRole('alert')).toContainText('任务依赖存在循环');
  await expect(page.getByRole('button', { name: '确认这版任务图' })).toBeDisabled();
  await expect(page.getByRole('button', { name: '保存任务图修改' })).toBeEnabled();
  await page.getByRole('button', { name: '保存任务图修改' }).click();
  await expect(page.getByRole('button', { name: '确认这版任务图' })).toBeEnabled();
  expect(mock.calls()).toBe(0);
});

test('asking in chat renders a DAG without clicking the outline button', async ({ page }) => {
  await setup(page);
  const send = async (text: string) => {
    await page.getByLabel('告诉 AI 你的目标或修改意见').fill(text);
    await page.getByRole('button', { name: '发送给 AI', exact: true }).click();
  };
  await send('想办读书节');
  await expect(page.getByRole('log')).toContainText('主要面向谁');
  await send('面向全校，预算一万元');
  await expect(page.getByRole('button', { name: '先生成草稿 DAG' })).toBeVisible();
  await send('生成DAG呀');
  await expect(page.getByRole('region', { name: '开放任务图编辑器' })).toBeVisible();
  await page.getByRole('button', {name:'编辑所选任务',exact:true}).click();
  await expect(page.getByLabel('任务名称', { exact: true })).toHaveValue('了解学生需求');
  await expect(page.getByRole('log')).toContainText('已生成任务图');
  await expect(page.getByRole('button', { name: '先生成草稿 DAG' })).toHaveCount(0);
});


test('rough first draft can be edited and confirmed even when optional AI review fails', async ({ page }) => {
  await setup(page);
  await page.getByLabel('告诉 AI 你的目标或修改意见').fill('我想办个活动，其他还不清楚');
  await page.getByRole('button', {name:'发送给 AI',exact:true}).click();
  await expect(page.getByRole('region',{name:'开放任务图编辑器'})).toBeVisible();
  await expect(page.getByRole('log')).toContainText('日期以后再补');
  await page.getByText('更多方案操作',{exact:true}).click();
  await page.getByRole('button',{name:'AI 查漏（可选）',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('可继续编辑或直接确认');
  await page.getByRole('button',{name:'确认这版任务图'}).click();
  await expect(page.getByRole('button',{name:'此版已确认'})).toBeDisabled();
});

for (const wide of [false,true]) {
  test(`all nodes fit without panning, after navigation and in large view: ${wide?'wide':'chain'}`, async ({page}, testInfo) => {
    const count = wide ? 24 : 10;
    await setup(page, false, count, wide);
    await page.getByRole('button',{name:'模板库',exact:true}).click();
    await page.getByRole('button',{name:'按此模板生成 DAG'}).click();
    const canvas = page.locator('.design-graph-editor .planning-graph').first();
    const fits = (target: any) => target.evaluate((element: HTMLElement) => {
      const bounds = element.getBoundingClientRect();
      return Array.from(element.querySelectorAll('.react-flow__node')).every(node => {
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.left >= bounds.left - 2 && rect.right <= bounds.right + 2 && rect.top >= bounds.top - 2 && rect.bottom <= bounds.bottom + 2;
      });
    });
    await expect(canvas.locator('.react-flow__node')).toHaveCount(count);
    await expect.poll(() => fits(canvas)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole('button',{name:'模板库',exact:true}).click();
    await page.getByRole('button',{name:'项目与成果',exact:true}).click();
    await page.locator('.draft-collection .task-list-main').first().click();
    await expect.poll(() => fits(canvas)).toBe(true);
    await page.setViewportSize({width:1280,height:900});
    await expect.poll(() => fits(canvas)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole('button',{name:'放大查看 DAG',exact:true}).click();
    const large = page.getByRole('dialog').locator('.planning-graph');
    await expect(large.locator('.react-flow__node')).toHaveCount(count);
    await expect.poll(() => fits(large)).toBe(true);
    await page.screenshot({path:testInfo.outputPath('dag-complete-view.png')});
    await page.getByRole('button',{name:'关闭 DAG 大图'}).click();
    await expect(page.getByRole('dialog')).toHaveCount(0);
  });
}


test('travel graph has visible directed arrows and goes straight from confirmation to quotes', async ({page}, testInfo) => {
  const tasks = [
    {key:'attractions',title:'筛选历史文化景点',capability:'attraction_search',dependencies:[]},
    {key:'hotels',title:'比较两晚住宿',capability:'hotel_search',dependencies:[]},
    {key:'route',title:'安排三日行程',capability:'route_planning',dependencies:['attractions','hotels']},
    {key:'report',title:'生成行程报告',capability:'travel_report',dependencies:['route']},
  ].map(t=>({...t,description:t.title,stageId:'design',inputs:[],outputs:[t.title],acceptance:[],requirementIds:['R1']}));
  const execution = {scenario:'travel_planning',travelRequest:{destination:'杭州',start_date:'2026-11-01',days:3,travelers:2,budget_cents:200000,interests:['culture']},outputs:['report']};
  await setup(page,true,2,false,{title:'杭州三日旅行',tasks,deliverables:[{name:'生成行程报告',taskKey:'report'}],execution});
  let checks=0, confirmations=0, comparisons=0;
  await page.route('**/api/workspace/designs/design1/execution-check',route=>{
    checks++;
    expect(route.request().postDataJSON().settings.travelRequest).toEqual(execution.travelRequest);
    return route.fulfill({json:{id:'ready',status:'ready',issues:[],nodes:[],gaps:[],canConfirm:true,
      recipe:{...execution,budget:'100'}}});
  });
  await page.route('**/api/workspace/designs/design1/execution-confirm',route=>{
    confirmations++;return route.fulfill({json:{recipe:{...execution,budget:'100'}}});
  });
  await page.route('**/api/workspace/comparisons',route=>{
    comparisons++;return route.fulfill({json:{options:[
      {proposal:{id:'fast',budget:'100',estimatedPrice:'70.000000',estimatedSeconds:30,work:[],
        pareto:{estimatedSuccessPercent:'69.55',advantages:['费用最低']},historySummary:{sources:['demo']}}},
      {proposal:{id:'reliable',budget:'100',estimatedPrice:'70.000000',estimatedSeconds:46,work:[],
        pareto:{estimatedSuccessPercent:'98.00',advantages:['参考成功率最高']},historySummary:{sources:['demo']}}},
      ...Array.from({length:10},(_,i)=>({proposal:{id:`tradeoff-${i}`,budget:'100',
        estimatedPrice:String(71+i),estimatedSeconds:29-i,work:[],
        pareto:{estimatedSuccessPercent:String(70+i),advantages:[]},historySummary:{sources:['demo']}}})),
    ]}});
  });
  await page.getByRole('button',{name:'模板库',exact:true}).click();
  await page.getByRole('button',{name:'按此模板生成 DAG'}).click();
  const graph = page.getByRole('region',{name:'开放任务图编辑器'}).getByLabel('子任务依赖图');
  await expect(graph.locator('.dag-arrowhead')).toHaveCount(3);
  const root=await graph.locator('[data-id="attractions"]').boundingBox();
  const parallel=await graph.locator('[data-id="hotels"]').boundingBox();
  const downstream=await graph.locator('[data-id="route"]').boundingBox();
  expect(Math.abs(root!.y-parallel!.y)).toBeLessThan(2);
  expect(downstream!.y).toBeGreaterThan(root!.y+root!.height);
  await page.getByRole('button',{name:'放大查看 DAG'}).click();
  await expect(page.getByRole('dialog').locator('.dag-arrowhead')).toHaveCount(3);
  await page.getByRole('dialog').screenshot({path:testInfo.outputPath('directed-travel-graph.png')});
  await page.getByRole('button',{name:'关闭 DAG 大图'}).click();
  await page.getByRole('button',{name:'确认这版任务图',exact:true}).click();
  await expect(page.getByRole('button',{name:'检查执行条件',exact:true})).toHaveCount(0);
  await expect(page.getByLabel('销售统计维度')).toHaveCount(0);
  expect(checks).toBe(0);
  await page.getByRole('button',{name:'获取服务报价',exact:true}).click();
  await expect(page.getByRole('button',{name:'选择此方案'})).toHaveCount(12);
  await expect(page.getByText('参考成功率 69.55%',{exact:true})).toBeVisible();
  await expect(page.getByText('参考成功率 98.00%',{exact:true})).toBeVisible();
  await expect(page.getByText('成功率含演示履约记录',{exact:true})).toHaveCount(12);
  const first = page.getByRole('article',{name:'方案 1',exact:true});
  const second = page.getByRole('article',{name:'方案 2',exact:true});
  await second.getByRole('button',{name:'选择此方案',exact:true}).click();
  await expect(second).toHaveClass(/is-selected/);
  await expect(second.getByRole('button',{name:'已选择此方案',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(second).toHaveCSS('background-color','rgb(230, 241, 255)');
  await first.getByRole('button',{name:'选择此方案',exact:true}).click();
  await expect(first).toHaveClass(/is-selected/);
  await expect(second).not.toHaveClass(/is-selected/);
  expect([checks,confirmations,comparisons]).toEqual([1,1,1]);
  await page.getByLabel('服务报价与预算').screenshot({path:testInfo.outputPath('direct-quotes.png')});
});

test('a stale backend is identified before another planning request', async ({page}) => {
  await setup(page);
  await page.route('**/api/workspace/planner',route=>route.fulfill({json:{configured:true,flowVersion:'requirements-dag-v1'}}));
  await page.reload();
  await expect(page.getByText('页面已更新，但后台仍是旧版本。请在启动窗口按 R 重启服务，然后刷新页面。')).toBeVisible();
  await page.getByLabel('告诉 AI 你的目标或修改意见').fill('杭州三日游');
  await expect(page.getByRole('button',{name:'发送给 AI',exact:true})).toBeDisabled();
});
