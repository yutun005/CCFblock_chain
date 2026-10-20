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
    await page.getByRole("combobox", { name: "任务类型", exact: true }).selectOption(scenario);
    await page.getByRole(travel ? "radio" : "checkbox", { name: /测试材料/ }).check();
    await page.getByLabel("任务目标", { exact: true }).fill("测试目标");
    await page.getByRole("button", { name: "模板库", exact: true }).click();
    await page.getByRole("button", { name: "查看 / 管理" }).click();
    const preview = page.getByLabel("模板内容预览");
    await expect(preview).toContainText("模板预览（只读）");
    await expect(preview.getByRole("checkbox")).toHaveCount(0);
    await page.locator(".template-library > .workspace-panel").getByRole("button", { name: "用此模板创建任务" }).click();
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
    await review.getByRole("button", { name: "确认任务方案" }).click();
    await expect(page.locator(".plan-footer")).toContainText("任务方案已确认");
    await expect(page.getByRole("button", { name: "查看执行方案", exact: true })).toBeEnabled();
    expect(reviewed[0].outputs).toEqual(["report", keys[0]]);
    expect([...reviewed[0].tasks[2].dependencies].sort()).toEqual(keys.slice(0, 2).sort());
    await review.getByText("保存到模板库，下次复用", { exact: true }).click();
    await review.getByRole("button", { name: "保存为新模板" }).click();
    await expect(review.getByText(/已保存“/)).toBeVisible();
    expect(saved[0].outputs).toEqual(reviewed[0].outputs);
    expect(saved[0].tasks).toEqual(reviewed[0].tasks);
    // Switching views must preserve unfinished graph edits.
    await review.getByRole("textbox", { name: "子任务名称", exact: true }).fill("保留我的任务修改");
    await page.getByRole("button", { name: "编辑目标与材料", exact: true }).click();
    await page.getByRole("button", { name: "返回当前方案", exact: true }).click();
    await expect(review.getByRole("textbox", { name: "子任务名称", exact: true })).toHaveValue("保留我的任务修改");
    // Any subsequent edit invalidates the accepted summary and comparison gate.
    await review.getByRole("checkbox", { name: names[1], exact: true }).check();
    await expect(page.locator(".plan-footer")).toContainText("请先确认这份任务方案");
    await expect(page.getByRole("button", { name: "查看执行方案", exact: true })).toBeDisabled();
    await page.screenshot({ path: testInfo.outputPath("task-editor-desktop.png"), fullPage: true });
    if (travel) {
      await page.setViewportSize({ width: 375, height: 812 });
      await expect(outputs).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await outputs.screenshot({ path: testInfo.outputPath("mobile-deliverables.png") });
    }
    await review.getByRole("button", { name: `编辑任务：${names[1]}`, exact: true }).click();
    await review.locator(".task-editor").filter({ has: page.getByRole("heading", { name: names[1], exact: true }) }).getByRole("button", { name: "移除此步骤" }).click();
    await expect(review.locator(".react-flow__node")).toHaveCount(3);
    await expect(review.locator(`.react-flow__edge[data-id="${keys[1]}-${keys[2]}"]`)).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}


for (const entry of ["model", "offline", "template", "search"] as const) {
  test(`task creation: ${entry} entry has a complete and honest path`, async ({ page }, testInfo) => {
    const task = { key: "clean", skill: "data_cleaning", title: "清洗数据", reason: "清理输入数据", dependencies: [] };
    const template = { id: "builtin-sales", name: "销售数据清洗", description: "清理销售数据并交付清洗结果", scenario: "cross_org_analysis", builtin: true, revision: 1, tasks: [task], outputs: ["clean"], tags: [] };
    const material = { id: "upload-1", scenario: "cross_org_analysis", name: "我的销售.csv", preview: { rows: 2 } };
    let uploaded = false;
    let planCalls = 0;
    let applyCalls = 0;
    let confirmCalls = 0;
    let recipe: Record<string, unknown> = {};
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(err.message));
    const draft = { status: "ready", summary: "清理销售数据", tasks: [task], outputs: ["clean"], groupBy: "organization_quarter", tier: "standard" };
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      const post = request.method() === "POST";
      const body = post ? request.postDataJSON() : {};
      if (path === "/api/config") return route.fulfill({ json: { scenarios: [], tokenSymbol: "USDC", chainId: 31337 } });
      if (path.endsWith("/planner")) return route.fulfill({ json: { configured: entry === "model" } });
      if (path.endsWith("/materials")) {
        if (post) { expect(body.content).toContain("organization"); uploaded = true; return route.fulfill({ json: material }); }
        return route.fulfill({ json: uploaded ? [material] : [] });
      }
      if (path.endsWith("/capabilities")) return route.fulfill({ json: [{ ...task, scenario: template.scenario, acceptance: "格式与清洗结果可复算" }] });
      if (path.endsWith("/templates")) return route.fulfill({ json: !url.searchParams.get("q") || entry === "search" ? [template] : [] });
      if (path.endsWith("/planning") || path.endsWith("/apply")) {
        if (path.endsWith("/planning")) { planCalls++; expect(body.title).not.toBe(""); expect(body.planningMode).toBe("llm"); }
        else { applyCalls++; expect(body.requirement).toBeTruthy(); }
        recipe = { ...body, outputs: ["clean"], planningId: "draft", planningMode: path.endsWith("/planning") ? "llm" : "template" };
        return route.fulfill({ json: { id: "draft", status: "ready", mode: "llm_dag", draft, recipe } });
      }
      if (path.endsWith("/planning/draft/review")) {
        recipe = { ...body.recipe, planningId: "reviewed", outputs: body.draft.outputs };
        return route.fulfill({ json: { id: "reviewed", status: "ready", mode: "llm_dag", draft: body.draft, recipe, reviewedAt: 1 } });
      }
      if (path.endsWith("/comparisons")) return route.fulfill({ json: { options: [{ rank: 1, proposal: { id: "proposal", confirmationHash: "bound-hash", recipe, estimatedPrice: "10", estimatedSeconds: 5, budget: "100", selectionReason: "费用优先", pareto: { estimatedSuccessPercent: "95.00" }, deliverables: ["cleaned.csv"], work: [{ key: "clean", title: "清洗数据", estimatedProvider: "clean-a", estimatedPrice: "10", estimatedSeconds: 5, history: { window: 0, source: "none" } }], reused: [], materials: [material] } }], unavailable: [] } });
      if (path.endsWith("/confirm")) { confirmCalls++; expect(body).toEqual({ proposalId: "proposal", confirmationHash: "bound-hash" }); return route.fulfill({ json: { versionId: "v1", noCharge: false } }); }
      if (path.endsWith("/versions/v1")) return route.fulfill({ json: { id: "v1", number: 1, recipe, status: "PREPARING", estimatedPrice: "10", materials: [material], deliverables: ["cleaned.csv"], reviews: [], files: [] } });
      return route.fulfill({ json: [] });
    });
    await page.goto("/");
    await page.getByLabel("访问令牌").fill(token);
    await page.getByRole("button", { name: /连接工作区/ }).click();
    await expect(page.getByRole("heading", { name: "创建一个新任务" })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "规划方式" })).toHaveCount(0);
    await expect(page.getByText("先查找可复用的任务模板")).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("create-task-desktop.png"), fullPage: true });
    if (entry === "template") {
      await page.getByRole("button", { name: "从模板开始", exact: true }).click();
      await page.getByRole("button", { name: "用此模板创建任务", exact: true }).click();
      await expect(page.getByLabel("任务目标", { exact: true })).toHaveValue(template.description);
      await expect(page.getByText("使用模板：")).toBeVisible();
      expect(applyCalls).toBe(0);
    } else await page.getByLabel("任务目标", { exact: true }).fill("整理这些订单，清洗后给我数据文件");
    // Visiting another workspace tool must not erase the task being composed.
    await page.getByRole("button", { name: "协作任务", exact: true }).click();
    await page.getByRole("button", { name: "项目与成果", exact: true }).click();
    await expect(page.getByLabel("任务目标", { exact: true })).not.toHaveValue("");
    // Upload selects the material automatically; no separate confirmation or hidden CLI step.
    await page.getByLabel("上传销售数据", { exact: true }).setInputFiles({ name: material.name, mimeType: "text/csv", buffer: Buffer.from("organization,order_id,date,category,quantity,unit_price_cents\nA,1,2026-10-01,book,1,500") });
    await expect(page.getByRole("checkbox", { name: /我的销售.csv/ })).toBeChecked();
    await page.getByRole("button", { name: entry === "template" ? "继续编辑方案" : "生成任务方案", exact: true }).click();
    if (entry === "offline") {
      await expect(page.getByRole("heading", { name: "为这个目标创建方案" })).toBeVisible();
      await expect(page.getByRole("button", { name: "按目标生成新方案" })).toBeDisabled();
      await expect(page.getByText("当前尚未配置模型接口，可以先使用并修改模板。")).toBeVisible();
      expect(planCalls).toBe(0); expect(confirmCalls).toBe(0);
      await page.getByRole("button", { name: "修改任务需求" }).click();
      await expect(page.getByLabel("任务目标", { exact: true })).toHaveValue("整理这些订单，清洗后给我数据文件");
      await page.setViewportSize({ width: 375, height: 812 });
      await page.emulateMedia({ reducedMotion: "reduce" });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath("create-task-mobile.png"), fullPage: true });
      await page.setViewportSize({ width: 812, height: 375 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      return;
    }
    if (entry === "search") {
      await expect(page.getByRole("heading", { name: "有可复用的任务方案" })).toBeVisible();
      expect(applyCalls).toBe(0); expect(planCalls).toBe(0);
      await page.getByRole("button", { name: "使用并编辑", exact: true }).click();
    }
    await expect(page.getByRole("heading", { name: "编辑任务方案", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "查看执行方案" })).toBeDisabled();
    expect(confirmCalls).toBe(0);
    await page.getByRole("button", { name: "确认任务方案", exact: true }).click();
    await page.getByRole("button", { name: "查看执行方案", exact: true }).click();
    await expect(page.getByRole("heading", { name: "选择执行方案", exact: true })).toBeVisible();
    expect(confirmCalls).toBe(0);
    await page.getByRole("button", { name: "选择此方案", exact: true }).click();
    await page.getByRole("button", { name: "确认方案并托管预算", exact: true }).click();
    await expect(page.getByRole("heading", { name: /版本 1/ })).toBeVisible();
    expect(confirmCalls).toBe(1);
    expect(planCalls).toBe(entry === "model" ? 1 : 0);
    expect(applyCalls).toBe(entry === "model" ? 0 : 1);
    expect(errors).toEqual([]);
  });
}
