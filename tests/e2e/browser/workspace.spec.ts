import { expect, test } from "@playwright/test";

const token = "browser-e2e-token";
const jobId = "job-browser-e2e-1";
const planHash = "0x" + "ab".repeat(32);

test("admin can connect, preview a plan, and start a collaboration job", async ({ page }) => {
  let created = false;
  let createdBody: Record<string, unknown> | undefined;
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname;

    expect(request.headers().authorization).toBe(`Bearer ${token}`);

    if (method === "GET" && path === "/api/config") {
      return route.fulfill({
        json: {
          mode: "SELF_HOSTED",
          chainId: 31337,
          escrow: "0x0000000000000000000000000000000000000001",
          token: "0x0000000000000000000000000000000000000002",
          owner: "0x0000000000000000000000000000000000000003",
          tokenSymbol: "TEST",
          scenarios: [
            { id: "cross_org_analysis", title: "跨组织数据分析", description: "固定合成数据分析" },
            { id: "travel_planning", title: "旅游规划", description: "固定合成旅游规划" },
          ],
        },
      });
    }

    if (method === "GET" && path === "/api/jobs") {
      return route.fulfill({ json: created ? [{ jobId, status: "ACTIVE" }] : [] });
    }

    if (method === "GET" && path === "/api/agents") {
      return route.fulfill({ json: [] });
    }

    if (method === "POST" && path === "/api/plans/preview") {
      const body = request.postDataJSON();
      expect(body.scenario).toBe("cross_org_analysis");
      expect(body.deposit).toBe("100");
      expect(body.slaSeconds).toBe(30);
      return route.fulfill({
        json: {
          planHash,
          plan: {
            tasks: [
              { key: "clean", title: "数据清洗", budget_cap: "30" },
              { key: "report", title: "生成报告", budget_cap: "60" },
            ],
          },
          inputs: { files: [{ rows: 2 }, { rows: 1 }] },
        },
      });
    }

    if (method === "POST" && path === "/api/jobs") {
      createdBody = request.postDataJSON();
      created = true;
      return route.fulfill({ json: { jobId, mode: "deterministic_template", planHash } });
    }

    if (method === "GET" && path === `/api/jobs/${jobId}`) {
      return route.fulfill({
        json: {
          jobId,
          scenario: "cross_org_analysis",
          goal: "固定合成数据分析",
          status: "ACTIVE",
          planningMode: "deterministic_template",
          chain: {
            deposit: "100000000",
            agentCredits: "0",
            withdrawn: "0",
            refundCredit: "0",
            refunded: "0",
            reserved: "60000000",
            unallocated: "40000000",
            finalized: false,
          },
          tasks: [
            {
              taskId: 0,
              key: "clean",
              title: "数据清洗",
              dependencies: [],
              onchain: { state: 0, attempt: 0, acceptedPrice: "0", config: { budgetCap: "30000000" } },
              checkpoint: { status: "READY", selected: null, verification: null },
            },
          ],
          domain: {},
          token: "TEST",
          createdAt: 1,
        },
      });
    }

    if (method === "GET" && path === `/api/jobs/${jobId}/quotes`) {
      return route.fulfill({ json: [] });
    }
    if (method === "GET" && path === `/api/jobs/${jobId}/audit`) {
      return route.fulfill({ json: [] });
    }
    if (method === "GET" && path === `/api/jobs/${jobId}/artifacts`) {
      return route.fulfill({ json: [] });
    }
    if (method === "GET" && path === `/api/jobs/${jobId}/settlements`) {
      return route.fulfill({ json: { events: [] } });
    }
    if (method === "GET" && path === `/api/jobs/${jobId}/events`) {
      return route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: "",
      });
    }

    return route.fulfill({ status: 404, json: { detail: `Unhandled ${method} ${path}` } });
  });

  await page.goto("/");
  await page.getByLabel("访问令牌").fill(token);
  await page.getByRole("button", { name: /连接工作区/ }).click();
  await page.getByRole("button", { name: "协作任务", exact: true }).click();
  await expect(page.getByRole("heading", { name: "协作任务", exact: true })).toBeVisible();

  await page.getByRole("button", { name: /查看计划并开始/ }).click();
  await expect(page.getByRole("heading", { name: "确认任务计划" })).toBeVisible();
  await expect(page.getByText("2 份输入文件")).toBeVisible();
  await expect(page.getByText(/节点预算合计 90\.00 代币/)).toBeVisible();

  await page.getByRole("button", { name: "托管预算并开始" }).click();
  await expect(page.getByRole("heading", { name: "跨组织数据分析" })).toBeVisible();
  await expect(page.getByTestId("rf__node-clean").getByText("数据清洗", { exact: true })).toBeVisible();
  expect(createdBody).toMatchObject({
    scenario: "cross_org_analysis",
    deposit: "100",
    slaSeconds: 30,
    expectedPlanHash: planHash,
  });
  expect(createdBody?.requestId).toEqual(expect.any(String));
  expect(pageErrors).toEqual([]);
});

for (const scenario of ["travel_planning", "cross_org_analysis"]) {
  test(`${scenario}: template preview and editable task graph stay consistent`, async ({ page }, testInfo) => {
    const travel = scenario === "travel_planning";
    const keys = travel ? ["attractions", "hotels", "route", "report"] : ["clean", "validate", "statistics", "report"];
    const names = travel ? ["景点推荐", "酒店推荐", "每日行程", "完整报告"] : ["清洗后的数据", "质量检查结果", "统计结果", "完整报告"];
    const skills = travel ? ["attraction_search", "hotel_search", "route_planning", "travel_report"] : ["data_cleaning", "data_validation", "statistical_analysis", "report_generation"];
    const tasks = keys.map((key, i) => ({ key, title: names[i], skill: skills[i], reason: "为目标提供成果", dependencies: i === 2 ? keys.slice(0, 2) : i === 3 ? [keys[2]] : [] }));
    const template = { id: `builtin-${scenario}`, name: "示例任务模板", description: "测试目标", scenario, builtin: true, revision: 1, tags: [], outputs: ["report"], tasks, tier: "standard", groupBy: "organization_quarter" };
    const reviewed: { outputs: string[]; tasks: typeof tasks }[] = [];
    const saved: Record<string, unknown>[] = [];
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(err.message));
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      const body = route.request().method() === "POST" ? route.request().postDataJSON() : {};
      if (path === "/api/config") return route.fulfill({ json: { scenarios: [{ id: scenario, title: "任务场景" }], chainId: 31337, tokenSymbol: "TEST" } });
      if (path === "/api/workspace/planner") return route.fulfill({ json: { configured: false, message: "可复用模板" } });
      if (path === "/api/workspace/materials") return route.fulfill({ json: [{ id: "material-1", scenario, name: "测试材料", preview: { rows: 2, source: "本地", request: null } }] });
      if (path === "/api/workspace/capabilities") return route.fulfill({ json: tasks.map((t) => ({ ...t, scenario, acceptance: "校验结果", available: true })) });
      if (path === "/api/workspace/templates" && route.request().method() === "GET") return route.fulfill({ json: [template] });
      if (path === `/api/workspace/templates/${template.id}/apply`) return route.fulfill({ json: {
        id: "draft-1", mode: "template_dag", status: "ready", draft: { ...template, status: "ready", summary: "测试目标" },
        recipe: { ...body, outputs: ["report"], planningId: "draft-1", planningMode: "template" },
      } });
      if (path.includes("/planning/") && path.endsWith("/review")) {
        reviewed.push(body.draft);
        return route.fulfill({ json: { id: `reviewed-${reviewed.length}`, mode: "template_dag", status: "ready", reviewedAt: 1, draft: body.draft, recipe: { ...body.recipe, outputs: body.draft.outputs, planningId: `reviewed-${reviewed.length}` } } });
      }
      if (path === "/api/workspace/templates" && route.request().method() === "POST") {
        saved.push(body.template);
        return route.fulfill({ json: { ...body.template, id: "new-template" } });
      }
      return route.fulfill({ json: [] });
    });
    await page.goto("/");
    await page.getByLabel("访问令牌").fill(token);
    await page.getByRole("button", { name: /连接工作区/ }).click();
    await page.getByRole("combobox", { name: "任务场景", exact: true }).selectOption(scenario);
    await page.getByRole("checkbox", { name: /测试材料/ }).check();
    await page.getByLabel("想完成什么？", { exact: true }).fill("测试目标");
    await page.getByRole("button", { name: "查看子任务" }).click();
    const preview = page.getByLabel("模板内容预览");
    await expect(preview).toContainText("模板预览（只读）");
    await expect(preview.getByRole("checkbox")).toHaveCount(0);
    await page.getByRole("button", { name: "采用并修改本次任务" }).click();
    await expect(preview).toHaveCount(0);
    const review = page.locator(".planning-result");
    const outputs = review.getByRole("group", { name: "最终需要哪些成果", exact: true });
    for (const name of names.slice(0, 3)) {
      const checkbox = outputs.getByRole("checkbox", { name, exact: true });
      await checkbox.check();
      await expect(checkbox).toBeChecked();
      await checkbox.uncheck();
      await expect(checkbox).not.toBeChecked();
    }
    if (!travel) {
      await outputs.getByRole("checkbox", { name: "图表", exact: true }).check();
      await outputs.getByRole("checkbox", { name: "图表", exact: true }).uncheck();
    }
    await outputs.getByRole("checkbox", { name: "完整报告", exact: true }).uncheck();
    await outputs.getByRole("checkbox", { name: "完整报告", exact: true }).check();
    await outputs.getByRole("checkbox", { name: names[0], exact: true }).check();
    // Final-file selection must not delete required prerequisite tasks.
    await expect(review.locator(".react-flow__node")).toHaveCount(4);
    await expect(review.getByText("这一步要等待哪些任务", { exact: true })).toHaveCount(0);
    await review.getByText("调整前后关系", { exact: true }).click();
    await review.getByRole("button", { name: `删除连线：${names[0]} → ${names[2]}`, exact: true }).click();
    await review.getByRole("combobox", { name: "先完成的任务", exact: true }).selectOption(keys[0]);
    await review.getByRole("combobox", { name: "随后执行的任务", exact: true }).selectOption(keys[2]);
    await review.getByRole("button", { name: "添加连线", exact: true }).click();
    await expect(review.locator(`.react-flow__edge[data-id="${keys[0]}-${keys[2]}"]`)).toHaveCount(1);
    await review.getByRole("button", { name: "添加连线", exact: true }).click();
    await expect(review.getByRole("alert")).toContainText("已经存在");
    await review.getByRole("combobox", { name: "先完成的任务", exact: true }).selectOption(keys[2]);
    await review.getByRole("combobox", { name: "随后执行的任务", exact: true }).selectOption(keys[0]);
    await review.getByRole("button", { name: "添加连线", exact: true }).click();
    await expect(review.getByRole("alert")).toContainText("互相等待");
    await expect(review.locator(`.react-flow__edge[data-id="${keys[2]}-${keys[0]}"]`)).toHaveCount(0);
    await review.locator(".planning-graph").scrollIntoViewIfNeeded();
    const point = await review.locator(`.react-flow__edge[data-id="${keys[0]}-${keys[2]}"] .react-flow__edge-interaction`).evaluate((element) => {
      const path = element as SVGPathElement;
      const position = path.getPointAtLength(path.getTotalLength() / 2).matrixTransform(path.getScreenCTM()!);
      return { x: position.x, y: position.y };
    });
    await page.mouse.click(point.x, point.y);
    await review.getByRole("button", { name: "删除选中的连线", exact: true }).click();
    await expect(review.locator(`.react-flow__edge[data-id="${keys[0]}-${keys[2]}"]`)).toHaveCount(0);
    // Direct graph interaction edits the very same dependency data.
    const from = review.locator(`[data-testid="rf__node-${keys[0]}"] .react-flow__handle-bottom`);
    const to = review.locator(`[data-testid="rf__node-${keys[2]}"] .react-flow__handle-top`);
    await from.scrollIntoViewIfNeeded();
    const start = (await from.boundingBox())!, end = (await to.boundingBox())!;
    await page.mouse.move(start.x + start.width / 2, start.y + start.height / 2);
    await page.mouse.down();
    await page.mouse.move(end.x + end.width / 2, end.y + end.height / 2, { steps: 12 });
    await page.mouse.up();
    await expect(review.getByRole("button", { name: `删除连线：${names[0]} → ${names[2]}`, exact: true })).toBeVisible();
    await review.locator(".planning-graph").screenshot({ path: testInfo.outputPath("dependency-graph.png") });
    await review.getByRole("button", { name: "检查通过，确认这些子任务" }).click();
    await expect(page.getByRole("heading", { name: "已确认的交付成果" })).toBeVisible();
    await expect(page.getByRole("button", { name: "比较方案与新增费用", exact: true })).toBeEnabled();
    expect(reviewed[0].outputs).toEqual(["report", keys[0]]);
    expect([...reviewed[0].tasks[2].dependencies].sort()).toEqual(keys.slice(0, 2).sort());
    await review.getByText("把这份任务拆解保存为模板", { exact: true }).click();
    await review.getByRole("button", { name: "保存为新模板" }).click();
    await expect(review.getByText(/已保存“/)).toBeVisible();
    expect(saved[0].outputs).toEqual(reviewed[0].outputs);
    expect(saved[0].tasks).toEqual(reviewed[0].tasks);
    // Any subsequent edit invalidates the accepted summary and comparison gate.
    await review.getByRole("checkbox", { name: names[1], exact: true }).check();
    await expect(page.getByRole("heading", { name: "交付成果待确认" })).toBeVisible();
    await expect(page.getByRole("button", { name: "比较方案与新增费用", exact: true })).toBeDisabled();
    if (travel) {
      await page.setViewportSize({ width: 390, height: 844 });
      await expect(outputs).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await outputs.screenshot({ path: testInfo.outputPath("mobile-deliverables.png") });
    }
    await review.locator(".task-editor").filter({ has: page.getByRole("heading", { name: names[1], exact: true }) }).getByRole("button", { name: "移除此步骤" }).click();
    await expect(review.locator(".react-flow__node")).toHaveCount(3);
    await expect(review.locator(`.react-flow__edge[data-id="${keys[1]}-${keys[2]}"]`)).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}
