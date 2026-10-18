import { expect, test } from "@playwright/test";
import { writeFileSync } from "node:fs";

test("live workspace creates a settled job and shows its chain evidence", async ({ page, request }) => {
  const api = process.env.AGENTFLOW_LIVE_API_URL;
  const token = process.env.AGENTFLOW_LIVE_API_TOKEN;
  const output = process.env.AGENTFLOW_LIVE_RESULT_PATH;
  test.skip(!api || !token || !output, "requires the local Anvil and service cluster fixture");
  const scenario = process.env.AGENTFLOW_LIVE_SCENARIO || "cross_org_analysis";
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.goto(api!);
  await page.getByLabel("访问令牌").fill(token!);
  await page.getByRole("button", { name: /连接工作区/ }).click();
  await expect(page.getByRole("heading", { name: "协作任务", exact: true })).toBeVisible();
  await page.getByRole("button", { name: /创建协作任务/ }).click();
  await page.getByRole("combobox", { name: "业务场景" }).selectOption(scenario);
  await expect(page.getByRole("heading", { name: "确认任务计划" })).toBeVisible();
  await expect(page.getByText(/\d+ 份输入文件/)).toBeVisible();
  const createdResponse = page.waitForResponse(
    (response) => response.url().endsWith("/api/jobs") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "托管预算并开始" }).click();
  const response = await createdResponse;
  expect(response.ok()).toBeTruthy();
  const { jobId } = await response.json();
  expect(jobId).toEqual(expect.any(String));

  const headers = { Authorization: `Bearer ${token}` };
  await expect.poll(async () => {
    const status = await request.get(`${api}/api/jobs/${jobId}`, { headers });
    expect(status.ok()).toBeTruthy();
    return (await status.json()).status;
  }, { timeout: 180_000, intervals: [500, 1_000, 2_000] }).toBe("SUCCEEDED");

  await page.getByRole("button", { name: "刷新数据" }).click();
  await expect(page.getByText("已完成", { exact: true }).first()).toBeVisible();
  const settlements = await request.get(`${api}/api/jobs/${jobId}/settlements`, { headers });
  expect(settlements.ok()).toBeTruthy();
  const events = (await settlements.json()).events as Array<{ event: string; transactionHash: string }>;
  const settled = events.filter((event) => event.event === "TaskSettled");
  expect(settled.length).toBeGreaterThanOrEqual(4);
  expect(settled.every((event) => /^0x[0-9a-f]{64}$/i.test(event.transactionHash))).toBeTruthy();

  await page.getByRole("button", { name: "资金账本" }).click();
  await expect(page.getByRole("columnheader", { name: "交易哈希" })).toBeVisible();
  await expect(page.locator("table tbody tr").filter({ hasText: "TaskSettled" })).toHaveCount(settled.length);
  await expect(page.locator(`button.hash-button[title="${settled[0].transactionHash}"]`)).toBeVisible();
  await page.getByRole("button", { name: "成果与证据" }).click();
  await expect(page.getByRole("heading", { name: "成果交付与内容承诺" })).toBeVisible();
  await expect(page.getByRole("button", { name: "下载" }).first()).toBeVisible();
  expect(pageErrors).toEqual([]);

  writeFileSync(output!, JSON.stringify({ jobId, scenario, status: "SUCCEEDED",
    settledTransactionHashes: settled.map((event) => event.transactionHash) }, null, 2) + "\n");
});
