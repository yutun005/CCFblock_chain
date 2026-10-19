import { useCallback, useEffect, useState, type ReactNode } from "react";

type Row = Record<string, any>;
type Api = (path: string, body?: unknown) => Promise<any>;
const prefix = "/api/workspace";
const tiers: Record<string, string> = {
  basic: "基础档",
  standard: "标准档",
  detailed: "详细档",
};
const labels: Record<string, string> = {
  clean: "清洗数据",
  validate: "数据质量检查",
  statistics: "统计结果",
  charts: "图表",
  report: "分析报告",
  attractions: "景点推荐",
  hotels: "酒店推荐",
  route: "路线安排",
};
const statuses: Record<string, string> = {
  ACTIVE: "执行中",
  SUCCEEDED: "已完成",
  PARTIAL_FAILED: "部分结束",
  INTERRUPTED: "执行中断",
  PREPARING: "准备中",
  AWAITING_REVIEW: "等待确认",
  REWORKING: "正在修改",
  ACCEPTED: "已经接受",
  REJECTED: "已拒绝",
  TIMED_OUT: "确认超时",
  CANCELED: "已取消",
  BIDDING: "询价中",
  ASSIGNING: "正在委派",
  RUNNING: "执行中",
  VERIFYING: "校验中",
  SETTLING: "结算中",
  SETTLED: "已结算",
  RECOVERING: "等待本轮失效",
  REASSIGNING: "重新委派",
};
const groupLabels: Record<string, string> = {
  organization_quarter: "组织与季度",
  organization: "组织",
  category: "商品类别",
  month: "月份",
};
const initial = {
  title: "销售数据分析",
  scenario: "cross_org_analysis",
  materialIds: [] as string[],
  outputs: ["clean", "validate", "statistics", "charts", "report"],
  tier: "standard",
  preference: "balanced",
  budget: "100",
  groupBy: "organization_quarter",
  requirement: "",
  forceRedo: [] as string[],
  reviewSeconds: 1800,
};
const money = (value: unknown) => {
  const units = BigInt(String(value ?? "0"));
  const fraction = (units % 1000000n)
    .toString()
    .padStart(6, "0")
    .replace(/0+$/, "");
  return (units / 1000000n).toString() + (fraction ? "." + fraction : "");
};

function Report({ text }: { text: string }) {
  const lines = text.split("\n"),
    blocks: ReactNode[] = [];
  const cells = (line: string) =>
    line
      .trim()
      .replace(/^\||\|$/g, "")
      .split("|")
      .map((value) => value.trim());
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i],
      key = i;
    if (
      line.trim().startsWith("|") &&
      /^\s*\|?[\s:|-]+\|\s*$/.test(lines[i + 1] || "")
    ) {
      const header = cells(line),
        rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|"))
        rows.push(cells(lines[i++]));
      i--;
      blocks.push(
        <div className="report-table" key={key}>
          <table>
            <thead>
              <tr>
                {header.map((cell, n) => (
                  <th key={n} scope="col">
                    {cell}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, n) => (
                <tr key={n}>
                  {row.map((cell, m) => (
                    <td key={m}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
    } else if (line.startsWith("# "))
      blocks.push(<h2 key={key}>{line.slice(2)}</h2>);
    else if (line.startsWith("## "))
      blocks.push(<h3 key={key}>{line.slice(3)}</h3>);
    else if (line.startsWith("### "))
      blocks.push(<h4 key={key}>{line.slice(4)}</h4>);
    else if (line && !line.startsWith("!["))
      blocks.push(
        <p key={key}>{line.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")}</p>,
      );
  }
  return <div className="report-body">{blocks}</div>;
}

const checkLabels: Record<string, string> = {
  signature: "执行者签名有效",
  input_lineage: "输入来源一致",
  frozen_deliverable_set: "约定文件齐全",
  source_numbers_and_hashes: "数值与来源校验通过",
  revision_changes: "修改要求已核验",
  offline_html_no_external_assets: "离线报告无需外部资源",
};

function Draft({ value }: { value: Row | null }) {
  if (!value) return <p role="status">正在读取报告…</p>;
  return (
    <div className="draft-preview">
      {value.files
        .filter((p: Row) => p.name.endsWith(".md"))
        .map((p: Row) => (
          <Report key={p.name} text={p.text} />
        ))}
      {value.files
        .filter((p: Row) => p.name.endsWith(".svg"))
        .map((p: Row) => (
          <img
            key={p.name}
            className="report-chart"
            src={
              "data:image/svg+xml;charset=utf-8," + encodeURIComponent(p.text)
            }
            alt="销售额图表；对应数值可在下方关联数据查看"
          />
        ))}
      {value.files
        .filter((p: Row) => p.name.endsWith(".html"))
        .map((p: Row) => (
          <details key={p.name}>
            <summary>预览离线网页报告</summary>
            <iframe title="离线网页报告" sandbox="" srcDoc={p.text} />
          </details>
        ))}
      <details>
        <summary>关联数据、引用资料与版本记录</summary>
        {[...value.files, ...(value.related || [])]
          .filter((p: Row) => /\.(json|csv)$/.test(p.name))
          .map((p: Row) => (
            <section key={p.name}>
              <h4>{p.name}</h4>
              <pre>{p.text}</pre>
            </section>
          ))}
      </details>
    </div>
  );
}

export default function Workspace({
  api,
  token,
  currentJob,
  onJob,
}: {
  api: Api;
  token: string;
  currentJob: string;
  onJob: (id: string) => void;
}) {
  const [page, setPage] = useState("editor"),
    [recipe, setRecipe] = useState<Row>(initial);
  const [materials, setMaterials] = useState<Row[]>([]),
    [projects, setProjects] = useState<Row[]>([]);
  const [offers, setOffers] = useState<Row[]>([]),
    [chosen, setChosen] = useState<Row | null>(null);
  const [version, setVersion] = useState<Row | null>(null),
    [compare, setCompare] = useState<Row | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Row>>({}),
    [reviews, setReviews] = useState<Row[]>([]);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState("");
  const [selectedDraft, setSelectedDraft] = useState<Record<string, number>>(
    {},
  );
  const [target, setTarget] = useState("结论与限制"),
    [revisionAction, setRevisionAction] = useState("shorten"),
    [feedback, setFeedback] = useState("");
  const [unresolved, setUnresolved] = useState<string[]>([]);
  const travel = recipe.scenario === "travel_planning";
  const availableOutputs = travel
    ? ["attractions", "hotels", "route", "report"]
    : ["clean", "validate", "statistics", "charts", "report"];
  const run = useCallback(async (name: string, action: () => Promise<void>) => {
    setBusy(name);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }, []);
  const refresh = useCallback(async () => {
    const [ms, ps] = await Promise.all([
      api(prefix + "/materials"),
      api(prefix + "/projects"),
    ]);
    setMaterials(ms);
    setProjects(ps);
  }, [api]);
  useEffect(() => {
    void refresh().catch((err) => setError(String(err)));
  }, [refresh]);
  const selectedVersion = version?.id;
  useEffect(() => {
    let disposed = false;
    async function tick() {
      try {
        const v = selectedVersion
          ? await api(`${prefix}/versions/${selectedVersion}`)
          : null;
        const rs =
          v?.reviews ||
          (currentJob ? await api(`${prefix}/reviews/${currentJob}`) : []);
        const hashes = rs.flatMap((r: Row) =>
          r.drafts.map((d: Row) => d.resultHash),
        );
        const ds = await Promise.all(
          hashes.map(async (hash: string) => [
            hash,
            await api(`${prefix}/drafts/${hash}`),
          ]),
        );
        if (!disposed) {
          if (v) setVersion(v);
          setReviews(rs);
          setDrafts((old) => ({ ...old, ...Object.fromEntries(ds) }));
        }
      } catch (err) {
        if (!disposed) setError(String(err));
      }
    }
    void tick();
    const timer = setInterval(() => void tick(), 2500);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [api, selectedVersion, currentJob]);
  const update = (patch: Row) => {
    setRecipe((old) => ({ ...old, ...patch }));
    setChosen(null);
    setOffers([]);
  };
  async function openVersion(id: string) {
    const value = await api(`${prefix}/versions/${id}`);
    setVersion(value);
    setCompare(null);
    setPage("review");
    if (value.jobId) onJob(value.jobId);
  }
  function follow(value: Row) {
    update({
      ...value.recipe,
      projectId: value.projectId,
      sourceVersion: value.id,
      forceRedo: [],
    });
    setPage("editor");
    setNotice(
      `基于版本 ${value.number} 创建续单。原版保留，确认前会列出复用成果和新增费用。`,
    );
  }
  async function buildComparison() {
    const result = await Promise.all(
      Object.keys(tiers).map(async (tier) => {
        const outputs =
          tier === "basic"
            ? recipe.outputs.filter(
                (key: string) => !["report", "charts"].includes(key),
              )
            : recipe.outputs;
        const selected = outputs.length
          ? outputs
          : [travel ? "route" : "statistics"];
        const candidate = { ...recipe, tier, outputs: selected };
        try {
          return {
            tier,
            proposal: await api(prefix + "/proposals", candidate),
            removed: recipe.outputs.filter(
              (key: string) => !selected.includes(key),
            ),
          };
        } catch (err) {
          return {
            tier,
            failure: err instanceof Error ? err.message : String(err),
          };
        }
      }),
    );
    setOffers(result);
    setPage("compare");
    setChosen(null);
  }
  async function submit() {
    if (!chosen) return;
    const result = await api(prefix + "/confirm", {
      proposalId: chosen.id,
      confirmationHash: chosen.confirmationHash,
    });
    await refresh();
    await openVersion(result.versionId);
    setNotice(
      result.noCharge
        ? "直接沿用已结算成果；没有新增收费任务或费用。"
        : "已确认方案并开始执行。报告生成后会在此等待您确认。请保持服务运行。",
    );
  }
  async function download(value: Row, file: Row) {
    const response = await fetch(
      `${prefix}/versions/${value.id}/files/${file.sha256}`,
      { headers: { Authorization: "Bearer " + token } },
    );
    if (!response.ok) throw new Error("下载失败，请刷新版本后重试");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `v${value.number}-${file.name}`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function decision(review: Row, action: string) {
    await api(`${prefix}/reviews/${encodeURIComponent(review.id)}`, {
      decisionId: crypto.randomUUID(),
      resultHash: review.drafts.at(-1).resultHash,
      action,
      target,
      feedback,
      revisionAction,
    });
    setFeedback("");
    setNotice(
      action === "accept"
        ? "已保存您接受的具体版本，正在验证并结算。"
        : action === "reject"
          ? "已拒绝当前报告；前置服务报酬保留，未使用余额进入退款。"
          : "修改意见已发回原执行者。本次不额外收费，稍后会生成修改稿。",
    );
    setReviews(await api(`${prefix}/reviews/${review.jobId}`));
  }
  return (
    <section className="project-workspace" aria-label="项目与成果工作区">
      <nav className="workspace-tabs" aria-label="项目操作">
        {[
          ["editor", "任务方案编辑"],
          ["compare", "方案对比"],
          ["history", "历史版本与续单"],
          ["review", "成果预览与修改"],
        ].map(([id, label]) => (
          <button
            key={id}
            onClick={() => {
              setPage(id);
              if (id === "history") void run("refresh", refresh);
            }}
            aria-current={page === id ? "page" : undefined}
          >
            {label}
            {id === "review" &&
            reviews.some((r) => r.status === "AWAITING_REVIEW")
              ? " · 待确认"
              : ""}
          </button>
        ))}
      </nav>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="workspace-notice" role="status">
          {notice}
        </p>
      )}
      {busy && (
        <p role="status">
          {busy === "compare"
            ? "正在向在线服务估价并检查复用成果…"
            : "正在处理…"}
        </p>
      )}
      {page === "editor" && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void run("compare", buildComparison);
          }}
        >
          <div className="workspace-columns">
            <section className="workspace-panel">
              <h2>1. 材料与需求</h2>
              {recipe.sourceVersion && (
                <p className="workspace-notice">
                  当前为续单。
                  <button
                    type="button"
                    onClick={() => {
                      setRecipe(initial);
                      setNotice("已切换为新项目。");
                    }}
                  >
                    改为新项目
                  </button>
                </p>
              )}
              <label>
                项目名称
                <input
                  value={recipe.title}
                  maxLength={100}
                  required
                  onChange={(e) => update({ title: e.target.value })}
                />
              </label>
              <label>
                任务场景
                <select
                  value={recipe.scenario}
                  disabled={!!recipe.sourceVersion}
                  onChange={(e) =>
                    update({
                      scenario: e.target.value,
                      materialIds: [],
                      travelRequest: null,
                      outputs:
                        e.target.value === "travel_planning"
                          ? ["attractions", "hotels", "route", "report"]
                          : initial.outputs,
                      requirement: "",
                    })
                  }
                >
                  <option value="cross_org_analysis">销售数据分析</option>
                  <option value="travel_planning">旅游行程规划</option>
                </select>
              </label>
              <fieldset>
                <legend>
                  选择已保存材料{travel ? "（一份）" : "（可多选）"}
                </legend>
                {materials
                  .filter((m) => m.scenario === recipe.scenario)
                  .map((m) => (
                    <label className="check-row" key={m.id}>
                      <input
                        type="checkbox"
                        checked={recipe.materialIds.includes(m.id)}
                        onChange={(e) =>
                          update({
                            materialIds: e.target.checked
                              ? travel
                                ? [m.id]
                                : [...recipe.materialIds, m.id]
                              : recipe.materialIds.filter(
                                  (id: string) => id !== m.id,
                                ),
                            ...(travel && e.target.checked
                              ? { travelRequest: m.preview.request }
                              : {}),
                          })
                        }
                      />
                      {m.name}
                      <small>
                        {travel
                          ? `${m.preview.source} · ${m.preview.as_of}`
                          : `${m.preview.rows} 行`}
                      </small>
                    </label>
                  ))}
              </fieldset>
              <label>
                导入{travel ? "旅游 JSON 资料" : "自己的销售 CSV"}
                <input
                  type="file"
                  accept={travel ? ".json" : ".csv"}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    void run("upload", async () => {
                      if (file.size > 2_000_000)
                        throw new Error("文件不能超过 2 MB");
                      const material = await api(prefix + "/materials", {
                        name: file.name,
                        scenario: recipe.scenario,
                        content: await file.text(),
                      });
                      await refresh();
                      update({
                        materialIds: travel
                          ? [material.id]
                          : [...new Set([...recipe.materialIds, material.id])],
                        ...(travel
                          ? { travelRequest: material.preview.request }
                          : {}),
                      });
                    });
                    e.target.value = "";
                  }}
                />
              </label>
              {!travel && (
                <p className="hint">
                  CSV 列名：organization, order_id, date, category, quantity,
                  unit_price_cents。金额以分计；UTF-8 编码。类别支持
                  book、food、tool；日期为 YYYY-MM-DD；最多 2 MB、20000 行。
                </p>
              )}
              {materials
                .filter((m) => recipe.materialIds.includes(m.id))
                .map((m) => (
                  <details key={m.id}>
                    <summary>核对材料：{m.name}</summary>
                    <pre>{JSON.stringify(m.preview, null, 2)}</pre>
                  </details>
                ))}
              <label>
                文字需求（可选）
                <textarea
                  value={recipe.requirement}
                  maxLength={4000}
                  onChange={(e) => update({ requirement: e.target.value })}
                  placeholder={
                    travel
                      ? "景点、酒店、路线和详细报告，预算100"
                      : "统计结果、图表和标准报告，预算100"
                  }
                />
              </label>
              <button
                type="button"
                disabled={!!busy || !recipe.requirement.trim()}
                onClick={() =>
                  void run("parse", async () => {
                    const result = await api(prefix + "/parse", {
                      text: recipe.requirement,
                      scenario: recipe.scenario,
                    });
                    update(result.patch);
                    setUnresolved(result.unresolved);
                    setNotice(
                      "已把识别结果填入可见选项。您可以修改，确认时以最终选项为准。",
                    );
                  })
                }
              >
                解析并填入选项
              </button>
              {!!unresolved.length && (
                <p role="alert">这些要求需调整：{unresolved.join("；")}</p>
              )}
            </section>
            <section className="workspace-panel">
              <h2>2. 交付与预算</h2>
              <fieldset>
                <legend>本次希望拿到的成果</legend>
                {availableOutputs.map((key) => (
                  <label className="check-row" key={key}>
                    <input
                      type="checkbox"
                      checked={recipe.outputs.includes(key)}
                      onChange={(e) =>
                        update({
                          outputs: e.target.checked
                            ? [...recipe.outputs, key]
                            : recipe.outputs.filter(
                                (item: string) => item !== key,
                              ),
                        })
                      }
                    />
                    {travel && key === "report" ? "完整行程报告" : labels[key]}
                  </label>
                ))}
              </fieldset>
              <p className="hint">
                必要的前置工作将在确认页列出。未选择的报告或图表不会自动收费。
              </p>
              {!travel && (
                <label>
                  统计分组
                  <select
                    value={recipe.groupBy}
                    onChange={(e) => update({ groupBy: e.target.value })}
                  >
                    <option value="organization_quarter">组织与季度</option>
                    <option value="organization">组织</option>
                    <option value="category">商品类别</option>
                    <option value="month">月份</option>
                  </select>
                </label>
              )}
              {travel && recipe.travelRequest && (
                <fieldset>
                  <legend>本次出行要求</legend>
                  {[
                    ["destination", "目的地", "text"],
                    ["start_date", "出发日期", "date"],
                    ["days", "天数（1–7）", "number"],
                    ["travelers", "人数", "number"],
                    ["budget_cents", "行程预算（分）", "number"],
                    ["day_start", "每日开始", "time"],
                    ["day_end", "每日结束", "time"],
                  ].map(([key, label, type]) => (
                    <label key={key}>
                      {label}
                      <input
                        type={type}
                        value={recipe.travelRequest[key]}
                        required
                        onChange={(e) =>
                          update({
                            travelRequest: {
                              ...recipe.travelRequest,
                              [key]:
                                type === "number"
                                  ? Number(e.target.value)
                                  : e.target.value,
                            },
                          })
                        }
                      />
                    </label>
                  ))}
                  <label>
                    兴趣类别（用逗号分隔）
                    <input
                      value={(recipe.travelRequest.interests || []).join(",")}
                      onChange={(e) =>
                        update({
                          travelRequest: {
                            ...recipe.travelRequest,
                            interests: e.target.value
                              .split(/[,，]/)
                              .map((s) => s.trim())
                              .filter(Boolean),
                          },
                        })
                      }
                    />
                  </label>
                </fieldset>
              )}
              <label>
                服务选择偏好
                <select
                  value={recipe.preference}
                  onChange={(e) => update({ preference: e.target.value })}
                >
                  <option value="lowest_price">低价优先</option>
                  <option value="fastest">速度优先</option>
                  <option value="balanced">
                    综合选择（价格、耗时、履约记录）
                  </option>
                </select>
              </label>
              <label>
                服务费用预算上限（USDC）
                <input
                  inputMode="decimal"
                  pattern="[0-9]+(\.[0-9]{1,6})?"
                  value={recipe.budget}
                  required
                  onChange={(e) => update({ budget: e.target.value })}
                />
              </label>
              <label>
                报告确认时限
                <select
                  value={recipe.reviewSeconds}
                  onChange={(e) =>
                    update({ reviewSeconds: Number(e.target.value) })
                  }
                >
                  <option value={600}>10 分钟</option>
                  <option value={1800}>30 分钟</option>
                  <option value={3300}>55 分钟</option>
                </select>
              </label>
              {recipe.sourceVersion && (
                <fieldset>
                  <legend>明确要求重做（会新增费用，并重做相关下游）</legend>
                  {(travel
                    ? ["attractions", "hotels", "route", "report"]
                    : ["clean", "validate", "statistics", "report"]
                  ).map((key) => (
                    <label className="check-row" key={key}>
                      <input
                        type="checkbox"
                        checked={recipe.forceRedo.includes(key)}
                        onChange={(e) =>
                          update({
                            forceRedo: e.target.checked
                              ? [...recipe.forceRedo, key]
                              : recipe.forceRedo.filter(
                                  (x: string) => x !== key,
                                ),
                          })
                        }
                      />
                      {labels[key]}
                    </label>
                  ))}
                </fieldset>
              )}
              <button
                className="primary"
                type="submit"
                disabled={
                  !!busy || !recipe.materialIds.length || !recipe.outputs.length
                }
              >
                比较方案与新增费用
              </button>
            </section>
          </div>
        </form>
      )}
      {page === "compare" && (
        <section>
          <h2>比较本次交付方案</h2>
          <p>
            估价来自当前在线服务，耗时为服务申报值。实际执行者与成交费用以任务中的签名报价为准。人工确认所需时间另计。
          </p>
          {!offers.length && <p>请先在“任务方案编辑”中选择材料和交付内容。</p>}
          <div className="tier-grid">
            {offers.map((offer) => (
              <article
                key={offer.tier}
                className={
                  "workspace-panel " +
                  (chosen?.id === offer.proposal?.id ? "chosen" : "")
                }
              >
                <h3>{tiers[offer.tier]}</h3>
                {offer.failure ? (
                  <p role="status">{offer.failure}</p>
                ) : (
                  <>
                    <strong className="price">
                      {offer.proposal.estimatedPrice} USDC
                    </strong>
                    <p>预计服务执行 {offer.proposal.estimatedSeconds} 秒</p>
                    <p>
                      {offer.proposal.recipe.outputs
                        .map((key: string) => labels[key])
                        .join("、")}
                    </p>
                    {!!offer.removed?.length && (
                      <p>
                        本档移除：
                        {offer.removed
                          .map((key: string) => labels[key])
                          .join("、")}
                      </p>
                    )}
                    {offer.tier === "detailed" &&
                      offer.proposal.recipe.outputs.includes("report") && (
                        <p>比标准档增加：离线网页报告、数据与来源对照表。</p>
                      )}
                    <ul>
                      {offer.proposal.deliverables.map((name: string) => (
                        <li key={name}>{name}</li>
                      ))}
                    </ul>
                    <p>
                      服务：
                      {offer.proposal.work
                        .map((w: Row) => `${w.title}（${w.estimatedProvider}）`)
                        .join("、") || "全部复用"}
                    </p>
                    <button
                      disabled={!!busy}
                      onClick={() => setChosen(offer.proposal)}
                    >
                      {chosen?.id === offer.proposal.id
                        ? "已选择"
                        : "选择此方案"}
                    </button>
                  </>
                )}
              </article>
            ))}
          </div>
          {chosen && (
            <section className="workspace-panel confirmation">
              <h3>确认这份方案后执行</h3>
              <p>
                最终交付：
                {chosen.recipe.outputs
                  .map((key: string) => labels[key])
                  .join("、")}{" "}
                · {tiers[chosen.recipe.tier]}
              </p>
              {chosen.recipe.requirement && (
                <p>原文字需求：{chosen.recipe.requirement}</p>
              )}
              {!!chosen.overrides?.length && (
                <p className="workspace-notice">
                  本方案已调整文字解析得到的选项。执行以此处展示的最终交付、档位、服务偏好和预算为准。
                </p>
              )}
              <p>材料：{chosen.materials.map((m: Row) => m.name).join("、")}</p>
              <p>
                预计新增费用：<b>{chosen.estimatedPrice} USDC</b>；预算上限：
                {chosen.budget} USDC。
              </p>
              <p>报告需人工接受才付款；最多免费返工两次，到期不自动接受。</p>
              <div className="workspace-columns">
                <div>
                  <h4>本次执行</h4>
                  {chosen.work.map((w: Row) => (
                    <p key={w.key}>
                      {w.title} · {w.estimatedPrice} USDC
                      <br />
                      <small>
                        {w.reason}
                        {w.requiresReview ? " · 需人工确认" : ""}
                      </small>
                    </p>
                  ))}
                </div>
                <div>
                  <h4>直接复用</h4>
                  {chosen.reused.map((w: Row) => (
                    <p key={w.key}>
                      {w.title}
                      <br />
                      <small>{w.reason}</small>
                    </p>
                  ))}
                  {!chosen.reused.length && <p>无</p>}
                </div>
              </div>
              <p>
                实际报价超过展示的节点预算时不会成交；剩余资金在结束后退款。
              </p>
              <button
                className="primary"
                disabled={!!busy}
                onClick={() => void run("confirm", submit)}
              >
                {chosen.noCharge
                  ? "查看已有成果（不收费）"
                  : "确认方案并托管预算"}
              </button>
              <button disabled={!!busy} onClick={() => setPage("editor")}>
                返回修改
              </button>
            </section>
          )}
        </section>
      )}
      {page === "history" && (
        <section>
          <h2>历史版本与续单</h2>
          {!projects.length && (
            <p>确认第一份方案后，这里会保存项目和每个版本。</p>
          )}
          {projects.map((project) => (
            <article className="workspace-panel" key={project.id}>
              <h3>{project.title}</h3>
              {project.versions.map((v: Row) => (
                <div className="version-row" key={v.id}>
                  <span>
                    <b>版本 {v.number}</b> · {tiers[v.recipe.tier]}
                    <small>
                      {new Date(v.createdAt * 1000).toLocaleString()} · 估价{" "}
                      {v.estimatedPrice} USDC
                    </small>
                  </span>
                  <button
                    onClick={() => void run("open", () => openVersion(v.id))}
                  >
                    打开与下载
                  </button>
                  <button
                    onClick={() =>
                      void run("follow", async () =>
                        follow(await api(`${prefix}/versions/${v.id}`)),
                      )
                    }
                  >
                    基于此版本继续
                  </button>
                </div>
              ))}
            </article>
          ))}
        </section>
      )}
      {page === "review" && (
        <section>
          <h2>
            {version
              ? `${version.recipe.title} · 版本 ${version.number}`
              : "成果预览与修改"}
          </h2>
          {version && (
            <>
              <div className="workspace-panel">
                <p>
                  状态：{statuses[version.status] || version.status}
                  ；预计新增服务费：{version.estimatedPrice} USDC。
                </p>
                <p>
                  材料：{version.materials.map((m: Row) => m.name).join("、")}
                  。交付：{version.deliverables.join("、")}
                </p>
                {version.chain && (
                  <p>
                    已结算报酬：{money(version.chain.settledAmount)} USDC ·
                    已退款：
                    {money(version.chain.refunded)} USDC · 可退余额：
                    {money(version.chain.refundCredit)} USDC
                  </p>
                )}
                <div className="workspace-actions">
                  <button onClick={() => follow(version)}>
                    基于此版本继续
                  </button>
                  {version.jobId &&
                    ["SUCCEEDED", "PARTIAL_FAILED"].includes(
                      version.status,
                    ) && (
                      <button
                        disabled={!!busy}
                        onClick={() =>
                          void run("claim", async () => {
                            await api(`/api/jobs/${version.jobId}/claims`, {});
                            setNotice("已执行服务提款和剩余预算退款。");
                          })
                        }
                      >
                        完成提款与退款
                      </button>
                    )}
                  <button
                    onClick={() => {
                      if (version.jobId) onJob(version.jobId);
                    }}
                  >
                    查看当前任务记录
                  </button>
                </div>
                {version.tasks?.map((task: Row) => (
                  <p key={task.key}>
                    {task.title} ·{" "}
                    {task.checkpoint.selected
                      ? `实际执行者 ${task.checkpoint.selected.agentId} · 成交 ${money(task.checkpoint.selected.quote.price)} USDC`
                      : "等待服务成交"}{" "}
                    ·{" "}
                    {statuses[task.checkpoint.status] ||
                      task.checkpoint.status ||
                      "等待依赖"}
                  </p>
                ))}
                <h3>此版本已结算成果</h3>
                <div className="workspace-actions">
                  {version.files.map((file: Row) => (
                    <button
                      key={file.sha256}
                      onClick={() =>
                        void run("download", () => download(version, file))
                      }
                    >
                      下载 v{version.number}-{file.name}
                    </button>
                  ))}
                </div>
                <label>
                  与历史版本比较
                  <select
                    value={compare?.id || ""}
                    onChange={(e) => {
                      const id = e.target.value;
                      if (!id) {
                        setCompare(null);
                        return;
                      }
                      void run("compare-version", async () => {
                        const other = await api(`${prefix}/versions/${id}`);
                        setCompare(other);
                        const hashes = other.reviews.flatMap((r: Row) =>
                          r.drafts.map((d: Row) => d.resultHash),
                        );
                        const pairs = await Promise.all(
                          hashes.map(async (hash: string) => [
                            hash,
                            await api(`${prefix}/drafts/${hash}`),
                          ]),
                        );
                        setDrafts((old) => ({
                          ...old,
                          ...Object.fromEntries(pairs),
                        }));
                      });
                    }}
                  >
                    <option value="">选择旧版</option>
                    {projects
                      .find((p) => p.id === version.projectId)
                      ?.versions.filter((v: Row) => v.id !== version.id)
                      .map((v: Row) => (
                        <option key={v.id} value={v.id}>
                          版本 {v.number}
                        </option>
                      ))}
                  </select>
                </label>
              </div>
              {compare && (
                <div className="workspace-columns">
                  {[compare, version].map((v) => (
                    <section className="workspace-panel" key={v.id}>
                      <h3>版本 {v.number}</h3>
                      <p>
                        材料：{v.materials.map((m: Row) => m.name).join("、")}
                      </p>
                      <p>
                        要求：{v.recipe.requirement || "按已选选项执行"} · 分组{" "}
                        {groupLabels[v.recipe.groupBy]} · {tiers[v.recipe.tier]}
                      </p>
                      <p>交付：{v.deliverables.join("、")}</p>
                      {v.reviews.map((r: Row) => (
                        <Draft
                          key={r.id}
                          value={
                            drafts[r.acceptedHash || r.drafts.at(-1).resultHash]
                          }
                        />
                      ))}
                    </section>
                  ))}
                </div>
              )}
            </>
          )}
          {!reviews.length && (
            <p>
              {version
                ? "任务仍在执行，或本次没有报告交付。报告通过系统检查后会在这里等待确认。"
                : "请从历史版本打开一个项目，或先创建任务方案。"}
            </p>
          )}
          {reviews.map((review) => {
            const index = selectedDraft[review.id] ?? review.drafts.length - 1;
            const hash =
              review.drafts[Math.min(index, review.drafts.length - 1)]
                .resultHash;
            const latest = index === review.drafts.length - 1;
            const canDecide =
              review.status === "AWAITING_REVIEW" &&
              latest &&
              Date.now() < review.deadline * 1000;
            const isTravel =
              version?.recipe.scenario === "travel_planning" ||
              !!drafts[hash]?.files.some(
                (f: Row) => f.name === "travel-report.md",
              );
            const targets = isTravel
              ? ["需求与资料", "住宿", "每日行程", "费用", "限制与来源"]
              : [
                  "数据与质量",
                  "统计结果",
                  "结论与限制",
                  "来源",
                  ...(drafts[hash]?.files.some(
                    (f: Row) => f.name === "revenue.svg",
                  )
                    ? ["revenue.svg"]
                    : []),
                ];
            return (
              <article className="workspace-panel" key={review.id}>
                <h3>
                  {statuses[review.status]} · {review.provider}
                </h3>
                <p>
                  确认截止：{new Date(review.deadline * 1000).toLocaleString()}{" "}
                  · 剩余免费返工 {2 - review.changes.length} 次 · 报告成交费{" "}
                  {money(review.price)} USDC。
                </p>
                <label>
                  查看草稿
                  <select
                    value={index}
                    onChange={(e) =>
                      setSelectedDraft((old) => ({
                        ...old,
                        [review.id]: Number(e.target.value),
                      }))
                    }
                  >
                    {review.drafts.map((d: Row, i: number) => (
                      <option key={d.resultHash} value={i}>
                        {i === 0 ? "初稿" : `第 ${i} 次修改稿`}
                        {d.resultHash === review.acceptedHash
                          ? " · 已接受"
                          : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <p>
                  系统检查：
                  {review.drafts[index].checks
                    .map((check: string) => checkLabels[check] || check)
                    .join(" · ")}
                </p>
                <Draft value={drafts[hash]} />
                <details>
                  <summary>修改意见与用户决定（完整记录）</summary>
                  <pre>
                    {JSON.stringify(
                      {
                        changes: review.changes,
                        decisions: review.decisions,
                        acceptedHash: review.acceptedHash,
                      },
                      null,
                      2,
                    )}
                  </pre>
                </details>
                {canDecide && (
                  <div className="review-controls">
                    <h4>对当前最新版作出决定</h4>
                    <div className="workspace-actions">
                      <button
                        className="primary"
                        disabled={!!busy}
                        onClick={() =>
                          void run("accept", () => decision(review, "accept"))
                        }
                      >
                        接受此版并结算
                      </button>
                      <button
                        className="danger"
                        disabled={!!busy}
                        onClick={() => {
                          if (
                            window.confirm(
                              "拒绝并结束当前报告？已结算的前置工作费用会保留，未使用余额可退款。",
                            )
                          )
                            void run("reject", () =>
                              decision(review, "reject"),
                            );
                        }}
                      >
                        拒绝并结束
                      </button>
                    </div>
                    {review.changes.length < 2 && (
                      <fieldset>
                        <legend>要求原执行者免费修改</legend>
                        <label>
                          具体位置
                          <select
                            value={targets.includes(target) ? target : ""}
                            onChange={(e) => setTarget(e.target.value)}
                          >
                            <option value="" disabled>
                              选择章节或图表
                            </option>
                            {targets.map((name) => (
                              <option key={name}>{name}</option>
                            ))}
                          </select>
                        </label>
                        <label>
                          修改方式
                          <select
                            value={revisionAction}
                            onChange={(e) => setRevisionAction(e.target.value)}
                          >
                            <option value="shorten">压缩重复说明</option>
                            <option value="clarify">
                              直接说明主要发现（销售结论）
                            </option>
                            <option value="costs">
                              补充费用构成解释（行程费用）
                            </option>
                            <option value="chronological">
                              按每日时间顺序展示（每日行程）
                            </option>
                            <option value="chart_labels">
                              放大文字并提升图表对比度
                            </option>
                          </select>
                        </label>
                        <label>
                          修改意见
                          <textarea
                            value={feedback}
                            maxLength={1500}
                            onChange={(e) => setFeedback(e.target.value)}
                            placeholder="描述这个位置的问题；修改方式会实际调整选中的内容。"
                          />
                        </label>
                        <button
                          disabled={
                            !!busy ||
                            !feedback.trim() ||
                            !targets.includes(target)
                          }
                          onClick={() =>
                            void run("revise", () => decision(review, "revise"))
                          }
                        >
                          提交修改意见（不加价）
                        </button>
                        <p className="hint">
                          新材料、新指标、改变天数或另一语言的完整报告，请使用“基于此版本继续”。
                        </p>
                      </fieldset>
                    )}
                  </div>
                )}
                {!latest && (
                  <p>正在查看历史草稿。请切回最新版后再接受或要求修改。</p>
                )}
              </article>
            );
          })}
        </section>
      )}
    </section>
  );
}
