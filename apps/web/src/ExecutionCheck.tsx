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
  needs_revision: "这份草稿需要更新",
  blocked: "需要调整",
};

export default function ExecutionCheck({
  api,
  session,
  dirty,
  onJob,
  onRegenerate,
}: {
  api: Api;
  session: Row;
  dirty: boolean;
  onJob: (id: string) => void;
  onRegenerate?: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [materials, setMaterials] = useState<Row[]>([]),
    [ids, setIds] = useState<string[]>([]);
  const [budget, setBudget] = useState("100"),
    [groupBy, setGroupBy] = useState("");
  const [charts, setCharts] = useState(false);
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
  const supplied = JSON.stringify(session.graph?.execution || {});
  useEffect(() => {
    const known = JSON.parse(supplied);
    setBudget(known.serviceBudget || "100");
    setGroupBy(known.groupBy || "");
    setTravel(known.travelRequest || {});
    setIds([]);
    setCharts(known.outputs?.includes("charts") || false);
  }, [session.id, supplied]);
  useEffect(() => {
    reset();
  }, [session.revision, session.id, dirty]);
  useEffect(() => {
    if (session.phase === "confirmed")
      void api(base + "/materials")
        .then(setMaterials)
        .catch((e) => setError(String(e)));
  }, [session.id, session.phase, api]);
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
  const inspect = async () => {
    setOptions([]);
    setChosen(null);
    const settings: Row = {
      materialIds: scenario === "cross_org_analysis" ? ids : [],
      budget,
    };
    if (scenario === "cross_org_analysis") {
      if (groupBy) settings.groupBy = groupBy;
      if (charts) settings.outputs = ["report", "charts"];
    }
    if (scenario === "travel_planning" && Object.keys(travel).length)
      settings.travelRequest = travel;
    const result = await api(`${base}/designs/${session.id}/execution-check`, {
      expectedRevision: session.revision,
      settings,
    });
    setCheck(result);
    setPartial(false);
    if (result.recipe?.travelRequest) setTravel(result.recipe.travelRequest);
    return result;
  };
  const compare = async (result: Row, acceptPartial = false) => {
    const scope = await api(`${base}/designs/${session.id}/execution-confirm`, {
      checkId: result.id,
      acceptPartial,
    });
    const comparison = await api(base + "/comparisons", scope.recipe);
    setOptions(comparison.options);
    if (!comparison.options.length)
      setError(comparison.unavailable.map((x: Row) => x.reason).join("；"));
  };
  const quote = async () => {
    const result = await inspect();
    if (result.canConfirm && !result.gaps?.length) await compare(result);
  };
  const scenario =
    check?.recipe?.scenario ||
    session.graph?.execution?.scenario ||
    (session.graph?.tasks?.some((t: Row) =>
      [
        "attraction_search",
        "hotel_search",
        "route_planning",
        "travel_report",
      ].includes(t.capability),
    )
      ? "travel_planning"
      : session.graph?.tasks?.some((t: Row) =>
            [
              "data_cleaning",
              "data_validation",
              "statistical_analysis",
              "report_generation",
            ].includes(t.capability),
          )
        ? "cross_org_analysis"
        : "");
  const confirmed = session.phase === "confirmed" && !dirty;
  return (
    <section className="execution-check panel" aria-label="服务报价与预算">
      <h3>下一步：选择服务方案</h3>
      {!confirmed && <p>确认上方任务图后，设置服务费上限并获取报价。</p>}
      {(confirmed || check) && (
        <>
          <p>按已确认的任务图获取报价，选择后再确认托管预算。</p>
          <fieldset
            disabled={busy || !confirmed}
            className="execution-settings"
          >
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
            {scenario === "cross_org_analysis" && (
              <>
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
              </>
            )}
          </fieldset>
          <details open={check?.status === "needs_input"}>
            <summary>
              {scenario === "travel_planning"
                ? "出游条件"
                : scenario === "cross_org_analysis"
                  ? "销售数据"
                  : "补充执行材料"}
            </summary>
            <fieldset disabled={busy || !confirmed}>
              {scenario === "travel_planning" && (
                <p>景点和住宿资料由服务 Agent 获取，无需上传旅游文件。</p>
              )}
              {scenario === "cross_org_analysis" && (
                <>
                  <p>选择或上传本次要分析的销售 CSV。</p>
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
                </>
              )}
              {scenario === "travel_planning" && (
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
              )}
            </fieldset>
          </details>
          <button
            type="button"
            className="primary"
            disabled={busy || !confirmed}
            onClick={() => void run(quote)}
          >
            {busy ? "正在获取报价…" : "获取服务报价"}
          </button>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {check && (
            <div aria-live="polite">
              {check.status !== "ready" && (
                <h3>{states[check.status] || check.status}</h3>
              )}
              {check.issues.map((issue: string, i: number) => (
                <p key={i}>{issue}</p>
              ))}
              {check.status === "needs_revision" && onRegenerate && (
                <button
                  type="button"
                  disabled={busy || dirty}
                  onClick={() => void run(onRegenerate)}
                >
                  按已有需求重新生成任务图
                </button>
              )}
              {!!check.gaps?.length &&
                check.nodes.map((n: Row) => (
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
                  本次交付：
                  {check.recipe.outputs
                    .map(
                      (key: string) =>
                        (
                          ({
                            report: "完整报告",
                            charts: "图表",
                            attractions: "景点推荐",
                            hotels: "住宿比较",
                            route: "每日行程",
                            clean: "清洗数据",
                            validate: "质量检查",
                            statistics: "统计汇总",
                          }) as Row
                        )[key] || key,
                    )
                    .join("、")}
                  ；
                  {scenario === "cross_org_analysis" && (
                    <>
                      统计维度：
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
                      ；
                    </>
                  )}
                  服务费上限 {Number(check.recipe.budget).toFixed(2)}{" "}
                  USDC。报告需要人工接受后结算。
                </p>
              )}
              {check.canConfirm && !!check.gaps?.length && (
                <label>
                  <input
                    type="checkbox"
                    checked={partial}
                    onChange={(e) => setPartial(e.target.checked)}
                  />
                  我同意只执行上面支持的部分；未支持的原要求保留在项目记录中。
                </label>
              )}
              {check.canConfirm && !!check.gaps?.length && (
                <button
                  className="primary"
                  disabled={
                    busy || dirty || (check.gaps?.length > 0 && !partial)
                  }
                  onClick={() => void run(() => compare(check, partial))}
                >
                  按选定范围获取报价
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
  const [questions, setQuestions] = useState<Row[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [sending, setSending] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const [value, pending] = await Promise.all([
          api(`/api/jobs/${jobId}/collaboration`),
          api(`/api/jobs/${jobId}/communication/requests`),
        ]);
        if (alive) {
          setRows(value);
          setQuestions(pending);
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
  async function reply(id: string) {
    setSending(id);
    setError("");
    try {
      await api(`/api/jobs/${jobId}/communication/requests/${id}/reply`, {
        message: answers[id].trim(),
      });
      setQuestions((current) => current.filter((q) => q.id !== id));
    } catch (e) {
      setError(String(e));
    } finally {
      setSending(null);
    }
  }
  if (!rows.length && !questions.length && !error) return null;
  return (
    <section className="panel" aria-label="执行沟通">
      <h2>执行沟通</h2>
      {error && <p role="alert">{error}</p>}
      {questions.map((q) => (
        <form
          key={q.id}
          onSubmit={(event) => {
            event.preventDefault();
            void reply(q.id);
          }}
        >
          <label>
            工作 {q.taskId + 1} 需要补充：{q.message}
            <textarea
              value={answers[q.id] || ""}
              maxLength={4000}
              onChange={(event) =>
                setAnswers({ ...answers, [q.id]: event.target.value })
              }
              required
              disabled={sending !== null}
            />
          </label>
          <button
            type="submit"
            disabled={sending !== null || !answers[q.id]?.trim()}
          >
            {sending === q.id ? "发送中…" : "发送补充说明"}
          </button>
        </form>
      ))}
      {rows.map((r, i) => (
        <article className="execution-node" key={i}>
          <strong>
            {(
              {
                question: "服务提出执行问题",
                answer: "相关服务已回复",
                resumed: "收到信息，继续执行",
                waiting: "等待服务回复",
                unavailable: "无法取得所需信息",
                timeout: "等待回复超时",
                progress: "执行进度",
                request: "服务提出请求",
                reply: "服务回复",
                candidate: "中间成果",
                candidate_accepted: "候选检查通过",
                candidate_rejected: "候选需要修正",
                final: "正式成果已提交，等待验收",
                failure: "服务无法继续",
                wait: "等待补充信息",
              } as Row
            )[r.kind] || r.kind}
          </strong>
          <p>{r.message}</p>
          {typeof r.fromTask === "number" && typeof r.toTask === "number" && (
            <small>
              工作 {r.fromTask + 1} → 工作 {r.toTask + 1}
            </small>
          )}
          {typeof r.fromTask === "number" && typeof r.toTask !== "number" && (
            <small>工作 {r.fromTask + 1}</small>
          )}
        </article>
      ))}
    </section>
  );
}
