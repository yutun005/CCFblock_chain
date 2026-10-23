import { useEffect, useState } from "react";
import { price } from "./amount";

type Row = Record<string, any>;
type Api = (path: string, body?: unknown) => Promise<any>;
const base = "/api/workspace";
const states: Row = {
  ready: "可以准备执行",
  needs_input: "还需要补充材料或条件",
  capability_gap: "部分要求尚无能力支持",
  unsupported_structure: "当前结构无法执行",
  service_unavailable: "服务暂不可用",
  model_unavailable: "模型暂不可用",
  blocked: "需要调整",
};

export default function ExecutionCheck({
  api,
  session,
  dirty,
  onJob,
}: {
  api: Api;
  session: Row;
  dirty: boolean;
  onJob: (id: string) => void;
}) {
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [materials, setMaterials] = useState<Row[]>([]),
    [ids, setIds] = useState<string[]>([]);
  const [budget, setBudget] = useState("100"),
    [groupBy, setGroupBy] = useState("");
  const [charts, setCharts] = useState(false),
    [coordination, setCoordination] = useState(false);
  const [travel, setTravel] = useState<Row>({}),
    [check, setCheck] = useState<Row | null>(null);
  const [options, setOptions] = useState<Row[]>([]),
    [chosen, setChosen] = useState<Row | null>(null),
    [partial, setPartial] = useState(false);
  const reset = () => {
    setCheck(null);
    setOptions([]);
    setChosen(null);
    setPartial(false);
  };
  useEffect(() => {
    reset();
  }, [session.revision, session.id, dirty]);
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  const isTravel =
    check?.recipe?.scenario === "travel_planning" ||
    coordination ||
    session.graph.tasks.some((t: Row) =>
      [
        "attraction_search",
        "hotel_search",
        "route_planning",
        "travel_report",
      ].includes(t.capability),
    );
  const inspect = async () => {
    setOptions([]);
    setChosen(null);
    const settings: Row = { materialIds: ids, budget, coordination };
    if (groupBy) settings.groupBy = groupBy;
    if (charts) settings.outputs = ["report", "charts"];
    if (Object.keys(travel).length) settings.travelRequest = travel;
    const result = await api(`${base}/designs/${session.id}/execution-check`, {
      expectedRevision: session.revision,
      settings,
    });
    setCheck(result);
    setPartial(false);
    if (result.recipe?.travelRequest) setTravel(result.recipe.travelRequest);
  };
  return (
    <section className="execution-check panel" aria-label="执行条件检查">
      <div className="workspace-actions">
        <button
          type="button"
          className="primary"
          disabled={dirty || busy}
          onClick={() =>
            void run(async () => {
              setOpen(true);
              setMaterials(await api(base + "/materials"));
              await inspect();
            })
          }
        >
          检查执行条件
        </button>
        {dirty && <span>先保存任务图，再检查本次执行。</span>}
      </div>
      {open && (
        <>
          <p>先核对实际能交付的工作；确认范围后再比较费用。此处不会付款。</p>
          <fieldset disabled={busy} className="execution-settings">
            <label>
              Agent 服务费上限（USDC）
              <input
                type="number"
                min="0.01"
                step="0.01"
                value={budget}
                onChange={(e) => {
                  setBudget(e.target.value);
                  reset();
                }}
              />
            </label>
            <label>
              销售统计维度
              <select
                value={groupBy}
                onChange={(e) => {
                  setGroupBy(e.target.value);
                  reset();
                }}
              >
                <option value="">按目标匹配；模板沿用默认</option>
                <option value="month">月份</option>
                <option value="category">商品类别</option>
                <option value="organization">组织</option>
                <option value="organization_quarter">组织与季度</option>
              </select>
            </label>
            <label>
              <input
                type="checkbox"
                checked={charts}
                onChange={(e) => {
                  setCharts(e.target.checked);
                  reset();
                }}
              />
              本次明确需要报告与图表
            </label>
            <label>
              <input
                type="checkbox"
                checked={coordination}
                onChange={(e) => {
                  setCoordination(e.target.checked);
                  reset();
                }}
              />
              景点与酒店协调候选（含最多一轮补充筛选）
            </label>
          </fieldset>
          <details open={check?.status === "needs_input"}>
            <summary>材料与出游条件</summary>
            <fieldset disabled={busy}>
              <p>销售分析需要私有 CSV；旅游不要求上传资料。</p>
              {materials
                .filter((m) => m.scenario === "cross_org_analysis")
                .map((m) => (
                  <label key={m.id}>
                    <input
                      type="checkbox"
                      checked={ids.includes(m.id)}
                      onChange={(e) => {
                        setIds(
                          e.target.checked
                            ? [...ids, m.id]
                            : ids.filter((x) => x !== m.id),
                        );
                        reset();
                      }}
                    />
                    {m.name}
                  </label>
                ))}
              <label>
                上传销售 CSV
                <input
                  type="file"
                  accept=".csv"
                  disabled={busy}
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f)
                      void run(async () => {
                        if (f.size > 2_000_000)
                          throw Error("文件不能超过 2 MB");
                        const m = await api(base + "/materials", {
                          name: f.name,
                          scenario: "cross_org_analysis",
                          content: await f.text(),
                        });
                        setMaterials(await api(base + "/materials"));
                        setIds([...ids, m.id]);
                        reset();
                      });
                  }}
                />
              </label>
              <div className="execution-settings">
                {[
                  ["destination", "目的地", "text"],
                  ["start_date", "出发日期", "date"],
                  ["days", "天数", "number"],
                  ["travelers", "人数", "number"],
                  ["budget_cents", "旅行预算（元）", "number"],
                ].map(([key, label, type]) => (
                  <label key={key}>
                    {label}
                    <input
                      type={type}
                      value={
                        key === "budget_cents"
                          ? travel[key] === undefined
                            ? ""
                            : travel[key] / 100
                          : (travel[key] ?? "")
                      }
                      onChange={(e) => {
                        const v = e.target.value;
                        setTravel({
                          ...travel,
                          [key]:
                            type === "number"
                              ? Number(v) * (key === "budget_cents" ? 100 : 1)
                              : v,
                        });
                        reset();
                      }}
                    />
                  </label>
                ))}
              </div>
              {isTravel && (
                <button
                  type="button"
                  onClick={() => {
                    setTravel({
                      destination: "协作演示城",
                      start_date: "2026-11-01",
                      days: 2,
                      travelers: 2,
                      budget_cents: 40000,
                      interests: ["culture"],
                      day_start: "09:00",
                      day_end: "18:00",
                    });
                    setCoordination(true);
                    reset();
                  }}
                >
                  使用两人两天、400 元的合成协作样例
                </button>
              )}
            </fieldset>
          </details>
          <button
            type="button"
            disabled={busy || dirty}
            onClick={() => void run(inspect)}
          >
            {busy ? "正在检查…" : "重新检查"}
          </button>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {check && (
            <div aria-live="polite">
              <h3>{states[check.status] || check.status}</h3>
              {check.issues.map((issue: string, i: number) => (
                <p key={i}>{issue}</p>
              ))}
              {check.nodes.map((n: Row) => (
                <article key={n.key} className="execution-node">
                  <strong>{n.title}</strong>
                  <p>{n.scope.supported}</p>
                  <small>
                    输入：{n.inputSources.join("、")} · 验收：
                    {n.scope.validation}
                  </small>
                  <p>
                    对应原图：
                    {n.sourceKeys
                      .map(
                        (k: string) =>
                          session.graph.tasks.find((t: Row) => t.key === k)
                            ?.title || k,
                      )
                      .join("、")}
                  </p>
                </article>
              ))}
              {check.recipe && (
                <p>
                  本次交付：{check.mapping.draft.summary}；统计维度：
                  {
                    (
                      {
                        month: "月份",
                        category: "类别",
                        organization: "组织",
                        organization_quarter: "组织与季度",
                      } as Row
                    )[check.recipe.groupBy]
                  }
                  ；服务费上限 {Number(check.recipe.budget).toFixed(2)}{" "}
                  USDC。报告需要人工接受后结算。
                </p>
              )}
              {!!check.gaps?.length && (
                <label>
                  <input
                    type="checkbox"
                    checked={partial}
                    onChange={(e) => setPartial(e.target.checked)}
                  />
                  我同意只执行上面支持的部分；未支持的原要求保留在项目记录中。
                </label>
              )}
              {check.canConfirm && (
                <button
                  className="primary"
                  disabled={
                    busy || dirty || (check.gaps?.length > 0 && !partial)
                  }
                  onClick={() =>
                    void run(async () => {
                      const scope = await api(
                        `${base}/designs/${session.id}/execution-confirm`,
                        { checkId: check.id, acceptPartial: partial },
                      );
                      const comparison = await api(
                        base + "/comparisons",
                        scope.recipe,
                      );
                      setOptions(comparison.options);
                      if (!comparison.options.length)
                        setError(
                          comparison.unavailable
                            .map((x: Row) => x.reason)
                            .join("；"),
                        );
                    })
                  }
                >
                  确认本次执行范围，查看费用
                </button>
              )}
            </div>
          )}
          <div className="execution-options">
            {options.map((o: Row) => (
              <article className="execution-node" key={o.proposal.id}>
                <strong>{price(o.proposal.estimatedPrice)} USDC</strong>
                <p>预计 {o.proposal.estimatedSeconds} 秒</p>
                <p>交付：{o.proposal.deliverables.join("、")}</p>
                <button onClick={() => setChosen(o.proposal)}>
                  选择此方案
                </button>
              </article>
            ))}
          </div>
          {chosen && (
            <section className="execution-node">
              <h3>确认服务费与验收</h3>
              <p>
                托管上限 {price(chosen.budget)} USDC，估计服务费{" "}
                {price(chosen.estimatedPrice)}{" "}
                USDC。就绪时选人；未使用资金在任务结束后可退款。
              </p>
              {chosen.work.map((w: Row) => (
                <p key={w.key}>
                  {w.title}：上限 {price(w.estimatedPrice)} USDC
                  {w.requiresReview ? "，需人工验收" : ""}
                  {w.coordinationMode && <small> · {w.coordinationMode}</small>}
                </p>
              ))}
              <button
                className="primary"
                disabled={busy || dirty}
                onClick={() =>
                  void run(async () => {
                    const result = await api(base + "/confirm", {
                      proposalId: chosen.id,
                      confirmationHash: chosen.confirmationHash,
                    });
                    if (result.jobId) onJob(result.jobId);
                  })
                }
              >
                确认并托管服务预算
              </button>
            </section>
          )}
        </>
      )}
    </section>
  );
}

export function Collaboration({ api, jobId }: { api: Api; jobId: string }) {
  const [rows, setRows] = useState<Row[]>([]),
    [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const value = await api(`/api/jobs/${jobId}/collaboration`);
        if (alive) {
          setRows(value);
          setError("");
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    }
    void poll();
    const timer = setInterval(poll, 3000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [api, jobId]);
  if (!rows.length && !error) return null;
  return (
    <section className="panel" aria-label="协作记录">
      <h2>协作记录</h2>
      {error && <p role="alert">{error}</p>}
      {rows.map((r, i) => (
        <article className="execution-node" key={i}>
          <strong>
            {(
              {
                candidate: "候选已提交，尚未结算",
                supplement_request: "请求补充筛选",
                adjustment: "服务已重新筛选",
                adopted: "共同检查通过",
                waiting_providers: "等待协作商家",
                unresolved: "协调未解决",
              } as Row
            )[r.kind] || r.kind}
          </strong>
          <p>{r.message}</p>
          {r.candidate?.data?.hotels && (
            <p>
              住宿候选：
              {r.candidate.data.hotels
                .map(
                  (h: Row) =>
                    `${h.name}，${(h.total_cents / 100).toFixed(2)} 元`,
                )
                .join("、")}
            </p>
          )}
          {r.hotel && (
            <p>
              最终采用 {r.hotel}，目录费用合计 {(r.totalCents / 100).toFixed(2)}{" "}
              元
            </p>
          )}
        </article>
      ))}
    </section>
  );
}
