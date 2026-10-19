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
