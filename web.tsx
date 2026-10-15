import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  type ReactFlowInstance,
  type Edge,
  type Node,
} from "@xyflow/react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Check,
  ChevronRight,
  CircleHelp,
  Coins,
  Copy,
  Database,
  FileCheck2,
  GitBranch,
  Layers3,
  Loader2,
  LockKeyhole,
  Network,
  Plus,
  RefreshCw,
  ShieldCheck,
  Square,
  Wallet,
  X,
} from "lucide-react";
import "@xyflow/react/dist/style.css";
import "./style.css";

type AnyRecord = Record<string, any>;
type Task = {
  taskId: number;
  key: string;
  title: string;
  dependencies: string[];
  onchain: AnyRecord;
  checkpoint: AnyRecord;
};
type Job = {
  jobId: string;
  scenario: string;
  goal: string;
  status: string;
  lastError?: string;
  planningMode: string;
  chain: AnyRecord;
  tasks: Task[];
  domain: AnyRecord;
  token: string;
  createdAt: number;
  fundedAt?: number;
  finishedAt?: number;
};
type Agent = {
  agentId: string;
  endpoint: string;
  wallet: string;
  skills: string[];
  status: string;
  bindingVersion: number;
};
const states: Record<string, string> = {
  ACTIVE: "执行中",
  FUNDING: "托管确认中",
  SUCCEEDED: "已完成",
  PARTIAL_FAILED: "部分失败",
  INTERRUPTED: "已中断",
  BIDDING: "询价中",
  ASSIGNING: "委派确认中",
  RUNNING: "执行中",
  VERIFYING: "验证中",
  SETTLING: "结算确认中",
  SETTLED: "已结算",
  CANCELED: "已取消",
  RECOVERING: "等待旧轮次失效",
  REASSIGNING: "重新委派",
};
const chainStates = ["等待依赖", "已委派", "已结算", "已取消"];
const short = (value?: string) =>
  value ? value.slice(0, 8) + "…" + value.slice(-6) : "—";
function amount(value: string | number | undefined) {
  if (value === undefined) return "—";
  const n = BigInt(value),
    whole = n / 1000000n,
    fraction = (n % 1000000n).toString().padStart(6, "0").replace(/0+$/, "");
  return whole.toString() + (fraction ? "." + fraction : "");
}
function trapDialogTab(event: React.KeyboardEvent<HTMLDialogElement>) {
  if (event.key !== "Tab") return;
  const elements = Array.from(
    event.currentTarget.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]',
    ),
  );
  const first = elements[0],
    last = elements.at(-1);
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last?.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first?.focus();
  }
}
const taskStatus = (t: Task) =>
  t.onchain.state >= 2
    ? chainStates[t.onchain.state]
    : states[t.checkpoint.status] || chainStates[t.onchain.state];
function Badge({ value }: { value: string }) {
  return (
    <span
      className={
        "badge " +
        (["SUCCEEDED", "SETTLED", "ONLINE"].includes(value)
          ? "good"
          : ["PARTIAL_FAILED", "INTERRUPTED", "OFFLINE", "CANCELED"].includes(
                value,
              )
            ? "warn"
            : "")
      }
    >
      <span className="status-dot" />
      {states[value] ||
        ({ ONLINE: "在线", OFFLINE: "离线" } as Record<string, string>)[
          value
        ] ||
        value}
    </span>
  );
}

function App() {
  const [token, setToken] = useState(
    sessionStorage.getItem("agentflow-token") || "",
  );
  const [draftToken, setDraftToken] = useState(""),
    [connected, setConnected] = useState(false);
  const [config, setConfig] = useState<AnyRecord>({}),
    [jobs, setJobs] = useState<AnyRecord[]>([]);
  const [jobId, setJobId] = useState(
    new URLSearchParams(location.search).get("job") || "",
  );
  const [job, setJob] = useState<Job | null>(null),
    [agents, setAgents] = useState<Agent[]>([]);
  const [quotes, setQuotes] = useState<AnyRecord[]>([]),
    [events, setEvents] = useState<AnyRecord[]>([]);
  const [artifacts, setArtifacts] = useState<AnyRecord[]>([]),
    [ledger, setLedger] = useState<AnyRecord[]>([]);
  const [inputs, setInputs] = useState<AnyRecord>({ files: [] }),
    [error, setError] = useState("");
  const [busy, setBusy] = useState(""),
    [tab, setTab] = useState("overview"),
    [selected, setSelected] = useState(0);
  const [createOpen, setCreateOpen] = useState(false),
    [budget, setBudget] = useState("100"),
    [sla, setSla] = useState(30);
  const [copied, setCopied] = useState(""),
    [stopOpen, setStopOpen] = useState(false);
  const [streamStatus, setStreamStatus] = useState("连接中"),
    modalRef = useRef<HTMLDialogElement>(null),
    stopRef = useRef<HTMLDialogElement>(null);
  const selectedJobRef = useRef(jobId);
  const flowRef = useRef<ReactFlowInstance | null>(null);
  const dagRef = useRef<HTMLDivElement>(null);
  const selectJob = useCallback((id: string) => {
    selectedJobRef.current = id;
    setJobId(id);
    setJob(null);
    setSelected(0);
    setEvents([]);
    setQuotes([]);
    setArtifacts([]);
    setLedger([]);
  }, []);
  const api = useCallback(
    async (path: string, body?: unknown) => {
      const response = await fetch(path, {
        method: body === undefined ? "GET" : "POST",
        headers: {
          Authorization: "Bearer " + token,
          "Content-Type": "application/json",
        },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(
          response.status === 401 ? "访问令牌无效，请重新连接。" : text,
        );
      }
      return response.json();
    },
    [token],
  );
  const refresh = useCallback(async () => {
    const [c, js, as, fs] = await Promise.all([
      api("/api/config"),
      api("/api/jobs"),
      api("/api/agents"),
      api("/api/inputs"),
    ]);
    setConfig(c);
    setJobs(js.slice().reverse());
    setAgents(as);
    setInputs(fs);
    setConnected(true);
    if (!selectedJobRef.current && js.length) selectJob(js.at(-1).jobId);
    if (jobId) {
      const [j, q, e, a, l] = await Promise.all([
        api("/api/jobs/" + jobId),
        api(`/api/jobs/${jobId}/quotes`),
        api(`/api/jobs/${jobId}/audit`),
        api(`/api/jobs/${jobId}/artifacts`),
        api(`/api/jobs/${jobId}/settlements`),
      ]);
      // A response for the previous selection must never populate the new Job's view.
      if (selectedJobRef.current !== jobId) return;
      setJob(j);
      setQuotes(q);
      setEvents(e);
      setArtifacts(a);
      setLedger(l.events);
    }
  }, [api, jobId, selectJob]);
  useEffect(() => {
    if (!token) return;
    let cancelled = false,
      timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        await refresh();
        if (!cancelled) setError("");
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
      if (!cancelled) timer = setTimeout(poll, 4000);
    };
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [refresh, token]);
  useEffect(() => {
    if (jobId) history.replaceState(null, "", "?job=" + jobId);
  }, [jobId]);
  useEffect(() => {
    if (!jobId || !token) return;
    const controller = new AbortController();
    let last = 0;
    const listen = async () => {
      while (!controller.signal.aborted) {
        try {
          const response = await fetch(
            `/api/jobs/${jobId}/events?after=${last}`,
            {
              headers: { Authorization: "Bearer " + token },
              signal: controller.signal,
            },
          );
          if (!response.ok || !response.body) throw new Error("事件流不可用");
          setStreamStatus("实时连接");
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          while (!controller.signal.aborted) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let end: number;
            while ((end = buffer.indexOf("\n\n")) >= 0) {
              const block = buffer.slice(0, end);
              buffer = buffer.slice(end + 2);
              const data = block
                .split("\n")
                .find((x) => x.startsWith("data: "));
              if (data) {
                const event = JSON.parse(data.slice(6));
                last = Math.max(last, event.seq);
                setEvents((old) =>
                  old.some((x) => x.seq === event.seq) ? old : [...old, event],
                );
              }
            }
          }
        } catch {
          if (!controller.signal.aborted) setStreamStatus("重连中，保留轮询");
        }
        if (!controller.signal.aborted)
          await new Promise((r) => setTimeout(r, 1500));
      }
    };
    void listen();
    return () => controller.abort();
  }, [jobId, token]);
  useEffect(() => {
    if (createOpen) modalRef.current?.showModal();
    else modalRef.current?.close();
  }, [createOpen]);
  useEffect(() => {
    if (stopOpen) stopRef.current?.showModal();
    else stopRef.current?.close();
  }, [stopOpen]);
  useEffect(() => {
    const element = dagRef.current;
    if (!element) return;
    const observer = new ResizeObserver(
      () => void flowRef.current?.fitView({ padding: 0.15 }),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [job?.jobId, tab]);
  useEffect(() => {
    setEvents([]);
    setQuotes([]);
    setArtifacts([]);
    setLedger([]);
  }, [jobId]);
  async function action(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    setError("");
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy("");
    }
  }
  async function createJob() {
    await action("create", async () => {
      const r = await api("/api/jobs", {
        deposit: budget,
        slaSeconds: sla,
        requestId: crypto.randomUUID(),
      });
      selectJob(r.jobId);
      setTab("overview");
      setCreateOpen(false);
    });
  }
  async function download(ref: AnyRecord) {
    await action("download", async () => {
      const r = await fetch(`/api/jobs/${jobId}/artifacts/${ref.sha256}`, {
        headers: { Authorization: "Bearer " + token },
      });
      if (!r.ok) throw new Error("下载失败");
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = ref.name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  async function copy(value: string) {
    await navigator.clipboard.writeText(value);
    setCopied(value);
    setTimeout(() => setCopied(""), 1500);
  }
  const graph = useMemo(() => {
    const nodes: Node[] = [],
      edges: Edge[] = [];
    const ranks: Record<string, number> = {},
      columns: Record<number, number> = {};
    for (const t of job?.tasks || []) {
      const rank = t.dependencies.length
        ? Math.max(...t.dependencies.map((d) => ranks[d] || 0)) + 1
        : 0;
      ranks[t.key] = rank;
      const col = columns[rank] || 0;
      columns[rank] = col + 1;
      nodes.push({
        id: t.key,
        position: { x: rank * 250, y: col * 145 },
        sourcePosition: Position.Right,
        targetPosition: Position.Left,
        className:
          "task-node " +
          (t.taskId === selected ? "is-selected " : "") +
          "state-" +
          t.onchain.state,
        data: {
          label: (
            <div>
              <div className="node-top">
                <span>T{t.taskId + 1}</span>
                <span>{taskStatus(t)}</span>
              </div>
              <strong>{t.title}</strong>
              <div className="node-bottom">
                <span>
                  {amount(
                    t.onchain.acceptedPrice || t.onchain.config.budgetCap,
                  )}{" "}
                  代币
                </span>
                <span>第 {t.onchain.attempt + 1} 轮</span>
              </div>
            </div>
          ),
        },
      });
      t.dependencies.forEach((d) =>
        edges.push({
          id: d + "-" + t.key,
          source: d,
          target: t.key,
          type: "smoothstep",
          markerEnd: { type: MarkerType.ArrowClosed },
          style: { stroke: "#607f90", strokeWidth: 1.5 },
        }),
      );
    }
    return { nodes, edges };
  }, [job, selected]);
  const currentTask = job?.tasks[selected];
  const visibleQuotes = quotes
    .filter((q) => q.taskId === selected)
    .flatMap((q) =>
      q.candidates.map((c: AnyRecord) => ({
        ...c,
        attempt: q.attempt,
        chosen: q.selected?.agentId === c.agentId,
      })),
    );
  const nav = [
    ["overview", "协作任务", GitBranch],
    ["market", "服务市场", Network],
    ["funds", "资金账本", Wallet],
    ["evidence", "成果与证据", FileCheck2],
  ] as const;
  if (!token || !connected)
    return (
      <div className="login">
        <div className="login-card">
          <div className="brand">
            <Layers3 /> AgentFlow <span>Pay</span>
          </div>
          <span className="eyebrow">LOCAL WORKSPACE</span>
          <h1>连接协作控制台</h1>
          <p>
            使用服务管理员配置的访问令牌连接协作工作区。
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              sessionStorage.setItem("agentflow-token", draftToken);
              setToken(draftToken);
            }}
          >
            <label htmlFor="token">访问令牌</label>
            <input
              id="token"
              type="password"
              autoComplete="off"
              value={draftToken}
              onChange={(e) => setDraftToken(e.target.value)}
              required
            />
            <p className="hint">令牌由服务管理员通过 AGENTFLOW_API_TOKEN 配置。</p>
            <button className="primary" type="submit">
              连接工作区 <ArrowRight size={18} />
            </button>
          </form>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {token && !error && <p role="status">正在连接本地服务…</p>}
        </div>
      </div>
    );
  return (
    <div className="shell">
      <a className="skip" href="#main">
        跳到主要内容
      </a>
      <aside className="sidebar">
        <div className="brand">
          <Layers3 />{" "}
          <span>
            AgentFlow <b>Pay</b>
          </span>
        </div>
        <div className="workspace">
          <span className="workspace-icon">A</span>
          <div>
            <strong>协作实验室</strong>
            <small>协作工作区</small>
          </div>
        </div>
        <span className="nav-label">工作区</span>
        <nav>
          {nav.map(([id, title, Icon]) => (
            <button
              key={id}
              className={tab === id ? "active" : ""}
              onClick={() => setTab(id)}
              aria-current={tab === id ? "page" : undefined}
            >
              <Icon size={19} />
              {title}
              {tab === id && <ChevronRight size={16} />}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <ShieldCheck size={20} />
          <strong>DASC · 依赖感知结算</strong>
          <p>报酬由任务状态和有效凭证共同约束。</p>
          <small>Anvil · Chain {config.chainId}</small>
        </div>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <span>
            工作区 <ChevronRight size={14} />{" "}
            {nav.find((n) => n[0] === tab)?.[1]}
          </span>
          <div>
            <span className="local-pill">
              <span className="status-dot" />
              已连接区块链
            </span>
            <button
              className="icon-button"
              title="刷新数据"
              aria-label="刷新数据"
              onClick={() => void action("refresh", refresh)}
            >
              <RefreshCw size={17} />
            </button>
            <button
              className="identity"
              onClick={() => {
                sessionStorage.removeItem("agentflow-token");
                setToken("");
                setConnected(false);
              }}
              title="断开连接"
            >
              <span>U</span>
              {short(config.owner)}
            </button>
          </div>
        </header>
        <main id="main">
          <div className="page-heading">
            <div>
              <div className="eyebrow">AUTONOMOUS COLLABORATION</div>
              <h1>{nav.find((n) => n[0] === tab)?.[1]}</h1>
              <p>从服务发现到成果交付，追踪每个节点和每笔报酬。</p>
            </div>
            <button className="primary" onClick={() => setCreateOpen(true)}>
              <Plus size={18} />
              创建分析任务
            </button>
          </div>
          <div className="mode-note">
            <LockKeyhole size={16} />
            <span>
              任务依赖调度 · 独立成果验证 · A2A 服务协作 · 合约托管结算
            </span>
          </div>
          {error && (
            <div className="error" role="alert">
              {error}
              <button onClick={() => void action("refresh", refresh)}>
                重试
              </button>
            </div>
          )}
          <div className="job-picker">
            <label htmlFor="job">当前任务</label>
            <select
              id="job"
              value={jobId}
              onChange={(e) => {
                selectJob(e.target.value);
              }}
            >
              <option value="" disabled>
                尚未创建任务
              </option>
              {jobs.map((j) => (
                <option key={j.jobId} value={j.jobId}>
                  {short(j.jobId)} · {states[j.status] || j.status}
                </option>
              ))}
            </select>
            {job && <Badge value={job.status} />}
            <span className="stream">
              <Activity size={14} />
              {jobId ? streamStatus : "等待任务"}
            </span>
          </div>
          {tab === "market" ? (
            <>
              <section className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>可发现的服务 Agent</h2>
                    <p>
                      {agents.filter((a) => a.status === "ONLINE").length}{" "}
                      个在线实例 · 身份经过端点签名挑战
                    </p>
                  </div>
                  <Network />
                </div>
                <div className="agent-grid">
                  {agents.map((a) => (
                    <div className="agent-card" key={a.agentId}>
                      <div className="agent-title">
                        <span className="agent-avatar">
                          <Network size={19} />
                        </span>
                        <strong>{a.agentId}</strong>
                        <Badge value={a.status} />
                      </div>
                      <p>{a.skills.join(" / ")}</p>
                      <div className="row">
                        <span>钱包</span>
                        <code title={a.wallet}>{short(a.wallet)}</code>
                      </div>
                      <div className="row">
                        <span>注册版本</span>
                        <span>v{a.bindingVersion}</span>
                      </div>
                      <small>{a.endpoint}</small>
                    </div>
                  ))}
                </div>
              </section>
            </>
          ) : !job ? (
            <section className="panel empty">
              <GitBranch size={36} />
              <h2>{jobId ? "正在读取任务…" : "创建你的第一个协作任务"}</h2>
              <p>使用已配置的数据完成清洗、校验、统计和报告生成。</p>
              {!jobId && (
                <button className="primary" onClick={() => setCreateOpen(true)}>
                  查看计划并开始 <ArrowRight size={16} />
                </button>
              )}
            </section>
          ) : (
            <>
              <div className="stats-grid">
                {[
                  ["托管预算", job.chain.deposit, Coins],
                  [
                    "已确认报酬",
                    BigInt(job.chain.agentCredits) +
                      BigInt(job.chain.withdrawn),
                    ShieldCheck,
                  ],
                  ["可领取退款", job.chain.refundCredit, ArrowDownToLine],
                  ["已退还用户", job.chain.refunded, Wallet],
                ].map(([title, value, Icon]: any) => (
                  <div className="metric" key={title}>
                    <div>
                      <span>{title}</span>
                      <Icon size={18} />
                    </div>
                    <strong>
                      {amount(String(value))}
                      <small>代币</small>
                    </strong>
                  </div>
                ))}
              </div>
              {tab === "overview" && (
                <>
                  <section className="panel">
                    <div className="panel-heading">
                      <div>
                        <h2>跨组织销售分析</h2>
                        <p>清洗与原始校验并行执行，统计和报告按依赖推进。</p>
                      </div>
                      <div className="job-actions">
                        <span className="mono">{short(job.jobId)}</span>
                        <button
                          className="icon-button"
                          aria-label="复制任务 ID"
                          onClick={() => void copy(job.jobId)}
                        >
                          {copied === job.jobId ? (
                            <Check size={16} />
                          ) : (
                            <Copy size={16} />
                          )}
                        </button>
                        {!["SUCCEEDED", "PARTIAL_FAILED"].includes(
                          job.status,
                        ) && (
                          <button
                            className="danger-outline"
                            onClick={() => setStopOpen(true)}
                          >
                            <Square size={13} />
                            停止新委派
                          </button>
                        )}
                      </div>
                    </div>
                    <div className="dag" ref={dagRef} aria-label="任务依赖图">
                      <ReactFlow
                        onInit={(instance) => {
                          flowRef.current = instance;
                        }}
                        nodes={graph.nodes}
                        edges={graph.edges}
                        fitView
                        fitViewOptions={{ padding: 0.2 }}
                        nodesDraggable={false}
                        nodesConnectable={false}
                        onNodeClick={(_, n) =>
                          setSelected(
                            job.tasks.findIndex((t) => t.key === n.id),
                          )
                        }
                        colorMode="dark"
                        minZoom={0.3}
                        maxZoom={1.5}
                      >
                        <Background gap={24} size={1} />
                        <Controls showInteractive={false} />
                      </ReactFlow>
                    </div>
                    <div className="graph-legend">
                      <span>
                        <i className="legend-dot completed" />
                        已结算
                      </span>
                      <span>
                        <i className="legend-dot running" />
                        执行或确认中
                      </span>
                      <span>
                        <i className="legend-dot waiting" />
                        等待委派
                      </span>
                      <span>点击节点查看报价与执行证据</span>
                    </div>
                  </section>
                  <div className="detail-grid">
                    <section className="panel">
                      <div className="panel-heading">
                        <div>
                          <h2>节点详情</h2>
                          <p>
                            {currentTask?.title} · 第{" "}
                            {(currentTask?.onchain.attempt || 0) + 1} 轮
                          </p>
                        </div>
                        <span className="tag">
                          {currentTask ? taskStatus(currentTask) : "—"}
                        </span>
                      </div>
                      {currentTask && (
                        <>
                          <div className="detail-fields">
                            <div>
                              <span>预算上限</span>
                              <strong>
                                {amount(currentTask.onchain.config.budgetCap)}{" "}
                                代币
                              </strong>
                            </div>
                            <div>
                              <span>成交金额</span>
                              <strong>
                                {currentTask.onchain.state === 0
                                  ? "—"
                                  : amount(currentTask.onchain.acceptedPrice) +
                                    " 代币"}
                              </strong>
                            </div>
                            <div>
                              <span>当前执行者</span>
                              <strong>
                                {currentTask.checkpoint.selected?.agentId ||
                                  "尚未选择"}
                              </strong>
                            </div>
                            <div>
                              <span>验收结果</span>
                              <strong>
                                {currentTask.checkpoint.verification?.passed
                                  ? "客观规则通过"
                                  : "等待验证"}
                              </strong>
                            </div>
                          </div>
                          <p className="hint">
                            {currentTask.checkpoint.reason ||
                              "报价、结果和验证证明均绑定此任务及执行轮次。"}
                          </p>
                        </>
                      )}
                      <div className="table-wrap">
                        <table>
                          <thead>
                            <tr>
                              <th>候选服务</th>
                              <th>报价</th>
                              <th>选择结果</th>
                            </tr>
                          </thead>
                          <tbody>
                            {visibleQuotes.length ? (
                              visibleQuotes.map((q, i) => (
                                <tr key={i}>
                                  <td>
                                    {q.agentId}
                                    <small>轮次 {q.attempt + 1}</small>
                                  </td>
                                  <td>
                                    {q.valid
                                      ? amount(q.quote.price) + " 代币"
                                      : "—"}
                                  </td>
                                  <td>
                                    {q.valid ? (
                                      q.chosen ? (
                                        <span className="text-good">
                                          已选择
                                        </span>
                                      ) : (
                                        "合格候选"
                                      )
                                    ) : (
                                      <span title={q.reason}>已拒绝</span>
                                    )}
                                  </td>
                                </tr>
                              ))
                            ) : (
                              <tr>
                                <td colSpan={3}>节点就绪后开始询价</td>
                              </tr>
                            )}
                          </tbody>
                        </table>
                      </div>
                    </section>
                    <section className="panel timeline-panel">
                      <div className="panel-heading">
                        <div>
                          <h2>执行时间线</h2>
                          <p>来自服务事件与交易确认</p>
                        </div>
                        <Activity size={18} />
                      </div>
                      <div className="timeline">
                        {events
                          .slice(-10)
                          .reverse()
                          .map((e) => (
                            <div className="timeline-item" key={e.seq}>
                              <span className="timeline-dot" />
                              <div>
                                <strong>
                                  {states[e.kind] ||
                                    (
                                      {
                                        JOB_FUNDED: "托管已确认",
                                        TX_CONFIRMED: "链上交易确认",
                                        QUOTES_RECEIVED: "收到候选报价",
                                        WORKER_INTERRUPTED: "执行已中断",
                                      } as Record<string, string>
                                    )[e.kind] ||
                                    e.kind}
                                </strong>
                                <p>
                                  {e.payload.operation ||
                                    e.payload.reason ||
                                    ("taskId" in e.payload
                                      ? "任务 T" + (e.payload.taskId + 1)
                                      : "任务状态更新")}
                                </p>
                                <time>
                                  {new Date(
                                    e.created * 1000,
                                  ).toLocaleTimeString()}
                                </time>
                              </div>
                            </div>
                          ))}
                      </div>
                    </section>
                  </div>
                </>
              )}
              {tab === "funds" && (
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <h2>逐任务结算账本</h2>
                      <p>结算记入待提现报酬，提现才会将代币转入钱包。</p>
                    </div>
                    <button
                      disabled={!!busy || !job.chain.finalized}
                      className="primary"
                      onClick={() =>
                        void action("claims", () =>
                          api(`/api/jobs/${jobId}/claims`, {}),
                        )
                      }
                    >
                      {busy === "claims" ? (
                        <Loader2 className="spin" size={17} />
                      ) : (
                        <ArrowDownToLine size={17} />
                      )}
                      领取报酬与退款
                    </button>
                  </div>
                  <div className="ledger-balances">
                    <span>
                      任务预留 <strong>{amount(job.chain.reserved)}</strong>
                    </span>
                    <span>
                      未使用 <strong>{amount(job.chain.unallocated)}</strong>
                    </span>
                    <span>
                      待提现报酬{" "}
                      <strong>{amount(job.chain.agentCredits)}</strong>
                    </span>
                    <span>
                      已提现 <strong>{amount(job.chain.withdrawn)}</strong>
                    </span>
                  </div>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>链上事件</th>
                          <th>任务</th>
                          <th>金额 代币</th>
                          <th>区块</th>
                          <th>交易哈希</th>
                        </tr>
                      </thead>
                      <tbody>
                        {ledger.map((e, i) => (
                          <tr key={i}>
                            <td>{e.event}</td>
                            <td>
                              {e.args.taskId !== undefined
                                ? "T" + (e.args.taskId + 1)
                                : "整单"}
                            </td>
                            <td>
                              {amount(
                                e.args.amount ??
                                  e.args.price ??
                                  e.args.deposit ??
                                  e.args.refund,
                              )}
                            </td>
                            <td>#{e.blockNumber}</td>
                            <td>
                              <button
                                className="hash-button"
                                onClick={() => void copy(e.transactionHash)}
                                title={e.transactionHash}
                              >
                                {short(e.transactionHash)}
                                {copied === e.transactionHash ? (
                                  <Check size={14} />
                                ) : (
                                  <Copy size={14} />
                                )}
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="hint">
                    金额按配置的六位小数代币显示；Gas 使用对应链的原生币单独支付。
                  </p>
                </section>
              )}
              {tab === "evidence" && (
                <>
                  <section className="panel">
                    <div className="panel-heading">
                      <div>
                        <h2>成果交付与内容承诺</h2>
                        <p>文件哈希、输入谱系与验证策略可以沿任务逐项核对。</p>
                      </div>
                      <FileCheck2 />
                    </div>
                    {artifacts.length ? (
                      artifacts.map((d) => (
                        <div className="artifact-group" key={d.resultHash}>
                          <div>
                            <span className="tag">
                              T{d.manifest.taskId + 1} · 轮次{" "}
                              {d.manifest.attempt + 1}
                            </span>
                            <code title={d.resultHash}>
                              {short(d.resultHash)}
                            </code>
                            <Badge
                              value={
                                job.tasks[d.manifest.taskId].onchain
                                  .resultHash === d.resultHash
                                  ? "SETTLED"
                                  : "未结算成果"
                              }
                            />
                          </div>
                          {d.manifest.artifacts.map((a: AnyRecord) => (
                            <div className="artifact" key={a.sha256}>
                              <FileCheck2 size={20} />
                              <div>
                                <strong>{a.name}</strong>
                                <small>
                                  {(a.size / 1024).toFixed(1)} KB ·{" "}
                                  {a.media_type}
                                </small>
                              </div>
                              <code title={a.sha256}>{short(a.sha256)}</code>
                              <button
                                aria-label={"下载 " + a.name}
                                onClick={() => void download(a)}
                              >
                                <ArrowDownToLine size={16} />
                                下载
                              </button>
                            </div>
                          ))}
                        </div>
                      ))
                    ) : (
                      <div className="empty">
                        <p>Agent 交付后会在这里显示实际成果。</p>
                      </div>
                    )}
                  </section>
                  <div className="trust-note">
                    <CircleHelp size={20} />
                    <p>
                      签名和哈希证明身份与内容绑定。付款依赖授权验证者按固定规则履职；它们不能单独证明原始数据真实，也不能消除恶意授权验证者的风险。
                    </p>
                  </div>
                </>
              )}
            </>
          )}
          <footer>
            <span>AgentFlow Pay · DASC v1</span>
            <span>独立履约 · 依赖约束 · 可核验结算</span>
          </footer>
        </main>
      </div>
      <dialog
        ref={modalRef}
        onCancel={() => setCreateOpen(false)}
        onKeyDown={trapDialogTab}
        aria-labelledby="create-title"
      >
        <div className="modal-heading">
          <div>
            <span className="eyebrow">NEW COLLABORATION</span>
            <h2 id="create-title">确认任务计划</h2>
          </div>
          <button
            aria-label="关闭创建窗口"
            className="icon-button"
            onClick={() => setCreateOpen(false)}
          >
            <X size={20} />
          </button>
        </div>
        <p>分析已配置的销售数据，生成可复算季度统计与报告。</p>
        <div className="input-list">
          <Database size={20} />
          <div>
            <strong>{inputs.files?.length || 0} 份输入文件</strong>
            <small>
              {inputs.files
                ?.reduce((s: number, f: AnyRecord) => s + f.rows, 0)
                .toLocaleString()}{" "}
              行输入数据
            </small>
          </div>
        </div>
        <div className="plan-steps">
          {[
            ["01", "数据清洗", "25"],
            ["02", "原始数据校验", "20"],
            ["03", "统计分析", "30"],
            ["04", "报告生成", "15"],
          ].map(([i, t, b]) => (
            <div key={i}>
              <span>{i}</span>
              <strong>{t}</strong>
              <small>上限 {b} 代币</small>
            </div>
          ))}
        </div>
        <div className="form-row">
          <div>
            <label htmlFor="budget">总托管预算（代币）</label>
            <input
              id="budget"
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              inputMode="decimal"
            />
          </div>
          <div>
            <label htmlFor="sla">每轮结算时限（秒）</label>
            <input
              id="sla"
              type="number"
              min={10}
              max={120}
              value={sla}
              onChange={(e) => setSla(Number(e.target.value))}
            />
          </div>
        </div>
        <p className="hint">
          节点预算合计
          90。成功节点按成交价结算，未使用部分在全部节点终止后退款。当前使用管理员配置的业务钱包。
        </p>
        <div className="modal-actions">
          <button onClick={() => setCreateOpen(false)}>返回</button>
          <button
            className="primary"
            disabled={
              !!busy ||
              !/^\d+(\.\d{1,6})?$/.test(budget) ||
              Number(budget) < 90 ||
              sla < 10
            }
            onClick={() => void createJob()}
          >
            {busy === "create" ? (
              <Loader2 className="spin" size={17} />
            ) : (
              <LockKeyhole size={17} />
            )}
            托管预算并开始
          </button>
        </div>
      </dialog>
      <dialog
        ref={stopRef}
        onCancel={() => setStopOpen(false)}
        onKeyDown={trapDialogTab}
        className="stop-dialog"
        aria-labelledby="stop-title"
      >
        <h2 id="stop-title">停止后续新委派？</h2>
        <p>
          已经接受的委派保留履约窗口，已确认的报酬仍属于执行者。剩余资金在任务收尾后可退。
        </p>
        <div className="modal-actions">
          <button autoFocus onClick={() => setStopOpen(false)}>
            继续运行
          </button>
          <button
            className="danger"
            disabled={!!busy}
            onClick={() =>
              void action("stop", async () => {
                await api(`/api/jobs/${jobId}/stop`, {});
                setStopOpen(false);
              })
            }
          >
            停止新委派
          </button>
        </div>
      </dialog>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

/* @agentflow-styles
@import "tailwindcss" source(none);
@source "./main.tsx";

:root {
  font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
  color: #edf3f8;
  background: #0b1420;
  font-synthesis: none;
  text-rendering: optimizeLegibility;
  -webkit-font-smoothing: antialiased;
  color-scheme: dark;
  --muted: #9bafc2;
  --line: #2b3b4b;
  --card: #121f2e;
  --accent: #edc56c;
  --green: #81dbc0;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
  min-width: 320px;
}
button,
input,
select {
  font: inherit;
}
button {
  cursor: pointer;
  display: inline-flex;
  justify-content: center;
  align-items: center;
  gap: 8px;
  min-height: 44px;
  padding: 10px 15px;
  background: #1d3042;
  border: 1px solid #41556a;
  border-radius: 7px;
  color: #edf3f8;
  font-size: 13px;
  font-weight: 600;
  transition:
    background 0.15s,
    border-color 0.15s;
}
button:hover {
  background: #29425a;
  border-color: #7393af;
}
button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
button:focus-visible,
a:focus-visible,
input:focus-visible,
select:focus-visible {
  outline: 3px solid #efc86e;
  outline-offset: 3px;
}
input,
select {
  width: 100%;
  height: 44px;
  border: 1px solid #46586a;
  border-radius: 6px;
  background: #0f1b28;
  color: #f1f5f9;
  padding: 9px 12px;
}
label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: #dce6ef;
  margin-bottom: 9px;
}
p {
  line-height: 1.75;
  margin: 0;
  color: var(--muted);
}
h1,
h2,
h3 {
  margin: 0;
  font-weight: 650;
  letter-spacing: -0.02em;
}
h1 {
  font-size: 30px;
  line-height: 1.4;
}
h2 {
  font-size: 17px;
  line-height: 1.5;
}
small {
  font-size: 12px;
  color: var(--muted);
}
code,
.mono {
  font-family: Consolas, monospace;
  font-size: 12px;
}
svg {
  flex-shrink: 0;
}
a {
  color: #a9d8ef;
  text-decoration: none;
}
.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #192535;
  box-shadow: 0 1px 2px #0003;
}
.primary:hover {
  background: #f5d995;
  border-color: #f5d995;
}
.icon-button {
  height: 44px;
  min-width: 44px;
  width: 44px;
  padding: 0;
  background: transparent;
  border-color: transparent;
}
.icon-button:hover {
  background: #26394b;
}
.eyebrow {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.17em;
  color: #c4a970;
  margin-bottom: 9px;
}
.hint {
  font-size: 12px;
  line-height: 1.8;
  color: #a9b9c9;
  margin: 16px 0;
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
.skip {
  position: fixed;
  z-index: 100;
  top: 8px;
  left: 8px;
  transform: translateY(-150%);
  background: #edc56c;
  color: #14202c;
  padding: 14px;
}
.skip:focus {
  transform: translateY(0);
}
.shell {
  display: grid;
  grid-template-columns: 230px minmax(0, 1fr);
  min-height: 100vh;
}
.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  border-right: 1px solid var(--line);
  background: #101b29;
  padding: 30px 19px 23px;
  display: flex;
  flex-direction: column;
}
.brand {
  display: flex;
  gap: 10px;
  align-items: center;
  white-space: nowrap;
  font-size: 19px;
  font-weight: 700;
  letter-spacing: -0.7px;
}
.brand > svg {
  color: var(--accent);
  width: 26px;
  height: 26px;
}
.brand b,
.brand > span:last-child:not(:first-child) {
  color: inherit;
}
.brand b {
  font-weight: 500;
  color: var(--accent);
}
.workspace {
  display: flex;
  gap: 10px;
  align-items: center;
  background: #182535;
  border: 1px solid #2e3e50;
  padding: 13px 10px;
  margin: 34px 0 28px;
  border-radius: 7px;
}
.workspace-icon,
.identity > span {
  display: grid;
  place-items: center;
  background: #314856;
  color: #d8eadd;
  border-radius: 5px;
  width: 30px;
  height: 32px;
  font-weight: 700;
}
.workspace strong {
  font-size: 13px;
  display: block;
}
.workspace small {
  display: block;
  font-size: 10px;
  margin-top: 5px;
}
.nav-label {
  color: #7e94a9;
  font-size: 10px;
  letter-spacing: 0.12em;
  padding-left: 12px;
  margin-bottom: 11px;
}
.sidebar nav {
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.sidebar nav button {
  background: transparent;
  border-color: transparent;
  justify-content: flex-start;
  padding: 13px 12px;
  color: #adbfce;
  gap: 12px;
  font-weight: 500;
}
.sidebar nav button > svg:last-child:not(:first-child) {
  margin-left: auto;
}
.sidebar nav button:hover {
  color: #fff;
  background: #1b2b3c;
}
.sidebar nav button.active {
  color: #f5d897;
  background: #2a2c2a;
  border-color: #574d36;
  box-shadow: inset 3px 0 0 #d9b85f;
}
.sidebar-foot {
  margin-top: auto;
  padding: 24px 10px 0;
  color: var(--green);
}
.sidebar-foot strong {
  font-size: 12px;
  display: block;
  margin: 12px 0 8px;
  color: #d8e6ee;
}
.sidebar-foot p {
  font-size: 11px;
  line-height: 1.9;
  margin-bottom: 15px;
}
.sidebar-foot small {
  font-size: 10px;
  color: #b9a984;
}
.workspace-main {
  min-width: 0;
}
.topbar {
  height: 76px;
  border-bottom: 1px solid var(--line);
  padding: 0 38px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #0f1926;
}
.topbar > span,
.topbar > div {
  display: flex;
  align-items: center;
  gap: 11px;
  font-size: 12px;
  color: #a6b7c7;
}
.topbar > div {
  gap: 14px;
}
.local-pill {
  display: flex;
  gap: 7px;
  align-items: center;
  font-size: 11px;
  color: #abd7c8;
}
.status-dot {
  width: 6px;
  height: 6px;
  background: currentColor;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}
.identity {
  background: transparent;
  border: 0;
  padding: 0;
  gap: 9px;
  font-family: Consolas, monospace;
  font-size: 11px;
  color: #b2c2d1;
}
.identity > span {
  font-family: "Segoe UI", sans-serif;
  width: 29px;
  height: 29px;
  background: #354858;
  color: #e7eef5;
}
main {
  max-width: 1680px;
  margin: auto;
  padding: 34px 38px 0;
}
.page-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 22px;
  margin-bottom: 25px;
}
.page-heading p {
  font-size: 12px;
  margin-top: 9px;
}
.page-heading .primary {
  font-size: 12px;
  min-height: 44px;
  padding: 11px 17px;
}
.mode-note {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #122535;
  border: 1px solid #2b4458;
  border-radius: 6px;
  padding: 10px 13px;
  font-size: 11px;
  line-height: 1.7;
  color: #bdd5e5;
  margin-bottom: 24px;
}
.mode-note > svg {
  color: #8ebfd6;
}
.job-picker {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 21px;
  flex-wrap: wrap;
}
.job-picker label {
  margin: 0;
  font-size: 12px;
  color: #b0c1cf;
}
.job-picker select {
  width: 270px;
  font-size: 12px;
  height: 40px;
}
.stream {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  color: #9fb3c5;
}
.badge {
  display: inline-flex;
  align-items: center;
  white-space: nowrap;
  gap: 6px;
  padding: 5px 8px;
  border: 1px solid #496377;
  background: #1c3042;
  border-radius: 4px;
  color: #c0d8e9;
  font-size: 10px;
  line-height: 1.3;
}
.badge.good {
  color: #91dfc4;
  border-color: #31594d;
  background: #173a33;
}
.badge.warn {
  color: #efd08a;
  border-color: #715c33;
  background: #342c1c;
}
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 24px;
}
.metric {
  padding: 19px 20px;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.metric > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: #bacbda;
  gap: 8px;
}
.metric > div svg {
  color: #8aa9c0;
}
.metric > strong {
  display: block;
  font-size: 30px;
  font-weight: 600;
  margin-top: 14px;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
}
.metric small {
  font-size: 10px;
  margin-left: 9px;
  color: #a0b3c5;
  font-weight: 400;
}
.panel {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 8px;
  margin-bottom: 23px;
  overflow: hidden;
}
.panel-heading {
  padding: 22px 24px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}
.panel-heading p {
  font-size: 11px;
  margin-top: 6px;
}
.panel-heading > svg {
  color: #8fabbf;
}
.job-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.job-actions > .mono {
  color: #8ea4b7;
  font-size: 10px;
}
.job-actions .icon-button {
  width: 32px;
  min-width: 32px;
}
.danger-outline {
  background: transparent;
  color: #e2b89d;
  border: 1px solid #715346;
  font-size: 10px;
  min-height: 36px;
  padding: 8px 9px;
}
.danger-outline:hover {
  background: #3c2927;
  border-color: #a37560;
}
.danger {
  color: #fff;
  background: #a64136;
  border-color: #bc6358;
}
.danger:hover {
  background: #bd5448;
}
.dag {
  height: 330px;
  background: #0f1a27;
  border-top: 1px solid #2a3b4a;
  border-bottom: 1px solid #2a3b4a;
}
.react-flow {
  --xy-background-pattern-dots-color: #355068;
  --xy-controls-button-background-color: #1d3043;
  --xy-controls-button-color: #d8e3ee;
  --xy-controls-button-background-color-hover: #354b60;
  --xy-controls-button-border-color: #45586b;
}
.react-flow__node.task-node {
  padding: 15px 16px;
  width: 205px;
  text-align: left;
  background: #192b3e;
  border: 1px solid #536f87;
  border-radius: 7px;
  color: #ecf3fa;
  box-shadow: 0 3px 12px #0002;
}
.task-node.state-2 {
  background: #19382f;
  border-color: #3a7e66;
}
.task-node.state-3 {
  background: #34291f;
  border-color: #826342;
}
.task-node.is-selected {
  box-shadow: 0 0 0 2px #ecc770;
  outline: 0;
}
.task-node .node-top {
  display: flex;
  justify-content: space-between;
  font-size: 9px;
  color: #aec4d7;
  gap: 6px;
  margin-bottom: 12px;
}
.task-node.state-2 .node-top {
  color: #9de1c8;
}
.task-node strong {
  font-size: 14px;
  display: block;
  font-weight: 600;
}
.task-node .node-bottom {
  display: flex;
  justify-content: space-between;
  font-size: 9px;
  color: #b6c8d7;
  margin-top: 15px;
}
.react-flow__handle {
  width: 6px;
  height: 6px;
  background: #8ca9bb;
  border-color: #8ca9bb;
}
.graph-legend {
  display: flex;
  align-items: center;
  gap: 19px;
  padding: 13px 23px;
  font-size: 10px;
  color: #b0c1cf;
  flex-wrap: wrap;
}
.graph-legend span {
  display: flex;
  align-items: center;
  gap: 6px;
}
.graph-legend span:last-child {
  margin-left: auto;
  color: #809bb1;
}
.legend-dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 2px;
}
.completed {
  background: #73caaa;
}
.running {
  background: #d9b767;
}
.waiting {
  background: #6f8ca5;
}
.detail-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
  gap: 22px;
}
.tag {
  display: inline-flex;
  color: #c8d5e2;
  font-size: 10px;
  border: 1px solid #3a5167;
  border-radius: 4px;
  background: #213448;
  padding: 5px 8px;
}
.detail-fields {
  padding: 0 24px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 20px 14px;
}
.detail-fields span {
  display: block;
  font-size: 11px;
  color: #9db2c4;
  margin-bottom: 7px;
}
.detail-fields strong {
  font-size: 13px;
  font-weight: 500;
}
.detail-fields + .hint {
  margin: 20px 24px;
  font-size: 10px;
  overflow-wrap: anywhere;
}
.table-wrap {
  overflow: auto;
  border-top: 1px solid var(--line);
}
table {
  border-collapse: collapse;
  width: 100%;
  font-size: 11px;
  text-align: left;
  white-space: nowrap;
}
th {
  background: #162535;
  color: #a5bbcf;
  font-weight: 500;
  padding: 13px 24px;
  font-size: 10px;
}
td {
  padding: 14px 24px;
  border-top: 1px solid #253749;
  color: #d4e0ea;
}
td small {
  display: block;
  font-size: 9px;
  margin-top: 5px;
}
tbody tr:hover {
  background: #172a3a;
}
.text-good {
  color: #8fdec2;
}
.timeline {
  padding: 0 24px 15px;
  max-height: 385px;
  overflow: auto;
}
.timeline-item {
  display: flex;
  gap: 14px;
  padding: 0 0 20px;
  position: relative;
}
.timeline-item:not(:last-child):before {
  content: "";
  position: absolute;
  top: 14px;
  left: 3px;
  bottom: 0;
  width: 1px;
  background: #3e586b;
}
.timeline-dot {
  position: relative;
  background: #79c7b5;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-top: 6px;
  flex-shrink: 0;
}
.timeline-item > div {
  flex: 1;
  position: relative;
  padding-right: 52px;
}
.timeline-item strong {
  font-size: 11px;
  font-weight: 500;
}
.timeline-item p {
  font-size: 10px;
  margin-top: 5px;
  overflow-wrap: anywhere;
}
.timeline-item time {
  position: absolute;
  right: 0;
  top: 4px;
  font-size: 9px;
  color: #819bad;
  font-variant-numeric: tabular-nums;
}
.agent-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  padding: 0 24px 24px;
}
.agent-card {
  border: 1px solid #384c5e;
  background: #172738;
  padding: 18px 15px;
  border-radius: 7px;
  min-width: 0;
}
.agent-title {
  display: flex;
  align-items: center;
  gap: 9px;
}
.agent-title strong {
  font-size: 12px;
}
.agent-title .badge {
  margin-left: auto;
  font-size: 9px;
  padding: 4px 6px;
}
.agent-avatar {
  width: 33px;
  height: 33px;
  border-radius: 7px;
  background: #2d4455;
  display: grid;
  place-items: center;
  color: #a4d2dd;
}
.agent-card > p {
  font-size: 10px;
  color: #bbcddc;
  margin: 14px 0 19px;
  overflow-wrap: anywhere;
}
.agent-card > .row {
  display: flex;
  justify-content: space-between;
  margin: 9px 0;
  gap: 10px;
  color: #a4b8c9;
  font-size: 10px;
}
.agent-card code {
  font-size: 10px;
  color: #c5d4e1;
}
.agent-card > small {
  display: block;
  border-top: 1px solid #34495b;
  padding-top: 13px;
  margin-top: 17px;
  font-size: 9px;
  color: #9ab1c4;
  overflow-wrap: anywhere;
}
.ledger-balances {
  display: flex;
  flex-wrap: wrap;
  gap: 30px;
  padding: 0 24px 23px;
  font-size: 11px;
  color: #9fb5c8;
}
.ledger-balances strong {
  margin-left: 8px;
  font-size: 14px;
  color: #dfe9f2;
  font-weight: 500;
}
.panel > .hint {
  margin: 17px 24px;
}
.hash-button {
  font-family: Consolas, monospace;
  font-size: 10px;
  border: 0;
  background: transparent;
  min-height: 30px;
  padding: 4px 0;
  color: #9bd5eb;
}
.artifact-group {
  border-top: 1px solid var(--line);
  padding: 20px 24px;
}
.artifact-group > div:first-child {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.artifact-group > div > code {
  color: #aac0d0;
  font-size: 10px;
}
.artifact {
  padding: 13px 0;
  display: flex;
  align-items: center;
  gap: 14px;
}
.artifact > svg {
  color: #91b7cd;
}
.artifact > div {
  flex: 1;
  min-width: 0;
}
.artifact strong {
  font-size: 12px;
  font-weight: 500;
  overflow-wrap: anywhere;
}
.artifact small {
  display: block;
  font-size: 10px;
  margin-top: 5px;
}
.artifact button {
  font-size: 11px;
}
.trust-note {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 2px 3px 18px;
  color: #97b2c5;
}
.trust-note p {
  font-size: 11px;
}
.empty {
  padding: 60px 24px;
  text-align: center;
}
.empty > svg {
  color: #cbb16e;
  margin: 0 auto 20px;
}
.empty h2 {
  font-size: 20px;
}
.empty p {
  font-size: 12px;
  margin: 12px 0 24px;
}
.empty button {
  font-size: 12px;
}
.error {
  display: flex;
  align-items: center;
  gap: 15px;
  justify-content: space-between;
  padding: 14px 17px;
  margin: 16px 0;
  background: #3a2727;
  border: 1px solid #98615a;
  color: #ffe1d9;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}
.error button {
  white-space: nowrap;
  background: #543531;
}
.error > span {
  min-width: 0;
}
footer {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  color: #8099ae;
  font-size: 10px;
  border-top: 1px solid var(--line);
  padding: 21px 0 25px;
  margin-top: 10px;
}
dialog {
  color: #edf3f8;
  background: #132130;
  border: 1px solid #4d6478;
  border-radius: 10px;
  max-width: 570px;
  width: calc(100% - 40px);
  max-height: calc(100vh - 48px);
  overflow: auto;
  padding: 27px;
  margin: auto;
  box-shadow: 0 30px 100px #0008;
}
dialog::backdrop {
  background: #060d18c9;
  backdrop-filter: blur(3px);
}
.modal-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 15px;
}
.modal-heading h2 {
  font-size: 23px;
}
dialog > p {
  font-size: 12px;
}
.input-list {
  display: flex;
  gap: 13px;
  align-items: center;
  border: 1px solid #3c5266;
  background: #1a2b3c;
  border-radius: 7px;
  padding: 15px;
  margin: 19px 0;
  color: #99c8d5;
}
.input-list strong {
  font-size: 12px;
  color: #d9e9f4;
}
.input-list small {
  display: block;
  font-size: 10px;
  margin-top: 5px;
}
.plan-steps {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 10px;
  margin: 18px 0 25px;
}
.plan-steps > div {
  border: 1px solid #34495e;
  border-radius: 6px;
  padding: 13px;
  display: grid;
  grid-template-columns: 25px 1fr;
  gap: 7px 9px;
}
.plan-steps > div > span {
  font:
    11px Consolas,
    monospace;
  color: var(--accent);
}
.plan-steps strong {
  font-size: 11px;
  font-weight: 500;
}
.plan-steps small {
  grid-column: 2;
  font-size: 10px;
}
.form-row {
  display: grid;
  grid-template-columns: 1.1fr 1fr;
  gap: 18px;
}
.form-row label {
  font-size: 11px;
}
.modal-actions {
  display: flex;
  gap: 12px;
  justify-content: flex-end;
  margin-top: 22px;
  padding-top: 19px;
  border-top: 1px solid #34495a;
}
.modal-actions button {
  font-size: 12px;
}
.stop-dialog h2 {
  font-size: 21px;
  margin-bottom: 14px;
}
.login {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    radial-gradient(ellipse at 25% 30%, #223645 0, transparent 60%), #0b1420;
}
.login-card {
  width: 100%;
  max-width: 465px;
  background: #142231;
  border: 1px solid #435565;
  border-radius: 12px;
  padding: 38px;
  box-shadow: 0 24px 100px #0005;
}
.login .brand {
  margin-bottom: 43px;
}
.login .eyebrow {
  display: block;
}
.login h1 {
  font-size: 26px;
  margin-bottom: 14px;
}
.login p {
  font-size: 12px;
}
.login form {
  margin-top: 29px;
}
.login .hint {
  font-size: 10px;
  margin: 10px 0 24px;
}
.login .primary {
  width: 100%;
}
.login .error {
  margin-bottom: 0;
}
.spin {
  animation: spin 0.9s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
@media (min-width: 1600px) {
  main {
    padding: 42px 50px 0;
  }
  .dag {
    height: 375px;
  }
  .metric {
    padding: 24px;
  }
  .agent-card {
    padding: 22px;
  }
}
@media (max-width: 1199px) {
  .shell {
    grid-template-columns: 194px minmax(0, 1fr);
  }
  .sidebar {
    padding: 24px 13px;
  }
  .brand {
    font-size: 16px;
  }
  .brand > svg {
    width: 22px;
  }
  .topbar {
    padding: 0 24px;
  }
  main {
    padding: 28px 24px 0;
  }
  .agent-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .job-actions > .mono {
    display: none;
  }
  .detail-grid {
    gap: 16px;
  }
  .panel-heading {
    padding: 20px;
  }
  .metric {
    padding: 17px 14px;
  }
  .metric > strong {
    font-size: 27px;
  }
  .metric small {
    display: block;
    margin: 7px 0 0;
  }
  .artifact > code {
    display: none;
  }
}
@media (max-width: 899px) {
  .shell {
    display: block;
  }
  .sidebar {
    position: static;
    height: auto;
    padding: 15px 22px;
    border-right: 0;
    border-bottom: 1px solid var(--line);
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 15px;
  }
  .sidebar .workspace,
  .nav-label,
  .sidebar-foot {
    display: none;
  }
  .sidebar nav {
    flex-direction: row;
    gap: 4px;
  }
  .sidebar nav button {
    padding: 10px;
    min-height: 42px;
    font-size: 11px;
    gap: 6px;
  }
  .sidebar nav button > svg:last-child:not(:first-child) {
    display: none;
  }
  .topbar {
    height: 57px;
  }
  .topbar > span {
    font-size: 11px;
  }
  .topbar > div {
    gap: 6px;
  }
  .topbar .identity {
    font-size: 10px;
  }
  .detail-grid {
    grid-template-columns: 1fr 1fr;
  }
  .panel-heading {
    align-items: flex-start;
  }
  .job-actions .danger-outline {
    white-space: nowrap;
  }
  .stats-grid {
    gap: 10px;
  }
  .graph-legend span:last-child {
    display: none;
  }
  .timeline {
    padding: 0 20px 15px;
  }
  .timeline-item > div {
    padding-right: 0;
  }
  .timeline-item time {
    position: static;
    display: block;
    margin-top: 4px;
  }
  .detail-fields {
    padding: 0 20px;
    gap: 20px 9px;
  }
  .detail-fields strong {
    font-size: 12px;
  }
  .detail-fields + .hint {
    margin: 20px;
  }
  .panel-heading .primary {
    font-size: 10px;
  }
  .ledger-balances {
    gap: 16px;
  }
}
@media (max-width: 639px) {
  .sidebar {
    display: block;
    padding: 16px 15px 7px;
  }
  .sidebar .brand {
    margin: 0 5px 15px;
    font-size: 18px;
  }
  .sidebar nav {
    justify-content: space-between;
  }
  .sidebar nav button {
    flex-direction: column;
    flex: 1;
    gap: 7px;
    font-size: 10px;
    padding: 9px 4px;
    border-radius: 5px;
  }
  .sidebar nav button.active {
    box-shadow: inset 0 -2px #d9b85f;
  }
  .topbar {
    padding: 0 20px;
  }
  .topbar > span {
    display: none;
  }
  .topbar > div {
    width: 100%;
    justify-content: flex-end;
  }
  .local-pill {
    margin-right: auto;
    font-size: 10px;
  }
  .identity {
    font-size: 10px;
  }
  .identity > span {
    display: none;
  }
  main {
    padding: 25px 16px 0;
  }
  .page-heading {
    align-items: flex-start;
    gap: 12px;
    margin-bottom: 20px;
  }
  .page-heading h1 {
    font-size: 26px;
  }
  .page-heading .eyebrow {
    font-size: 8px;
  }
  .page-heading p {
    font-size: 11px;
  }
  .page-heading .primary {
    font-size: 10px;
    padding: 10px 11px;
    white-space: nowrap;
  }
  .page-heading .primary svg {
    display: none;
  }
  .mode-note {
    font-size: 10px;
    align-items: flex-start;
    padding: 10px;
    margin-bottom: 19px;
  }
  .mode-note svg {
    margin-top: 1px;
  }
  .job-picker {
    gap: 10px;
  }
  .job-picker label {
    font-size: 11px;
  }
  .job-picker select {
    flex: 1;
    width: auto;
    min-width: 0;
    font-size: 10px;
  }
  .job-picker .badge {
    font-size: 9px;
  }
  .stream {
    flex-basis: 100%;
    justify-content: flex-end;
    font-size: 9px;
  }
  .stats-grid {
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin-bottom: 19px;
  }
  .metric {
    padding: 15px;
  }
  .metric > div {
    font-size: 10px;
  }
  .metric > strong {
    font-size: 27px;
    margin-top: 12px;
  }
  .metric small {
    display: inline-block;
    margin: 0 0 0 7px;
    font-size: 9px;
  }
  .panel {
    margin-bottom: 18px;
  }
  .panel-heading {
    padding: 19px 16px;
    flex-wrap: wrap;
    gap: 13px;
  }
  .panel-heading h2 {
    font-size: 15px;
  }
  .panel-heading p {
    font-size: 10px;
  }
  .panel-heading .primary {
    min-height: 44px;
  }
  .job-actions {
    width: 100%;
    justify-content: flex-end;
  }
  .job-actions .mono {
    display: inline;
    margin-right: auto;
  }
  .dag {
    height: 300px;
  }
  .graph-legend {
    padding: 12px 15px;
    gap: 13px;
    font-size: 9px;
  }
  .detail-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }
  .detail-fields {
    padding: 0 16px;
  }
  .detail-fields + .hint {
    margin: 18px 16px;
  }
  .tag {
    font-size: 9px;
  }
  .timeline {
    max-height: 300px;
    padding: 0 17px 16px;
  }
  .timeline-item > div {
    padding-right: 58px;
  }
  .timeline-item time {
    position: absolute;
    margin: 0;
    top: 4px;
  }
  .agent-grid {
    grid-template-columns: 1fr;
    padding: 0 16px 18px;
    gap: 12px;
  }
  .agent-card {
    padding: 18px;
  }
  .agent-title strong {
    font-size: 13px;
  }
  .agent-card > p {
    font-size: 11px;
  }
  .agent-card > .row {
    font-size: 11px;
  }
  .ledger-balances {
    padding: 0 16px 19px;
    font-size: 10px;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 17px;
  }
  .ledger-balances strong {
    font-size: 13px;
  }
  th,
  td {
    padding: 13px 16px;
  }
  .artifact-group {
    padding: 19px 16px;
  }
  .artifact {
    gap: 10px;
  }
  .artifact > svg {
    display: none;
  }
  .artifact strong {
    font-size: 11px;
  }
  .artifact button {
    font-size: 10px;
    min-height: 44px;
    padding: 9px;
  }
  .artifact-group > div > code {
    font-size: 9px;
  }
  .trust-note p {
    font-size: 10px;
  }
  .trust-note > svg {
    width: 17px;
  }
  .empty {
    padding: 42px 16px;
  }
  .empty h2 {
    font-size: 18px;
  }
  footer {
    font-size: 9px;
    flex-wrap: wrap;
    padding: 20px 0;
  }
  dialog {
    width: calc(100% - 24px);
    padding: 22px 18px;
  }
  .modal-heading h2 {
    font-size: 21px;
  }
  .form-row {
    gap: 12px;
  }
  .form-row label {
    font-size: 10px;
  }
  .plan-steps {
    gap: 8px;
  }
  .plan-steps > div {
    padding: 10px;
    grid-template-columns: 18px 1fr;
    gap: 6px;
  }
  .modal-actions button {
    font-size: 11px;
    padding: 10px;
  }
  .login {
    padding: 17px;
  }
  .login-card {
    padding: 28px 24px;
  }
  .error {
    font-size: 11px;
    align-items: flex-start;
    flex-direction: column;
  }
}
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation: none !important;
    transition: none !important;
    scroll-behavior: auto !important;
  }
}

@end-agentflow-styles */
