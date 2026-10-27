import { expect, test } from "@playwright/test";
import { writeFileSync } from "node:fs";

test("live workspace creates a settled job and shows its chain evidence", async ({ page, request }) => {
  test.setTimeout(240_000);
  page.setDefaultTimeout(15_000);
  const api = process.env.AGENTFLOW_LIVE_API_URL;
  const token = process.env.AGENTFLOW_LIVE_API_TOKEN;
  const output = process.env.AGENTFLOW_LIVE_RESULT_PATH;
  test.skip(!api || !token || !output, "requires the local Anvil and service cluster fixture");
  const scenario = process.env.AGENTFLOW_LIVE_SCENARIO || "cross_org_analysis";
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  const existingJob = process.env.AGENTFLOW_LIVE_JOB_ID;
  const decision = process.env.AGENTFLOW_LIVE_REVIEW_ACTION || "accept";
  await page.goto(api! + (existingJob ? `/?job=${existingJob}` : ""));
  await page.getByLabel("访问令牌").fill(token!);
  await page.getByRole("button", { name: /连接工作区/ }).click();
  let jobId = existingJob;
  if (!jobId) {
    const headers = { Authorization: `Bearer ${token}` };
    const templates = await (await request.get(`${api}/api/workspace/design-templates`, {headers})).json();
    const capability = scenario === "travel_planning" ? "travel_report" : "report_generation";
    const template = templates.find((t:any)=>t.graph.tasks.some((n:any)=>n.capability===capability));
    expect(template).toBeTruthy();
    await page.getByRole("button",{name:"模板库",exact:true}).click();
    await page.locator('.design-template-grid article').filter({has:page.getByRole('heading',{name:template.name,exact:true})})
      .getByRole('button',{name:'按此模板生成 DAG'}).click();
    await page.getByRole('button',{name:'确认这版任务图',exact:true}).click();
    if (scenario === 'travel_planning') {
      await page.getByText('出游条件',{exact:true}).click();
      for (const [label,value] of [['目的地','杭州'],['出发日期','2026-11-01'],['天数','3'],['人数','2'],['旅行预算（元）','2000']]) {
        await page.getByLabel(label,{exact:true}).fill(value);
      }
    } else {
      const materials=await (await request.get(`${api}/api/workspace/materials`,{headers})).json();
      const material=materials.find((m:any)=>m.scenario==='cross_org_analysis');
      await page.getByText('销售数据',{exact:true}).click();
      await page.getByLabel(material.name,{exact:true}).check();
    }
    await page.getByRole('button',{name:'获取服务报价',exact:true}).click();
    await page.getByRole('button',{name:'选择此方案',exact:true}).first().click();
    const creating=page.waitForResponse(r=>r.url().endsWith('/api/workspace/confirm') && r.request().method()==='POST');
    await page.getByRole('button',{name:'确认并托管服务预算',exact:true}).click();
    const created=await creating;expect(created.ok()).toBeTruthy();
    jobId=(await created.json()).jobId;

  }
  // Keyboard activation also verifies accessible controls on a long report page.
  async function activate(name: string) {
    const button = inbox.getByRole("button", {name, exact: true});
    await button.focus();
    await button.press("Enter");
  }
  const headers = { Authorization: `Bearer ${token}` };
  const inbox = page.getByRole("region", { name: "成果与验收", exact: true });
  await expect(inbox.getByRole("button", { name: "验收通过并结算", exact: true })).toBeVisible({ timeout: 120_000 });
  await expect(inbox.locator(".report-body")).toContainText(scenario === "travel_planning" ? "每日行程" : "统计结果");
  const before = await (await request.get(`${api}/api/jobs/${jobId}`, { headers })).json();
  expect(before.tasks.at(-1).onchain.state).toBe(1);
  const download = page.waitForEvent("download");
  await inbox.getByRole("button", { name: scenario === "travel_planning" ? "下载旅行方案" : "下载销售分析报告", exact: true }).click();
  expect((await download).suggestedFilename()).toMatch(/report\.md$/);
  await page.getByRole("button",{name:"查看任务成果",exact:true}).click();
  const intermediate=page.getByRole("region",{name:"中间成果",exact:true});
  await intermediate.locator("summary").first().click();
  await expect(intermediate.locator(".task-output")).toBeVisible();
  await expect(intermediate.locator(".task-output pre")).toHaveCount(0);
  await page.screenshot({ path: output!.replace(/\.json$/, "-pending.png"), fullPage: true });
  if (decision === "revise") {
    await activate("退回修改");
    await inbox.getByLabel("修改位置", { exact: true }).selectOption(scenario === "travel_planning" ? "费用" : "结论与限制");
    await inbox.getByLabel("修改方式", { exact: true }).selectOption(scenario === "travel_planning" ? "costs" : "clarify");
    await inbox.getByLabel("修改意见", { exact: true }).fill(scenario === "travel_planning" ? "说明住宿和交通分别花了多少钱" : "直接说明销售额最高的分组");
    await activate("提交修改意见");
    await expect(inbox.getByLabel("查看版本", { exact: true })).toHaveValue("1", { timeout: 120_000 });
    await inbox.getByLabel("查看版本", { exact: true }).selectOption("0");
    await expect(inbox.getByRole("button", { name: "验收通过并结算", exact: true })).toHaveCount(0);
    await inbox.getByLabel("查看版本", { exact: true }).selectOption("1");
  }
  if (decision === "reject") {
    await activate("不通过");
    await activate("确认不通过并结束");
    await expect(inbox.getByRole("heading", { name: "验收不通过", exact: true })).toBeVisible();
  } else {
    await activate("验收通过并结算");
    await expect(page.getByRole("heading", { name: "任务结算", exact: true })).toBeVisible();
  }
  await expect.poll(async () => {
    const status = await request.get(`${api}/api/jobs/${jobId}`, { headers });
    expect(status.ok()).toBeTruthy();
    return (await status.json()).status;
  }, { timeout: 180_000, intervals: [500, 1_000, 2_000] }).toBe(decision === "reject" ? "PARTIAL_FAILED" : "SUCCEEDED");

  await page.setViewportSize({width:390,height:844});
  if (decision === "reject") await expect(inbox.locator(".report-body")).toBeVisible();
  else await expect(page.getByRole("region", { name: "任务结算明细", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await page.screenshot({path:output!.replace(/\.json$/, "-mobile.png"), fullPage:true});
  await page.setViewportSize({width:1280,height:720});
  await page.getByRole("button", { name: "刷新数据" }).focus();
  await page.getByRole("button", { name: "刷新数据" }).press("Enter");
  await expect(page.getByText(decision === "reject" ? "部分失败" : "已完成", { exact: true }).filter({visible:true}).first()).toBeVisible();
  const settlements = await request.get(`${api}/api/jobs/${jobId}/settlements`, { headers });
  expect(settlements.ok()).toBeTruthy();
  const events = (await settlements.json()).events as Array<{ event: string; transactionHash: string }>;
  const settled = events.filter((event) => event.event === "TaskSettled");
  expect(settled.length).toBe(decision === "reject" ? 3 : 4);
  expect(settled.every((event) => /^0x[0-9a-f]{64}$/i.test(event.transactionHash))).toBeTruthy();

  await page.getByRole("button", { name: "资金账本" }).click();
  await page.getByText("查看链上交易记录", { exact: true }).click();
  await expect(page.getByRole("columnheader", { name: "交易哈希" })).toBeVisible();
  await expect(page.locator("table tbody tr").filter({ hasText: "TaskSettled" })).toHaveCount(settled.length);
  await expect(page.locator(`button.hash-button[title="${settled[0].transactionHash}"]`)).toBeVisible();
  await page.getByRole("button", { name: "任务成果",exact:true }).click();
  await expect(page.getByRole("heading", { name: "任务成果", exact: true })).toBeVisible();
  await page.locator('.task-list-main:visible').first().click();
  await expect(page.locator('.report-body')).toBeVisible();
  await page.getByRole('button',{name:'服务市场',exact:true}).click();
  await expect(page.getByRole('heading',{name:'服务业务图与商家',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'新增商家',exact:true})).toBeEnabled();
  await expect.poll(()=>page.locator('.planning-graph:visible .react-flow__node').count()).toBeGreaterThan(0);
  expect(pageErrors).toEqual([]);

  writeFileSync(output!, JSON.stringify({ jobId, scenario, status: decision === "reject" ? "PARTIAL_FAILED" : "SUCCEEDED",
    decision, settledTransactionHashes: settled.map((event) => event.transactionHash) }, null, 2) + "\n");
});
