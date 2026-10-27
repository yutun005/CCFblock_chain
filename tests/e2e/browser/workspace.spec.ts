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
      expect(body.slaSeconds).toBe(3600);
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
      return route.fulfill({ json: [{
        resultHash: planHash,
        manifest: { taskId: 0, attempt: 0, artifacts: [
          { name: "cleaned.csv", sha256: planHash, size: 24, media_type: "text/csv" },
        ] },
      }] });
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
  await page.getByRole("button", { name: "执行与验收", exact: true }).click();
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
    slaSeconds: 3600,
    expectedPlanHash: planHash,
  });
  expect(createdBody?.requestId).toEqual(expect.any(String));
  await page.getByRole("button", { name: "成果与证据", exact: true }).click();
  await expect(page.getByRole("heading", { name: "成果文件" })).toBeVisible();
  await expect(page.getByText("cleaned.csv", { exact: true })).toBeVisible();
  await expect(page.locator(".artifact-group code")).toHaveCount(0);
  await expect(page.locator(`[title="${planHash}"]`)).toHaveCount(0);
  expect(pageErrors).toEqual([]);
});

for (const scenario of ["travel_planning", "cross_org_analysis"]) {
  test(`${scenario}: template preview and editable task graph stay consistent`, async ({ page }, testInfo) => {
    const travel = scenario === "travel_planning";
    const keys = travel ? ["attractions", "hotels", "route", "report"] : ["clean", "validate", "statistics", "report"];
    const names = travel ? ["景点推荐", "酒店推荐", "每日行程", "完整报告"] : ["清洗后的数据", "质量检查结果", "统计结果", "完整报告"];
    const skills = travel ? ["attraction_search", "hotel_search", "route_planning", "travel_report"] : ["data_cleaning", "data_validation", "statistical_analysis", "report_generation"];
    const tasks = keys.map((key, i) => ({ key, title: names[i], skill: skills[i], reason: "为目标提供成果", dependencies: i === 2 ? keys.slice(0, 2) : i === 3 ? [keys[2]] : [] }));
    const types = travel ? ["attractions", "hotels", "itinerary", "travel_report"] : ["cleaned_dataset", "quality_report", "statistics", "report"];
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
      if (path === "/api/workspace/capabilities") return route.fulfill({ json: tasks.map((t, i) => ({ ...t, outputType: types[i], requires: i === 2 ? types.slice(0, 2) : i === 3 ? [types[2]] : [], scenario, acceptance: "校验结果", available: true })) });
      if (path === "/api/workspace/templates" && route.request().method() === "GET") return route.fulfill({ json: [template] });
      if (path.endsWith("/travel/prepare")) return route.fulfill({ json: { status: "ready", request: { destination: "杭州", days: 3, travelers: 2, start_date: "2026-11-01", budget_cents: 200000 }, questions: [] } });
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
  await page.getByRole("button", { name: "执行与验收", exact: true }).click();
    await page.getByRole("combobox", { name: "任务类型", exact: true }).selectOption(scenario);
    if (!travel) await page.getByRole("checkbox", { name: /测试材料/ }).check();
    await page.getByLabel("任务目标", { exact: true }).fill("测试目标");
    await page.getByRole("navigation", { name: "项目操作" }).getByRole("button", { name: "模板库", exact: true }).click();
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
    await expect(review.getByRole("button", { name: "移除此步骤" })).toHaveCount(0);
    await expect(review.getByRole("combobox", { name: "添加子任务" })).toHaveCount(0);
    // Removing a final result removes only its now-unused work, immediately.
    await outputs.getByRole("checkbox", { name: "完整报告", exact: true }).uncheck();
    await expect(review.locator(".react-flow__node")).toHaveCount(1);
    await expect(review.locator(".react-flow__edge")).toHaveCount(0);
    await outputs.getByRole("checkbox", { name: names[2], exact: true }).check();
    await expect(review.locator(".react-flow__node")).toHaveCount(3);
    await expect(review.locator(".react-flow__edge")).toHaveCount(2);
    await outputs.getByRole("checkbox", { name: "完整报告", exact: true }).check();
    await expect(review.locator(".react-flow__node")).toHaveCount(4);
    await expect(review.locator(".react-flow__edge")).toHaveCount(3);
    // Dependency handles are intentionally read-only; task selection stays editable.
    await expect(review.getByRole("button", { name: "删除连线" })).toHaveCount(0);
    await review.locator(".planning-graph").screenshot({ path: testInfo.outputPath("dependency-graph.png") });
    await review.getByRole("button", { name: "确认任务方案" }).click();
    await expect(page.locator(".plan-footer")).toContainText("任务方案已确认");
    await expect(page.getByRole("button", { name: "查看执行方案", exact: true })).toBeEnabled();
    expect([...reviewed[0].outputs].sort()).toEqual(["report", keys[0], keys[2]].sort());
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
    await outputs.getByRole("checkbox", { name: "完整报告", exact: true }).uncheck();
    await outputs.getByRole("checkbox", { name: names[2], exact: true }).uncheck();
    await expect(review.locator(".react-flow__node")).toHaveCount(2);
    await expect(review.locator(".react-flow__edge")).toHaveCount(0);
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
      if (path.endsWith("/planning/intake")) {
        recipe = { ...body.recipe, outputs: ["clean"] };
        return route.fulfill({ json: { id: "intake", status: "ready", recipe, specHash: "spec-hash",
          spec: { status: "ready", summary: "清洗销售数据，交付数据文件", outputs: ["clean"], questions: [], limitations: [],
            requirements: [{ id: "R1", text: "交付清洗数据", priority: "must", status: "supported", explanation: "清洗服务支持" }] } } });
      }
      if (path.endsWith("/planning/intake/intake/confirm")) {
        expect(body.specHash).toBe("spec-hash");
        return route.fulfill({ json: { id: "confirmed-intake", confirmedRecipe: recipe, recipe,
          spec: { status: "ready", summary: "清洗销售数据，交付数据文件", outputs: ["clean"], questions: [], limitations: [], requirements: [] } } });
      }
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
      if (path.endsWith("/comparisons")) return route.fulfill({ json: { options: [{ rank: 1, proposal: { id: "proposal", confirmationHash: "bound-hash", recipe, estimatedPrice: "10", estimatedSeconds: 5, budget: "100", selectionReason: "在当前预算与候选商家中，没有其他组合能在费用、耗时、成功率上都不差且至少一项更好。", historySummary: { totalTasks: 4, coveredTasks: 4, settled: 155, expired: 45 }, pareto: { estimatedSuccessPercent: "95.00" }, deliverables: ["cleaned.csv"], work: [{ key: "clean", title: "清洗数据", estimatedProvider: "clean-a", estimatedPrice: "10", estimatedSeconds: 5, reason: "新增工作", requiredBy: ["report"], requiresReview: true, history: { window: 0, source: "none" } }], reused: [], materials: [material] } }], unavailable: [] } });
      if (path.endsWith("/confirm")) { confirmCalls++; expect(body).toEqual({ proposalId: "proposal", confirmationHash: "bound-hash" }); return route.fulfill({ json: { versionId: "v1", noCharge: false } }); }
      if (path.endsWith("/versions/v1")) return route.fulfill({ json: { id: "v1", number: 1, recipe, status: "PREPARING", estimatedPrice: "10", materials: [material], deliverables: ["cleaned.csv"], reviews: [], files: [] } });
      return route.fulfill({ json: [] });
    });
    await page.goto("/");
    await page.getByLabel("访问令牌").fill(token);
    await page.getByRole("button", { name: /连接工作区/ }).click();
  await page.getByRole("button", { name: "执行与验收", exact: true }).click();
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
    await page.getByRole("button", { name: "执行与验收", exact: true }).click();
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
    if (entry === "model") {
      await expect(page.getByRole("heading", { name: "理解需求与核对能力" })).toBeVisible();
      expect(planCalls).toBe(0); expect(confirmCalls).toBe(0);
      await page.getByRole("button", { name: "确认需求并生成任务图", exact: true }).click();
    }
    await expect(page.getByRole("heading", { name: "编辑任务方案", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "查看执行方案" })).toBeDisabled();
    expect(confirmCalls).toBe(0);
    await page.getByRole("button", { name: "确认任务方案", exact: true }).click();
    await page.getByRole("button", { name: "查看执行方案", exact: true }).click();
    await expect(page.getByRole("heading", { name: "选择执行方案", exact: true })).toBeVisible();
    await expect(page.locator(".tier-grid")).not.toContainText("在当前预算与候选商家中");
    await expect(page.locator(".tier-grid")).not.toContainText("参考商家近期记录合计");
    await expect(page.locator(".tier-grid")).toContainText("10.00 USDC");
    await expect(page.locator(".tier-grid")).toContainText("95.00%");
    expect(confirmCalls).toBe(0);
    await page.getByRole("button", { name: "选择此方案", exact: true }).click();
    await expect(page.locator(".confirmation")).not.toContainText(/新增工作|必做前置|需人工确认/);
    await expect(page.locator(".confirmation")).toContainText("清洗数据 · 10.00 USDC");
    await page.getByRole("button", { name: "确认方案并托管预算", exact: true }).click();
    await expect(page.getByRole("heading", { name: /版本 1/ })).toBeVisible();
    expect(confirmCalls).toBe(1);
    expect(planCalls).toBe(entry === "model" ? 1 : 0);
    expect(applyCalls).toBe(entry === "model" ? 0 : 1);
    expect(errors).toEqual([]);
  });
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
  await page.getByRole("button", { name: "执行与验收", exact: true }).click();
  await page.getByRole("button", { name: "我的项目", exact: true }).click();
  await page.getByRole("button", { name: "打开与下载", exact: true }).click();
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
  await expect(page.locator(".project-workspace")).not.toContainText(/SHA.?256|0xcdcd|resultHash/i);
  await expect(page.locator(".draft-preview")).toContainText("销售额,合计");
  await expect(page.locator(".draft-preview")).toContainText("1500.00 元");
  await expect(page.getByText(/保留业务说明/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("report-without-fingerprints.png"), fullPage: true });
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载 v1-report.md", exact: true }).focus();
  await page.getByRole("button", { name: "下载 v1-report.md", exact: true }).press("Enter");
  expect((await downloadEvent).suggestedFilename()).toBe("v1-report.md");
  expect(downloaded).toBe(true);
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
  let failAcceptance = true, decisions = 0, claims = 0;
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/config") return route.fulfill({ json: { scenarios: [], chainId: 31337, tokenSymbol: "USDC" } });
    if (path === "/api/jobs") return route.fulfill({ json: [{ jobId: id, status: job.status }] });
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
    if (path.endsWith("/claims") && route.request().method() === "POST") {
      claims++;
      Object.assign(job.chain, { withdrawn: "59000000", agentCredits: "0", refundCredit: "0", refunded: "41000000" });
      return route.fulfill({ json: job });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto(`/?job=${id}`);
  await page.getByLabel("访问令牌").fill(token);
  await page.getByRole("button", { name: /连接工作区/ }).click();
  await page.getByRole("button", { name: "协作任务", exact: true }).click();
  const accept = page.getByRole("button", { name: "验收通过并结算", exact: true });
  await accept.focus();
  await accept.press("Enter");
  await expect(page.getByRole("alert")).toContainText("验收暂未提交，请重试");
  await expect(page.getByRole("heading", { name: "协作任务", exact: true })).toBeVisible();
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
  const claim = panel.getByRole("button", { name: "领取报酬与退款", exact: true });
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
  await expect(page.locator(".metric").filter({ hasText: "可领取退款" })).toContainText("41.00");
  await expect(page.getByRole("table", { name: "各项服务费用" }).getByRole("row").filter({ hasText: "生成旅行方案" })).toContainText("9.00");
  await expect(claim).toBeEnabled();
  expect(claims).toBe(0);
  await page.screenshot({ path: testInfo.outputPath("settlement-desktop.png"), fullPage: true });
  await page.reload();
  await expect(page.getByRole("heading", { name: "任务结算", exact: true })).toBeVisible();
  await expect(claim).toBeEnabled();
  await claim.focus();
  await claim.press("Enter");
  await expect(page.locator(".metric").filter({ hasText: "已退还用户" })).toContainText("41.00");
  await expect(page.locator(".metric").filter({ hasText: "可领取退款" })).toContainText("0.00");
  await expect(panel).toContainText("本次任务的付款与退款已处理完毕。");
  await expect(claim).toBeDisabled();
  expect(claims).toBe(1);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("settlement-mobile.png"), fullPage: true });
  await panel.getByRole("button", { name: "查看报告与验收记录", exact: true }).click();
  const reopen = page.getByRole("button", { name: "查看结算", exact: true });
  await reopen.focus();
  await reopen.press("Enter");
  await expect(page.getByRole("heading", { name: "任务结算", exact: true })).toBeVisible();
  expect(decisions).toBe(2);
  expect(errors).toEqual([]);
});

for (const complete of [false, true]) {
  test(`travel goal without upload: ${complete ? "complete request" : "only missing clarifications"}`, async ({ page }, testInfo) => {
    const request = { destination: "杭州", start_date: "2026-11-01", days: 3, travelers: 2, budget_cents: 200000 };
    const tasks = [
      { key: "attractions", skill: "attraction_search", title: "获取景点", reason: "筛选景点", dependencies: [], outputType: "attractions", requires: [] },
      { key: "hotels", skill: "hotel_search", title: "比较酒店", reason: "比较住宿", dependencies: [], outputType: "hotels", requires: [] },
      { key: "route", skill: "route_planning", title: "安排路线", reason: "使用前置结果", dependencies: ["attractions", "hotels"], outputType: "itinerary", requires: ["attractions", "hotels"] },
      { key: "report", skill: "travel_report", title: "生成报告", reason: "汇总行程", dependencies: ["route"], outputType: "report", requires: ["itinerary"] },
    ];
    let preparations = 0, plans = 0, uploads = 0;
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      const post = route.request().method() === "POST";
      const body = post ? route.request().postDataJSON() : {};
      if (path === "/api/config") return route.fulfill({ json: { scenarios: [], chainId: 31337, tokenSymbol: "USDC" } });
      if (path.endsWith("/planner")) return route.fulfill({ json: { configured: false } });
      if (path.endsWith("/travel/source")) return route.fulfill({ json: { destinations: ["杭州", "示例城"], mode: "demo" } });
      if (path.endsWith("/materials")) { if (post) uploads++; return route.fulfill({ json: [] }); }
      if (path.endsWith("/capabilities")) return route.fulfill({ json: tasks.map((t) => ({ ...t, scenario: "travel_planning", available: true })) });
      if (path.endsWith("/travel/prepare")) {
        preparations++;
        expect(body.materialIds).toEqual([]);
        expect(body.serviceBudget).toBe("100");
        if (!complete && preparations === 1) return route.fulfill({ json: {
          status: "needs_input", interpretation: "local_rules", request: { days: 3 },
          questions: [{ field: "destination", label: "目的地" }, { field: "start_date", label: "出发日期" }, { field: "travelers", label: "出行人数" }, { field: "budget_cents", label: "旅行花费预算（元）" }],
        } });
        if (!complete) expect(body.answers).toMatchObject(request);
        return route.fulfill({ json: { status: "ready", request, questions: [], interpretation: "local_rules" } });
      }
      if (path.endsWith("/planning")) {
        plans++;
        expect(body.materialIds).toEqual([]);
        expect(body.travelRequest).toMatchObject(request);
        expect(body.budget).toBe("100");
        return route.fulfill({ json: { id: "travel-draft", status: "ready", mode: "template_dag",
          draft: { status: "ready", summary: "旅行方案", outputs: ["report"], tasks },
          recipe: { ...body, outputs: ["report"], planningId: "travel-draft", planningMode: "template" } } });
      }
      return route.fulfill({ json: [] });
    });
    await page.goto("/");
    await page.getByLabel("访问令牌").fill(token);
    await page.getByRole("button", { name: /连接工作区/ }).click();
  await page.getByRole("button", { name: "执行与验收", exact: true }).click();
    await page.getByRole("combobox", { name: "任务类型", exact: true }).selectOption("travel_planning");
    await expect(page.getByLabel("上传旅游资料", { exact: true })).not.toBeVisible();
    await page.getByLabel("任务目标", { exact: true }).fill(complete ? "2026年11月1日去杭州玩三天，两人出行，旅行预算2000元，生成完整方案" : "生成三日旅游方案");
    await page.getByRole("button", { name: "生成任务方案", exact: true }).click();
    if (!complete) {
      const questions = page.getByLabel("补充出行需求", { exact: true });
      await expect(questions.locator("input")).toHaveCount(4);
      await expect(questions.getByLabel("旅行天数", { exact: true })).toHaveCount(0);
      expect(plans).toBe(0);
      await questions.getByLabel("目的地", { exact: true }).fill("杭州");
      await questions.getByLabel("出发日期", { exact: true }).fill("2026-11-01");
      await questions.getByLabel("出行人数", { exact: true }).fill("2");
      await questions.getByLabel("旅行花费预算（元）", { exact: true }).fill("2000");
      await page.screenshot({ path: testInfo.outputPath("travel-clarification.png"), fullPage: true });
      await page.getByRole("button", { name: "生成任务方案", exact: true }).click();
    }
    await expect(page.getByRole("heading", { name: "编辑任务方案", exact: true })).toBeVisible();
    await expect(page.locator(".planning-result .react-flow__node")).toHaveCount(4);
    await expect(page.locator(".planning-result .react-flow__edge")).toHaveCount(3);
    expect(plans).toBe(1);
    expect(preparations).toBe(complete ? 1 : 2);
    expect(uploads).toBe(0);
  });
}
