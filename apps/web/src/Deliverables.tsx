import { useEffect, useRef, useState, type ReactNode } from "react";
import { amount } from "./amount";
type Row = Record<string, any>;
type Api = (path: string, body?: unknown) => Promise<any>;
// Keep content fingerprints in the original files and API, out of user previews.
const fingerprint = /sha[\s_-]?256|\b(?:0x)?[a-f0-9]{64}\b/i;
function reportText(text: string) {
  return text
    .split("\n")
    .filter((line) => !fingerprint.test(line))
    .join("\n");
}

const fileLabels: Record<string, string> = {
  "report.md": "销售分析报告",
  "travel-report.md": "旅行方案",
  "report.html": "网页报告",
  "cleaned.csv": "清洗后的销售数据",
  "cleaning.json": "数据清洗结果",
  "validation.json": "数据质量检查",
  "statistics.json": "销售统计",
  "attractions.json": "景点推荐",
  "hotels.json": "住宿比较",
  "itinerary.json": "每日行程与费用",
  "references.json": "报告来源",
  "travel-references.json": "旅行资料来源",
  "chart-data.json": "图表数据",
  "revenue.svg": "销售额图表",
  "revision.json": "修改记录",
  "source-comparison.csv": "资料对照表",
};
const yuan = (value: unknown) => `${(Number(value || 0) / 100).toFixed(2)} 元`;
function downloadFile(file: Row) {
  const url = URL.createObjectURL(
    new Blob([file.text], {
      type: file.media_type || "text/plain;charset=utf-8",
    }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = file.name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
const fieldLabels: Record<string, string> = {
  total: "原始记录",
  kept: "保留记录",
  removed: "移除记录",
  rejected: "异常记录",
  violations: "发现的问题",
  valid_rows: "有效记录",
  invalid_rows: "异常记录",
  total_rows: "总记录数",
  rows: "记录",
  group: "分组",
  valid: "有效记录",
  invalid: "异常记录",
  total_orders: "有效订单",
  total_revenue_cents: "销售总额",
  revenue_cents: "销售额",
  count: "订单数",
  quantity: "数量",
  quality_invalid_rows: "原始异常记录",
  group_by: "统计维度",
  groups: "分组结果",
  organization: "组织",
  order_id: "订单编号",
  date: "日期",
  category: "类别",
  unit_price_cents: "单价",
  file: "来源文件",
  line: "行号",
  reason: "原因",
  reasons: "发现的问题",
  accepted_rows: "保留记录",
  rejected_rows: "移除记录",
  quarter: "季度",
  month: "月份",
  series_name: "统计项目",
  note: "说明",
  source: "资料来源",
  as_of: "资料日期",
  revision: "修改次数",
  changes: "修改内容",
  target: "修改位置",
  action: "修改方式",
  text: "说明",
  feedback: "修改意见",
  status: "状态",
  name: "名称",
  value: "数值",
  labels: "分组",
  series: "数据系列",
  total_cents: "总费用",
  remaining_cents: "预算余额",
  budget_cents: "预算",
  numerator: "合计",
  denominator: "数量",
  mean_order_cents: "平均订单金额",
  basis: "统计依据",
  request: "出行需求",
  destination: "目的地",
  days: "天数",
  travelers: "人数",
  start_date: "出发日期",
  interests: "偏好",
  title: "标题",
  notes: "说明",
  message: "说明",
};
const valueLabels: Record<string, string> = {
  culture: "历史文化",
  nature: "自然风光",
  family: "亲子",
  food: "美食",
  book: "图书",
  tool: "工具",
  month: "月份",
  organization_quarter: "组织与季度",
  organization: "组织",
  category: "商品类别",
  shorten: "精简说明",
  clarify: "说明主要发现",
  costs: "解释费用构成",
  chronological: "按时间整理",
  chart_labels: "改善图表",
  accepted: "通过",
  rejected: "不通过",
  invalid_date: "日期格式不正确",
  unknown_category: "商品类别不支持",
  duplicate_key: "订单重复",
};
function DataFields({ value, field = "" }: { value: any; field?: string }) {
  if (value == null) return <span>无</span>;
  if (field.endsWith("_cents") && typeof value === "number")
    return <span>{yuan(value)}</span>;
  if (field.endsWith("_cents") && value?.denominator > 0)
    return <span>{yuan(value.numerator / value.denominator)}</span>;
  if (Array.isArray(value))
    return value.length ? (
      <ul className="result-list">
        {value.map((v, i) => (
          <li key={i}>
            <DataFields value={v} />
          </li>
        ))}
      </ul>
    ) : (
      <span>无</span>
    );
  if (typeof value === "object")
    return (
      <dl className="result-fields">
        {Object.entries(value)
          .filter(
            ([k, v]) =>
              !/hash|sha256|signature|uri|^id$|_id$|^inputRefs$|^upstreamRefs$/i.test(
                k,
              ) &&
              !fingerprint.test(String(v)) &&
              (field === "groups" || fieldLabels[k] || !/^[a-z_]+$/i.test(k)),
          )
          .map(([k, v]) => (
            <div key={k}>
              <dt>{fieldLabels[k] || valueLabels[k] || k}</dt>
              <dd>
                <DataFields value={v} field={k} />
              </dd>
            </div>
          ))}
      </dl>
    );
  return (
    <span>
      {typeof value === "boolean"
        ? value
          ? "是"
          : "否"
        : valueLabels[String(value)] ||
          String(value).replace(
            /\b(missing|invalid):([a-z_]+)/g,
            (_, issue, key) =>
              `${fieldLabels[key] || "字段"}${issue === "missing" ? "未填写" : "不符合要求"}`,
          )}
    </span>
  );
}
function DataFile({ file }: { file: Row }) {
  let d: Row;
  try {
    d = JSON.parse(file.text);
  } catch {
    if (file.name.endsWith(".csv")) {
      const rows = file.text
        .trim()
        .split(/\r?\n/)
        .map((line: string) =>
          (line.match(/(?:^|,)(?:"(?:[^"]|"")*"|[^,\r]*)/g) || []).map((v) =>
            v.replace(/^,/, "").replace(/^"|"$/g, "").replace(/""/g, '"'),
          ),
        );
      const header = rows.shift() || [];
      const columns = header
        .map((name: string, i: number) => ({ name, i }))
        .filter((c: Row) => !fingerprint.test(c.name));
      return (
        <div className="report-table">
          <table>
            <thead>
              <tr>
                {columns.map((c: Row) => (
                  <th key={c.i}>
                    {fieldLabels[c.name] ||
                      c.name.replace(/（分）|\(分\)/g, "（元）")}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 100).map((row: string[], i: number) => (
                <tr key={i}>
                  {columns.map((c: Row) => (
                    <td key={c.i}>
                      {c.name.endsWith("_cents") || /（分）|\(分\)/.test(c.name)
                        ? yuan(row[c.i])
                        : valueLabels[row[c.i]] || row[c.i]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length > 100 && <p>预览前 100 行，完整内容可下载。</p>}
        </div>
      );
    }
    return <p>此文件可下载查看。</p>;
  }
  if (file.name === "attractions.json")
    return (
      <div className="result-cards">
        {(d.attractions || []).map((a: Row) => (
          <article key={a.id}>
            <h4>{a.name}</h4>
            <p>{valueLabels[a.category] || a.category}</p>
            <p>
              开放：{a.opens_at}—{a.closes_at} · 建议停留 {a.visit_minutes} 分钟
            </p>
            <strong>门票 {yuan(a.ticket_cents)} / 人</strong>
          </article>
        ))}
      </div>
    );
  if (file.name === "hotels.json")
    return (
      <div className="result-cards">
        {(d.hotels || []).map((h: Row) => (
          <article key={h.id}>
            <h4>{h.name}</h4>
            <p>
              {h.rooms} 间房 · {h.nights} 晚 · 每间可住 {h.room_capacity} 人
            </p>
            <p>每晚 {yuan(h.nightly_cents)}</p>
            <strong>住宿合计 {yuan(h.total_cents)}</strong>
          </article>
        ))}
      </div>
    );
  if (file.name === "itinerary.json")
    return (
      <>
        <p>
          住宿：{d.hotel?.name} · 行程总计{" "}
          <strong>{yuan(d.total_cents)}</strong> · 预算余额{" "}
          {yuan(d.remaining_cents)}
        </p>
        {(d.days || []).map((day: Row, i: number) => (
          <article className="itinerary-day" key={day.date}>
            <h4>
              第 {i + 1} 天 · {day.date}
            </h4>
            {(day.stops || []).map((s: Row) => (
              <p key={s.id}>
                <time>
                  {s.arrive}—{s.depart}
                </time>{" "}
                {s.name} · 门票 {yuan(s.ticket_cents)}
              </p>
            ))}
            <p>
              {day.return_at} 返回住宿点 · 当日交通 {yuan(day.transport_cents)}
            </p>
          </article>
        ))}
      </>
    );
  return <DataFields value={d} />;
}

function previewMarkup(text: string, svg = false) {
  const doc = new DOMParser().parseFromString(
    text,
    svg ? "image/svg+xml" : "text/html",
  );
  doc.querySelectorAll("table").forEach((table) => {
    const rows = Array.from(table.querySelectorAll("tr"));
    const columns = Array.from(rows[0]?.children || []).flatMap(
      (cell, index) =>
        fingerprint.test(cell.textContent || "") ? [index] : [],
    );
    rows.forEach((row) =>
      columns
        .slice()
        .reverse()
        .forEach((index) => row.children[index]?.remove()),
    );
  });
  doc.querySelectorAll("pre").forEach((element) => {
    element.textContent = reportText(element.textContent || "");
  });
  doc
    .querySelectorAll("p, li, td, th, text, tspan, title, desc")
    .forEach((element) => {
      if (fingerprint.test(element.textContent || "")) element.remove();
    });
  return new XMLSerializer().serializeToString(doc);
}

function Report({ text }: { text: string }) {
  const lines = reportText(text).split("\n"),
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
                    {cell.replace(/（分）|\(分\)/g, "（元）")}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, n) => (
                <tr key={n}>
                  {row.map((cell, m) => (
                    <td key={m}>
                      {/（分）|\(分\)/.test(header[m]) && /^\d+$/.test(cell)
                        ? (Number(cell) / 100).toFixed(2)
                        : cell}
                    </td>
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
        <p key={key}>
          {line
            .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
            .replace(/（分）：\s*(\d+)/g, (_, n) => "：" + yuan(n))
            .replace(/(\d+)\s*分(?!钟)/g, (_, n) => yuan(n))}
        </p>,
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

export function Draft({ value }: { value?: Row | null }) {
  if (!value) return <p role="status">正在读取报告…</p>;
  return (
    <div className="draft-preview">
      <div className="deliverable-downloads" aria-label="下载成果">
        {value.files.map((p: Row) => (
          <button key={p.name} onClick={() => downloadFile(p)}>
            下载{fileLabels[p.name] || p.name}
          </button>
        ))}
      </div>
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
              "data:image/svg+xml;charset=utf-8," +
              encodeURIComponent(previewMarkup(p.text, true))
            }
            alt="销售额图表；对应数值可在下方关联数据查看"
          />
        ))}
      {value.files
        .filter((p: Row) => p.name.endsWith(".html"))
        .map((p: Row) => (
          <details key={p.name}>
            <summary>预览离线网页报告</summary>
            <iframe
              title="离线网页报告"
              sandbox=""
              srcDoc={previewMarkup(p.text)}
            />
          </details>
        ))}
      <details>
        <summary>关联数据、引用资料与版本记录</summary>
        {[...value.files, ...(value.related || [])]
          .filter((p: Row) => /\.(json|csv)$/.test(p.name))
          .map((p: Row) => (
            <section key={p.name}>
              <h4>{fileLabels[p.name] || "补充资料"}</h4>
              <DataFile file={p} />
            </section>
          ))}
      </details>
    </div>
  );
}

const reviewLabels: Record<string, string> = {
  AWAITING_REVIEW: "待您验收",
  ACCEPTED: "验收通过",
  REJECTED: "验收不通过",
  REWORKING: "正在按意见修改",
  TIMED_OUT: "验收已超时",
  CANCELED: "已取消",
};
export function ReviewCard({
  api,
  review,
  drafts,
  onChange,
  onSettlement,
}: {
  api: Api;
  review: Row;
  drafts: Record<string, Row>;
  onChange: () => Promise<unknown>;
  onSettlement?: (jobId: string) => void;
}) {
  const [accepted, setAccepted] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  const [mode, setMode] = useState("");
  const [target, setTarget] = useState("");
  const [revisionAction, setRevisionAction] = useState("shorten");
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const index = Math.min(
    selected ?? review.drafts.length - 1,
    review.drafts.length - 1,
  );
  const draft = review.drafts[index],
    value = drafts[draft?.resultHash];
  const latest = index === review.drafts.length - 1;
  const canDecide =
    !accepted &&
    !!value &&
    latest &&
    review.status === "AWAITING_REVIEW" &&
    Date.now() < review.deadline * 1000;
  const travel = !!value?.files.some((f: Row) => f.name === "travel-report.md");
  const targets = travel
    ? ["需求与资料", "住宿", "每日行程", "费用", "限制与来源"]
    : [
        "数据与质量",
        "统计结果",
        "结论与限制",
        "来源",
        ...(value?.files.some((f: Row) => f.name === "revenue.svg")
          ? ["revenue.svg"]
          : []),
      ];
  const actions: Record<string, string> = {
    shorten: "精简重复说明",
    clarify: "说明主要发现",
    costs: "解释费用构成",
    chronological: "按时间整理行程",
    chart_labels: "改善图表文字与对比度",
  };
  const available =
    target === "revenue.svg"
      ? ["chart_labels"]
      : [
          "shorten",
          ...((
            {
              结论与限制: ["clarify"],
              费用: ["costs"],
              每日行程: ["chronological"],
            } as Record<string, string[]>
          )[target] || []),
        ];
  async function decide(action: string) {
    if (!canDecide || busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/api/workspace/reviews/${encodeURIComponent(review.id)}`, {
        decisionId: crypto.randomUUID(),
        resultHash: draft.resultHash,
        action,
        ...(action === "revise"
          ? { target, revisionAction, feedback: feedback.trim() }
          : {}),
      });
      setMode("");
      setSelected(null);
      setFeedback("");
      setNotice(
        action === "accept"
          ? "已验收通过。"
          : action === "reject"
            ? "已记录不通过，本报告不付款，未使用余额将按订单流程退款。"
            : "修改意见已发送。新稿生成后，请再次查看并验收。",
      );
      if (action === "accept") {
        setAccepted(true);
        onSettlement?.(review.jobId);
      }
      await onChange().catch((e) => {
        setError(
          `验收意见已提交，刷新记录失败：${e instanceof Error ? e.message : String(e)}`,
        );
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="review-card" aria-label="报告验收">
      <header className="review-heading">
        <div>
          <h3>{reviewLabels[review.status] || "报告成果"}</h3>
          <p>
            本步骤服务费 {amount(review.price)} 代币 · 验收截止{" "}
            {new Date(review.deadline * 1000).toLocaleString("zh-CN")}
          </p>
        </div>
        <span className="tag">第 {index + 1} 版</span>
      </header>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="review-notice" role="status">
          {notice}
        </p>
      )}
      {(accepted || review.status === "ACCEPTED") && onSettlement && (
        <button onClick={() => onSettlement(review.jobId)}>查看结算</button>
      )}
      {review.status === "AWAITING_REVIEW" && (
        <p className="review-instruction">
          请阅读下方报告或下载文件。符合要求请点“验收通过”；有问题可退回修改或选择“不通过”。
        </p>
      )}
      {review.status === "REWORKING" && (
        <p role="status">
          正在修改报告，当前展示上一版。新稿会自动出现在这里，无需重复提交。
        </p>
      )}
      {review.status === "AWAITING_REVIEW" &&
        Date.now() >= review.deadline * 1000 && (
          <p role="status">
            验收时间已到，正在处理超时结果，不能继续确认本轮报告。
          </p>
        )}
      {review.drafts.length > 1 && (
        <label>
          查看版本
          <select
            aria-label="查看版本"
            value={index}
            onChange={(e) => {
              setSelected(Number(e.target.value));
              setMode("");
            }}
          >
            {review.drafts.map((d: Row, i: number) => (
              <option key={d.resultHash} value={i}>
                第 {i + 1} 版
                {i === review.drafts.length - 1 ? "（最新）" : "（历史）"}
              </option>
            ))}
          </select>
        </label>
      )}
      <Draft value={value} />
      <details className="review-checks">
        <summary>查看系统检查与验收记录</summary>
        <ul>
          {(draft?.checks || []).map((c: string) => (
            <li key={c}>{checkLabels[c] || "检查通过"}</li>
          ))}
        </ul>
        {(review.decisions || []).map((d: Row, i: number) => (
          <p key={i}>
            {d.at ? new Date(d.at * 1000).toLocaleString("zh-CN") : "历史记录"}{" "}
            ·{" "}
            {(
              {
                accept: "验收通过",
                reject: "验收不通过",
                revise: "退回修改",
              } as Row
            )[d.request?.action] || "已记录决定"}
            {d.request?.feedback && `：${d.request.feedback}`}
          </p>
        ))}
        {(review.changes || []).map((c: Row, i: number) => (
          <p key={i}>
            {fileLabels[c.target] || c.target}：{c.text || c.feedback}
          </p>
        ))}
      </details>
      {!latest && <p>当前为历史版本。切换到最新版后可以验收。</p>}
      {canDecide && (
        <div className="review-controls">
          <div className="workspace-actions">
            <button
              className="primary"
              disabled={busy}
              onClick={() => void decide("accept")}
            >
              {busy ? "正在提交…" : "验收通过并结算"}
            </button>
            <button
              disabled={busy || (review.changes || []).length >= 2}
              onClick={() => setMode(mode === "revise" ? "" : "revise")}
            >
              退回修改
              {(review.changes || []).length >= 2 ? "（次数已用完）" : ""}
            </button>
            <button
              className="danger-outline"
              disabled={busy}
              onClick={() => setMode(mode === "reject" ? "" : "reject")}
            >
              不通过
            </button>
          </div>
          {mode === "reject" && (
            <div className="review-reject">
              <p>
                不通过将结束本次报告，不支付这一步的服务费。已经完成并结算的前置工作费用保留，剩余款项可退款。
              </p>
              <button
                className="danger"
                disabled={busy}
                onClick={() => void decide("reject")}
              >
                确认不通过并结束
              </button>
              <button disabled={busy} onClick={() => setMode("")}>
                返回查看
              </button>
            </div>
          )}
          {mode === "revise" && (
            <form
              className="review-revision"
              onSubmit={(e) => {
                e.preventDefault();
                void decide("revise");
              }}
            >
              <h4>
                提出修改意见 · 剩余{" "}
                {Math.max(0, 2 - (review.changes || []).length)} 次免费修改
              </h4>
              <label>
                修改位置
                <select
                  aria-label="修改位置"
                  required
                  value={target}
                  onChange={(e) => {
                    setTarget(e.target.value);
                    setRevisionAction(
                      e.target.value === "revenue.svg"
                        ? "chart_labels"
                        : "shorten",
                    );
                  }}
                >
                  <option value="">选择需要修改的部分</option>
                  {targets.map((t) => (
                    <option key={t} value={t}>
                      {fileLabels[t] || t}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                修改方式
                <select
                  aria-label="修改方式"
                  value={revisionAction}
                  onChange={(e) => setRevisionAction(e.target.value)}
                >
                  {available.map((a) => (
                    <option key={a} value={a}>
                      {actions[a]}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                修改意见
                <textarea
                  aria-label="修改意见"
                  required
                  value={feedback}
                  maxLength={1500}
                  onChange={(e) => setFeedback(e.target.value)}
                  placeholder="说明哪里不符合要求，希望怎样修改"
                />
              </label>
              <button
                disabled={busy || !target || !feedback.trim()}
                type="submit"
              >
                提交修改意见
              </button>
            </form>
          )}
        </div>
      )}
    </article>
  );
}

export function ReviewInbox({
  api,
  jobId,
  onChange,
  onSettlement,
}: {
  api: Api;
  jobId: string;
  onChange: () => Promise<unknown>;
  onSettlement: (jobId: string) => void;
}) {
  const [reviews, setReviews] = useState<Row[]>([]),
    [drafts, setDrafts] = useState<Record<string, Row>>({});
  const [error, setError] = useState("");
  const cache = useRef<Record<string, Row>>({});
  const refreshRef = useRef<() => Promise<void>>(async () => {});
  useEffect(() => {
    let alive = true,
      timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const rows = await api(`/api/workspace/reviews/${jobId}`);
        const hashes: string[] = rows.flatMap((r: Row) =>
          r.drafts.map((d: Row) => d.resultHash),
        );
        const loaded = await Promise.all(
          hashes
            .filter((h) => !cache.current[h])
            .map(async (h) => [h, await api(`/api/workspace/drafts/${h}`)]),
        );
        if (alive) {
          cache.current = { ...cache.current, ...Object.fromEntries(loaded) };
          setDrafts(cache.current);
          setReviews(rows);
          setError("");
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    }
    refreshRef.current = refresh;
    async function tick() {
      await refresh();
      if (alive) timer = setTimeout(tick, 2500);
    }
    void tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [api, jobId]);
  if (!reviews.length && !error) return null;
  return (
    <section className="panel review-inbox" aria-label="成果与验收">
      <div className="panel-heading">
        <h2>成果与验收</h2>
        <span className="tag">
          {reviews.filter((r) => r.status === "AWAITING_REVIEW").length
            ? "有报告等待您处理"
            : "报告与验收记录"}
        </span>
      </div>
      {error && (
        <div role="alert">
          <p>报告读取失败：{error}</p>
          <button onClick={() => void refreshRef.current()}>
            重新读取报告
          </button>
        </div>
      )}
      {reviews.map((r) => (
        <ReviewCard
          key={r.id}
          api={api}
          review={r}
          drafts={drafts}
          onSettlement={onSettlement}
          onChange={async () => {
            await refreshRef.current();
            await onChange();
          }}
        />
      ))}
    </section>
  );
}

export function TaskOutput({
  api,
  jobId,
  taskId,
  revision,
}: {
  api: Api;
  jobId: string;
  taskId: number;
  revision: string;
}) {
  const [value, setValue] = useState<Row | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api(`/api/workspace/jobs/${jobId}/tasks/${taskId}/outputs`)
      .then((v) => {
        if (active) {
          setValue(v);
          setError("");
        }
      })
      .catch((e) => {
        if (active) setError(String(e));
      });
    return () => {
      active = false;
    };
  }, [api, jobId, taskId, revision]);
  if (error) return <p role="alert">成果读取失败，请稍后重新展开。{error}</p>;
  if (!value) return <p role="status">正在读取成果…</p>;
  if (!value.files.length) return <p>本步骤还没有提交成果。</p>;
  return (
    <div className="task-output">
      {value.stage === "candidate" && <p>中间成果，尚待检查。</p>}
      {value.stage === "ended" && <p>本步骤已结束，以下为保留的成果记录。</p>}
      {value.files.map((file: Row) => (
        <section key={file.name}>
          <div className="output-heading">
            <h4>{fileLabels[file.name] || "成果文件"}</h4>
            <button onClick={() => downloadFile(file)}>
              下载{fileLabels[file.name] || file.name}
            </button>
          </div>
          {file.name.endsWith(".md") ? (
            <Report text={file.text} />
          ) : /\.(json|csv)$/.test(file.name) ? (
            <DataFile file={file} />
          ) : (
            <p>可下载查看完整文件。</p>
          )}
        </section>
      ))}
    </div>
  );
}
