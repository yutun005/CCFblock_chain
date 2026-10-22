import { expect, test } from "@playwright/test";

test("desktop entry explains the new flow and can recheck a previously blank model configuration", async ({ page }, testInfo) => {
  let configured = false;
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/config") return route.fulfill({ json: { scenarios: [], tokenSymbol: "USDC", chainId: 31337 } });
    if (path.endsWith("/planner")) {
      return route.fulfill({ json: { configured, flowVersion: "requirements-dag-v1", message: "请填写模型配置" } });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.getByLabel("访问令牌").fill("test-token");
  await page.getByRole("button", { name: /连接工作区/ }).click();
  await page.getByRole("button", { name: "执行与验收", exact: true }).click();
  await expect(page.getByRole("list", { name: "需求到任务图流程" }).getByRole("listitem")).toHaveCount(4);
  await expect(page.getByRole("heading", { name: "模型需求规划尚未启用", exact: true })).toBeVisible();
  await page.getByText("怎样启用模型规划", { exact: true }).click();
  await expect(page.getByText("planner.local.json", { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("desktop-new-entry.png"), fullPage: true });
  configured = true;
  await page.getByRole("button", { name: "重新检查配置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "模型需求规划已就绪", exact: true })).toBeVisible();
});

test("clarify, refine, confirm requirements, inspect coverage and freeze DAG before pricing", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const task = { key: "statistics", skill: "statistical_analysis", title: "月度统计", dependencies: [], reason: "按月份汇总" };
  const material = { id: "sales", name: "sales.csv", scenario: "cross_org_analysis", preview: { rows: 2 } };
  const requirements = [{ id: "R1", text: "按月份交付统计表，不生成报告", priority: "must", status: "supported", explanation: "月份参数与交付可支持" }];
  const readySpec = { status: "ready", summary: "按月份汇总销售，只交付统计表。", requirements, outputs: ["statistics"], questions: [], limitations: [] };
  let recipe: Record<string, any> = {};
  let intakeCalls = 0, planCalls = 0, reviewCalls = 0, pricingCalls = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().method() === "POST" ? route.request().postDataJSON() : {};
    if (path === "/api/config") return route.fulfill({ json: { scenarios: [], tokenSymbol: "USDC", chainId: 31337 } });
    if (path.endsWith("/planner")) return route.fulfill({ json: { configured: true } });
    if (path.endsWith("/materials")) return route.fulfill({ json: [material] });
    if (path.endsWith("/capabilities")) return route.fulfill({ json: [{ ...task, outputType: "statistics", requires: [], available: true, scenario: "cross_org_analysis", acceptance: "统计可复算" }] });
    if (path.endsWith("/planning/intake")) {
      intakeCalls++;
      if (intakeCalls === 1) {
        recipe = { ...body.recipe, outputs: ["statistics"], groupBy: "month" };
        return route.fulfill({ json: { id: "intake-1", status: "needs_input", recipe: body.recipe, specHash: "s1", spec: { ...readySpec,
          status: "needs_input", questions: [{ id: "Q1", question: "需要文字报告吗？", reason: "决定最终交付", options: ["只要统计表", "需要报告"] }] } } });
      }
      if (intakeCalls === 2) {
        expect(body.previousId).toBe("intake-1"); expect(body.answers.Q1).toBe("只要统计表");
      } else {
        expect(body.feedback).toBe("继续只要统计表");
      }
      return route.fulfill({ json: { id: `intake-${intakeCalls}`, status: "ready", recipe: body.recipe, specHash: "s2", spec: readySpec } });
    }
    if (/\/planning\/intake\/.*\/confirm$/.test(path)) {
      expect(body.specHash).toBe("s2");
      return route.fulfill({ json: { id: "confirmed", status: "ready", confirmedAt: 1, recipe, confirmedRecipe: recipe, spec: readySpec, specHash: "s2" } });
    }
    if (path.endsWith("/planning")) {
      expect(body.intakeId).toBe("confirmed"); expect(body.groupBy).toBe("month");
      planCalls++;
      recipe = { ...body, planningId: "dag" }; delete recipe.intakeId;
      return route.fulfill({ json: { id: "dag", status: "ready", mode: "llm_dag", recipe, intakeId: "confirmed", requirements: readySpec,
        draft: { status: "ready", summary: readySpec.summary, outputs: ["statistics"], tasks: [task], groupBy: "month", tier: "standard" },
        tasks: [task], coverage: [{ ...requirements[0], taskKeys: ["statistics"], check: "program", actual: "month" }], semanticAudit: { status: "pass" } } });
    }
    if (path.endsWith("/planning/dag/review")) {
      reviewCalls++;
      return route.fulfill({ json: { id: "frozen", status: "ready", mode: "llm_dag", recipe: { ...recipe, planningId: "frozen" },
        reviewedAt: 1, draft: body.draft } });
    }
    if (path.endsWith("/comparisons")) { pricingCalls++; return route.fulfill({ json: { options: [], unavailable: [] } }); }
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.getByLabel("访问令牌").fill("test-token");
  await page.getByRole("button", { name: /连接工作区/ }).click();
  await page.getByRole("button", { name: "执行与验收", exact: true }).click();
  await page.getByLabel("任务目标", { exact: true }).fill("汇总销售数据，按月份");
  await page.getByRole("checkbox", { name: /sales.csv/ }).check();
  await page.getByRole("button", { name: "生成任务方案", exact: true }).click();
  await expect(page.getByRole("heading", { name: "理解需求与核对能力" })).toBeVisible();
  await expect(page.getByRole("button", { name: "确认需求并生成任务图" })).toHaveCount(0);
  await page.getByRole("button", { name: "只要统计表", exact: true }).click();
  await page.getByRole("button", { name: "提交补充并更新需求", exact: true }).click();
  await expect(page.getByRole("heading", { name: "核对我们理解的需求" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("requirements-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "确认需求并生成任务图" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("requirements-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  expect(planCalls).toBe(0); expect(pricingCalls).toBe(0);
  await page.getByRole("button", { name: "确认需求并生成任务图" }).click();
  await expect(page.getByRole("heading", { name: "需求怎样落实到方案" })).toBeVisible();
  await expect(page.getByRole("button", { name: "查看执行方案", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "返回需求澄清与调整" }).click();
  await page.getByLabel("需要补充或调整的要求（可选）").fill("继续只要统计表");
  await expect(page.getByRole("button", { name: "确认需求并生成任务图" })).toBeDisabled();
  await page.getByRole("button", { name: "提交补充并更新需求", exact: true }).click();
  await page.getByRole("button", { name: "确认需求并生成任务图" }).click();
  await expect(page.getByRole("heading", { name: "编辑任务方案", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "确认任务方案", exact: true }).click();
  await expect(page.getByRole("button", { name: "查看执行方案", exact: true })).toBeEnabled();
  expect(reviewCalls).toBe(1); expect(planCalls).toBe(2); expect(pricingCalls).toBe(0); expect(errors).toEqual([]);
});
