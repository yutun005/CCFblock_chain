import { expect, test } from "@playwright/test";

const token = "browser-e2e-token";
const jobId = "job-browser-e2e-1";
const planHash = "0x" + "ab".repeat(32);

function settlementJob(id: string) {
  return {
    jobId: id, scenario: "travel_planning", goal: "杭州三日游", status: "ACTIVE",
    planningMode: "model", domain: {}, token: "USDC", createdAt: 1,
    chain: { deposit: "100000000", settledAmount: "50000000", agentCredits: "50000000",
      withdrawn: "0", refundCredit: "0", refunded: "0", reserved: "9000000",
      unallocated: "41000000", finalized: false },
    tasks: ["筛选景点", "比较住宿", "安排行程", "生成旅行方案"].map((title, i) => ({
      taskId: i, key: ["attractions", "hotels", "route", "report"][i], title,
      dependencies: i === 2 ? ["attractions", "hotels"] : i === 3 ? ["route"] : [],
      onchain: { state: i < 3 ? 2 : 1, attempt: 0, acceptedPrice: ["18000000", "12000000", "20000000", "9000000"][i], config: { budgetCap: "30000000" } },
      checkpoint: { status: i < 3 ? "SETTLED" : "AWAITING_REVIEW" },
    })),
  };
}

test("report previews hide fingerprints while download and acceptance keep original references", async ({ page }, testInfo) => {
  const digest = "0x" + "cd".repeat(32);
  const report = `# 业务报告\n总销售额：1500 元\n统计成果 SHA-256：${digest}\n统计来源：${digest}\n行程成果 SHA-256：${digest}\n原始资料 SHA-256：${digest}\n资料来源：用户上传的订单\n`;
  const chart = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="80"><text x="10" y="20">销售额 1500 元</text><text x="10" y="60">来源：${digest}</text></svg>`;
  const html = `<html><body><pre>${report}</pre>${chart}<table><tr><td>项目</td><td>数值（分）</td><td>来源 SHA-256</td></tr><tr><td>销售额</td><td>150000</td><td>${digest}</td></tr></table></body></html>`;
  const review = {
    id: "review-1", jobId: "review-job", status: "AWAITING_REVIEW", provider: "report-a",
    deadline: Date.now() / 1000 + 3600, price: "10000000", changes: [],
    decisions: [{ request: { action: "revise", resultHash: digest, feedback: "保留业务说明" } }],
    drafts: [{ resultHash: digest, checks: ["source_numbers_and_hashes"] }],
  };
  const recipe = { title: "报告预览", scenario: "cross_org_analysis", outputs: ["report"], groupBy: "month" };
  const version = {
    id: "v-preview", projectId: "project-preview", number: 1, recipe, status: "ACTIVE",
    estimatedPrice: "10", materials: [{ name: "销售数据.csv" }], deliverables: ["report.md"],
    reviews: [review], files: [{ name: "report.md", sha256: digest }],
  };
  let accepted: Record<string, unknown> | undefined;
  let downloaded = false;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/config") return route.fulfill({ json: { scenarios: [], tokenSymbol: "USDC", chainId: 31337 } });
    if (path === "/api/jobs/review-job") return route.fulfill({ json: settlementJob("review-job") });
    if (path === "/api/jobs") return route.fulfill({ json: [{ jobId: "review-job", status: "ACTIVE" }] });
    if (path.endsWith("/settlements")) return route.fulfill({ json: { events: [] } });
    if (path.endsWith("/events")) return route.fulfill({ body: "", contentType: "text/event-stream" });
    if (path.endsWith("/planner")) return route.fulfill({ json: { configured: false } });
    if (path.endsWith("/projects")) return route.fulfill({ json: [{ id: version.projectId, title: recipe.title, versions: [{ ...version, createdAt: 1 }] }] });
    if (path.endsWith(`/versions/${version.id}`)) return route.fulfill({ json: version });
    if (path.endsWith(`/drafts/${digest}`)) return route.fulfill({ json: { files: [
      { name: "report.md", text: report }, { name: "revenue.svg", text: chart }, { name: "report.html", text: html },
      { name: "references.json", text: JSON.stringify({ source: digest, input_hash: digest, total_revenue_cents: 150000, nested: { sha256: digest, note: "订单资料" } }) },
      { name: "source-comparison.csv", text: `项目,数值（分）,来源 SHA-256\r\n"销售额,合计",150000,${digest}\r\n` },
    ] } });
    if (path.endsWith(`/files/${digest}`)) {
      downloaded = true;
      return route.fulfill({ body: report, contentType: "text/markdown" });
    }
    if (path.endsWith("/reviews/review-1") && route.request().method() === "POST") {
      accepted = route.request().postDataJSON();
      review.status = "ACCEPTED";
      return route.fulfill({ json: review });
    }
    if (path.endsWith("/reviews/review-job")) return route.fulfill({ json: [review] });
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.getByLabel("访问令牌").fill(token);
  await page.getByRole("button", { name: /连接工作区/ }).click();
  await page.locator(".task-list-main").first().click();
  await expect(page.locator(".report-body")).toContainText("总销售额：1500 元");
  await expect(page.locator(".report-body")).toContainText("资料来源：用户上传的订单");
  await expect(page.locator(".report-body")).not.toContainText(/SHA.?256|0xcdcd/i);
  const svg = decodeURIComponent((await page.locator(".report-chart").getAttribute("src"))!.split(",").slice(1).join(","));
  expect(svg).toContain("销售额 1500 元");
  expect(svg).not.toContain(digest);
  await page.getByText("预览离线网页报告", { exact: true }).click();
  const frame = page.frameLocator('iframe[title="离线网页报告"]');
  await expect(frame.locator("body")).toContainText("总销售额：1500 元");
  await expect(frame.locator("body")).not.toContainText(/SHA.?256|0xcdcd/i);
  await expect(frame.locator("tr").last().locator("td")).toHaveCount(2);
  await page.getByText("关联数据、引用资料与版本记录", { exact: true }).click();
  await page.getByText("查看系统检查与验收记录", { exact: true }).click();
  await expect(page.getByRole("region",{name:"成果与验收",exact:true})).not.toContainText(/SHA.?256|0xcdcd|resultHash/i);
  await expect(page.locator(".draft-preview")).toContainText("销售额,合计");
  await expect(page.locator(".draft-preview")).toContainText("1500.00 元");
  await expect(page.getByText(/保留业务说明/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("report-without-fingerprints.png"), fullPage: true });
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载销售分析报告", exact: true }).focus();
  await page.getByRole("button", { name: "下载销售分析报告", exact: true }).press("Enter");
  expect((await downloadEvent).suggestedFilename()).toBe("report.md");

  await page.getByRole("button", { name: "验收通过并结算", exact: true }).click();
  await expect.poll(() => accepted).toMatchObject({ resultHash: digest, action: "accept" });
  await expect(page.getByRole("heading", { name: "任务结算", exact: true })).toBeVisible();
  await expect(page).toHaveURL(/job=review-job&view=funds/);
  expect(errors).toEqual([]);
});

test("acceptance opens settlement, follows confirmation and refund, and survives reload", async ({ page }, testInfo) => {
  const id = "settlement-job";
  const job = settlementJob(id);
  const review = {
    id: "settlement-review", jobId: id, status: "AWAITING_REVIEW", price: "9000000",
    deadline: Date.now() / 1000 + 3600, changes: [], decisions: [],
    drafts: [{ resultHash: planHash, checks: [] }],
  };
  let failAcceptance = true, failRefund = true, decisions = 0, claims = 0;
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/config") return route.fulfill({ json: { scenarios: [], chainId: 31337, tokenSymbol: "USDC" } });
    if (path === "/api/jobs") return route.fulfill({ json: [{ jobId: id, title: "杭州三日游", status: job.status }] });
    if (path === `/api/jobs/${id}`) return route.fulfill({ json: job });
    if (path.endsWith("/planner")) return route.fulfill({ json: { configured: false } });
    if (path.endsWith("/settlements")) return route.fulfill({ json: { events: [] } });
    if (path.endsWith("/events")) return route.fulfill({ body: "", contentType: "text/event-stream" });
    if (path.endsWith(`/reviews/${id}`)) return route.fulfill({ json: [review] });
    if (path.endsWith(`/drafts/${planHash}`)) return route.fulfill({ json: { files: [{ name: "travel-report.md", text: "# 旅行方案\n杭州三日行程与住宿安排。" }] } });
    if (path.endsWith("/reviews/settlement-review") && route.request().method() === "POST") {
      decisions++;
      if (failAcceptance) return route.fulfill({ status: 409, json: { detail: "验收暂未提交，请重试" } });
      review.status = "ACCEPTED";
      job.tasks[3].checkpoint.status = "SETTLING";
      return route.fulfill({ json: review });
    }
    if (path.endsWith("/refund") && route.request().method() === "POST") {
      claims++;
      if (failRefund) return route.fulfill({ status: 503, json: {detail:"连接暂时中断"} });
      Object.assign(job.chain, { refundCredit: "0", refunded: "41000000" });
      return route.fulfill({ json: job });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto(`/?job=${id}`);
  await page.getByLabel("访问令牌").fill(token);
  await page.getByRole("button", { name: /连接工作区/ }).click();
  const accept = page.getByRole("button", { name: "验收通过并结算", exact: true });
  await accept.focus();
  await accept.press("Enter");
  await expect(page.getByRole("alert")).toContainText("验收暂未提交，请重试");
  await expect(page.getByRole("heading", { name: "杭州三日游", exact: true })).toBeVisible();
  expect(claims).toBe(0);
  failAcceptance = false;
  await accept.focus();
  await accept.press("Enter");
  await expect(page.getByRole("heading", { name: "任务结算", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "任务结算", exact: true })).toBeFocused();
  await expect(page).toHaveURL(/job=settlement-job&view=funds/);
  await expect(page.getByRole("region", { name: "成果与验收", exact: true })).toHaveCount(0);
  const panel = page.getByRole("region", { name: "任务结算明细", exact: true });
  await expect(panel.getByRole("heading", { name: "结算进行中", exact: true })).toBeVisible();
  const refund = page.getByRole("region", { name: "领取退还代币", exact: true });
  const claim = refund.getByRole("button");
  await expect(claim).toHaveText("领取退还代币");
  await expect(claim).toBeDisabled();
  await expect(page.locator(".metric").filter({ hasText: "实际服务费" })).toContainText("50.00");
  expect(decisions).toBe(2);
  expect(claims).toBe(0);

  // The acceptance response is not proof of payment; only the next chain snapshot changes totals.
  job.status = "SUCCEEDED";
  job.tasks[3].onchain.state = 2;
  job.tasks[3].checkpoint.status = "SETTLED";
  Object.assign(job.chain, { finalized: true, settledAmount: "59000000", agentCredits: "59000000", refundCredit: "41000000", reserved: "0", unallocated: "0" });
  await expect(panel.getByRole("heading", { name: "结算完成", exact: true })).toBeVisible({ timeout: 12_000 });
  await expect(page.locator(".metric").filter({ hasText: "实际服务费" })).toContainText("59.00");
  await expect(refund).toContainText("剩余 41.00 代币");
  await expect(page.getByText("可领取退款", {exact: true})).toHaveCount(0);
  await expect(page.getByRole("table", { name: "各项服务费用" }).getByRole("row").filter({ hasText: "生成旅行方案" })).toContainText("9.00");
  await expect(claim).toBeEnabled();
  expect(claims).toBe(0);
  await page.screenshot({ path: testInfo.outputPath("settlement-desktop.png"), fullPage: true });
  await page.reload();
  await expect(page.getByRole("heading", { name: "任务结算", exact: true })).toBeVisible();
  await expect(claim).toBeEnabled();
  await claim.focus();
  await claim.press("Enter");
  await expect(refund.getByRole("alert")).toContainText("连接暂时中断");
  await expect(claim).toBeEnabled();
  expect(job.chain.refunded).toBe("0");
  failRefund = false;
  await claim.click();
  await expect(page.locator(".metric").filter({ hasText: "已退还用户" })).toContainText("41.00");
  await expect(refund).toContainText("已退还 41.00 代币到你的钱包。");
  await expect(claim).toHaveText("已领取退还代币");
  await expect(claim).toBeDisabled();
  expect(claims).toBe(2);
  expect(job.chain.agentCredits).toBe("59000000");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("settlement-mobile.png"), fullPage: true });
  await panel.getByRole("button", { name: "查看任务成果", exact: true }).click();
  await expect(page.locator(".report-body")).toBeVisible();
  const reopen = page.getByRole("button", { name: "查看结算", exact: true });
  await reopen.focus();
  await reopen.press("Enter");
  await expect(page.getByRole("heading", { name: "任务结算", exact: true })).toBeVisible();
  expect(decisions).toBe(2);
  expect(errors).toEqual([]);
});

test("fully spent budget has no refund to claim", async ({ page }) => {
  const job = settlementJob("spent-job");
  job.status = "SUCCEEDED";
  Object.assign(job.chain, {finalized:true, settledAmount:"100000000",agentCredits:"100000000",reserved:"0",unallocated:"0"});
  job.tasks.forEach(task => {task.onchain.state=2; task.checkpoint.status="SETTLED";});
  let refundCalls = 0;
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if(path === "/api/config") return route.fulfill({json:{scenarios:[],chainId:31337,tokenSymbol:"USDC"}});
    if(path === "/api/jobs") return route.fulfill({json:[{jobId:job.jobId,status:job.status}]});
    if(path === `/api/jobs/${job.jobId}`) return route.fulfill({json:job});
    if(path.endsWith("/refund")) refundCalls++;
    if(path.endsWith("/events")) return route.fulfill({body:"",contentType:"text/event-stream"});
    if(path.endsWith("/settlements")) return route.fulfill({json:{events:[]}});
    return route.fulfill({json:[]});
  });
  await page.goto(`/?job=${job.jobId}&view=funds`);
  await page.getByLabel("访问令牌").fill(token);
  await page.getByRole("button",{name:/连接工作区/}).click();
  const refund = page.getByRole("region",{name:"领取退还代币",exact:true});
  await expect(refund).toContainText("本次预算已全部用于服务，无剩余代币。");
  await expect(refund.getByRole("button",{name:"无剩余代币",exact:true})).toBeDisabled();
  expect(refundCalls).toBe(0);
});
