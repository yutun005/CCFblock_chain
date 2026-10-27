import { TaskOutput } from "./Deliverables";
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

export function ProposalMetrics({ proposal }: { proposal: Row }) {
  const rate = proposal.pareto?.estimatedSuccessPercent;
  const sources: string[] = proposal.historySummary?.sources || [];
  return (
    <>
      {!!proposal.pareto?.advantages?.length && (
        <p>{proposal.pareto.advantages.join(" · ")}</p>
      )}
      <strong className="price">{price(proposal.estimatedPrice)} USDC</strong>
      <p>预计 {proposal.estimatedSeconds} 秒</p>
      <p>
        {rate == null ? "履约记录不足，暂不估算成功率" : `参考成功率 ${rate}%`}
      </p>
      {sources.includes("demo") && <small>成功率含演示履约记录</small>}
    </>
  );
}

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
          {!!options.length && (
            <p>
              共 {options.length} 种方案。
              {options[0].proposal.pareto?.comparisonNotice ||
                "同样的交付，比较费用、预计耗时和参考成功率。"}
            </p>
          )}
          <div className="execution-options">
            {options.map((o: Row, index: number) => (
              <article
                className={`execution-node proposal-card${chosen?.id === o.proposal.id ? " is-selected" : ""}`}
                aria-label={`方案 ${index + 1}`}
                key={o.proposal.id}
              >
                <div className="proposal-heading">
                  <h3>方案 {index + 1}</h3>
                  {chosen?.id === o.proposal.id && (
                    <span className="proposal-selection">已选择</span>
                  )}
                </div>
                <ProposalMetrics proposal={o.proposal} />
                <details>
                  <summary>查看服务与费用</summary>
                  {o.proposal.work.map((w: Row) => (
                    <p key={w.key}>
                      {w.title}：{price(w.estimatedPrice)} USDC
                    </p>
                  ))}
                </details>
                <button
                  disabled={busy}
                  aria-pressed={chosen?.id === o.proposal.id}
                  onClick={() => setChosen(o.proposal)}
                >
                  {chosen?.id === o.proposal.id ? "已选择此方案" : "选择此方案"}
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

export function Collaboration({
  api,
  jobId,
  tasks,
}: {
  api: Api;
  jobId: string;
  tasks: Row[];
}) {
  const [openOutputs, setOpenOutputs] = useState<Record<number, boolean>>({});
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
            {tasks.find((t) => t.taskId === q.taskId)?.title || "当前任务"}
            需要补充：{readableMessage(q.message)}
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
      <div className="task-record-grid">
        {tasks.map((task) => {
          const records = rows.filter((r) => r.fromTask === task.taskId);
          const latest = records.at(-1);
          const status =
            task.onchain.state === 2
              ? "已完成并结算"
              : task.onchain.state === 3
                ? "已结束"
                : task.checkpoint.status === "AWAITING_REVIEW"
                  ? "报告等待您验收"
                  : task.checkpoint.status === "REWORKING"
                    ? "正在修改报告"
                    : latest
                      ? eventLabels[latest.kind] || "正在处理"
                      : task.dependencies.length
                        ? "等待前置任务"
                        : "准备执行";
          const hasResult =
            records.some((r) => ["candidate", "final"].includes(r.kind)) ||
            task.onchain.state === 2;
          return (
            <article className="task-record" key={task.key}>
              <header>
                <h3>{task.title}</h3>
                <span
                  className={
                    "tag " + (task.onchain.state === 2 ? "text-good" : "")
                  }
                >
                  {status}
                </span>
              </header>
              <p className="task-summary">
                {task.onchain.state === 2
                  ? "成果已通过检查，可在下方查看。"
                  : task.checkpoint.status === "AWAITING_REVIEW"
                    ? "请在页面上方阅读报告，选择通过、修改或不通过。"
                    : latest?.kind === "failure" ||
                        latest?.kind === "candidate_rejected"
                      ? readableMessage(latest.message)
                      : hasResult
                        ? "本步骤已生成成果，正在完成检查与交付。"
                        : records.length
                          ? "正在处理本步骤，进度会自动更新。"
                          : "执行条件满足后自动委派。"}
              </p>
              {hasResult && (
                <details
                  onToggle={(e) => {
                    const open = e.currentTarget.open;
                    setOpenOutputs((old) => ({ ...old, [task.taskId]: open }));
                  }}
                >
                  <summary>查看本步骤成果</summary>
                  {openOutputs[task.taskId] && (
                    <TaskOutput
                      api={api}
                      jobId={jobId}
                      taskId={task.taskId}
                      revision={`${records.length}:${task.checkpoint.status}`}
                    />
                  )}
                </details>
              )}
              {!!records.length && (
                <details className="task-history">
                  <summary>查看处理过程 · {records.length} 条记录</summary>
                  <ol>
                    {records
                      .filter(
                        (r, i) =>
                          i === 0 ||
                          r.kind !== records[i - 1].kind ||
                          r.message !== records[i - 1].message,
                      )
                      .map((r, i) => (
                        <li key={i}>
                          <div>
                            <strong>{eventLabels[r.kind] || "处理记录"}</strong>
                            {r.at && (
                              <time>
                                {new Date(r.at * 1000).toLocaleTimeString(
                                  "zh-CN",
                                )}
                              </time>
                            )}
                          </div>
                          <p>{readableMessage(r.message)}</p>
                          {typeof r.toTask === "number" && (
                            <small>
                              发给：
                              {tasks.find((t) => t.taskId === r.toTask)
                                ?.title || "相关任务"}
                            </small>
                          )}
                        </li>
                      ))}
                  </ol>
                </details>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

const eventLabels: Record<string, string> = {
  question: "需要补充说明",
  answer: "已收到答复",
  resumed: "继续处理",
  waiting: "等待回复",
  unavailable: "缺少所需资料",
  timeout: "等待超时",
  progress: "正在处理",
  request: "提出协作请求",
  reply: "已回复请求",
  candidate: "成果已生成",
  candidate_accepted: "成果检查通过",
  candidate_rejected: "成果需要修正",
  final: "成果已提交",
  failure: "执行遇到问题",
  wait: "等待补充说明",
};
function readableMessage(text: string) {
  const labels: Record<string, string> = {
    "attractions.json": "景点推荐",
    "hotels.json": "住宿比较",
    "itinerary.json": "每日行程",
    "travel-report.md": "旅行方案",
    "travel-references.json": "旅行资料来源",
    "cleaned.csv": "清洗后的数据",
    "cleaning.json": "数据清洗结果",
    "validation.json": "数据质量检查",
    "statistics.json": "销售统计",
    "report.md": "分析报告",
    "references.json": "资料来源",
    "revision.json": "修改记录",
    "revenue.svg": "销售图表",
    culture: "历史文化",
    route: "路线规划",
    transfer: "交通衔接",
    hotels: "住宿候选",
  };
  let value = text || "";
  for (const [key, label] of Object.entries(labels))
    value = value.replaceAll(key, label);
  return value
    .replace(/Day\s*(\d+)/gi, "第 $1 天")
    .replace(
      /(\d+)\s*分(?!钟)/g,
      (_, n) => (Number(n) / 100).toFixed(2) + " 元",
    )
    .replace(
      /候选检查通过，等待服务提交正式成果；尚未付款/g,
      "成果检查通过，正在准备正式交付。",
    )
    .replace(
      /已完成本步骤的业务工具计算，正在核对成果。/g,
      "本步骤处理完成，正在检查成果。",
    )
    .replace(/\b(?:0x)?[a-fA-F0-9]{64}\b/g, "");
}
