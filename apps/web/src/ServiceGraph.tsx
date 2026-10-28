import { useCallback, useEffect, useMemo, useState } from "react";
import { Background, Controls, MarkerType, ReactFlow } from "@xyflow/react";
import { price } from "./amount";
import {
  Activity,
  Award,
  CheckCircle2,
  Clock,
  Coins,
  Database,
  FileUp,
  Download,
  Filter,
  GitBranch,
  Layers,
  Network,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import Modal from "./Modal";

type Row = Record<string, any>;
type Api = (path: string, body?: unknown) => Promise<any>;
const prefix = "/api/workspace";

export const evidenceLabels: Record<string, string> = {
  demo: "演示数据",
  operator: "人工录入，未独立核验",
  chain: "链上已核验记录",
  unknown: "无履约数据",
  registry: "注册资料",
};

const scenarioLabels: Record<string, string> = {
  cross_org_analysis: "销售分析",
  travel_planning: "旅行规划",
  unclassified: "新业务 / 待接入",
};

const rate = (h: Row) =>
  h?.window
    ? `${((100 * h.settled) / h.window).toFixed(2)}%（${h.settled}/${h.window}）`
    : "未知";

const rateValue = (h?: Row): number | null =>
  h?.window ? (100 * h.settled) / h.window : null;

/**
 * Compute Local Skyline (Pareto-optimal) providers and specialized badges
 * per service node based on (Price, Latency, Success Rate) & DASC Risk-Adjusted Cost.
 */
function analyzeProviders(providers: Row[]) {
  const parsed = providers.map((p) => {
    const numPrice =
      p.price != null && p.price !== "" ? Number(price(p.price)) : null;
    const sec = typeof p.seconds === "number" && p.seconds > 0 ? p.seconds : null;
    const sr = rateValue(p.history);
    const prob = sr != null ? sr / 100 : 0.5;
    const riskAdjusted =
      numPrice != null && !Number.isNaN(numPrice)
        ? numPrice / Math.max(prob, 0.01)
        : null;
    return { raw: p, id: p.id, numPrice, sec, sr, riskAdjusted };
  });

  const valid = parsed.filter(
    (x) => x.numPrice != null && !Number.isNaN(x.numPrice) && x.sec != null,
  );

  const skylineIds = new Set<string>();
  for (const a of valid) {
    const aRate = a.sr ?? 0;
    const dominated = valid.some((b) => {
      if (b.id === a.id) return false;
      const bRate = b.sr ?? 0;
      const noWorse =
        b.numPrice! <= a.numPrice! && b.sec! <= a.sec! && bRate >= aRate;
      const strictlyBetter =
        b.numPrice! < a.numPrice! || b.sec! < a.sec! || bRate > aRate;
      return noWorse && strictlyBetter;
    });
    if (!dominated) skylineIds.add(a.id);
  }

  const minPrice = valid.length
    ? Math.min(...valid.map((x) => x.numPrice!))
    : null;
  const maxPrice = valid.length
    ? Math.max(...valid.map((x) => x.numPrice!))
    : null;
  const minSec = valid.length ? Math.min(...valid.map((x) => x.sec!)) : null;
  const maxSec = valid.length ? Math.max(...valid.map((x) => x.sec!)) : null;
  const rates = parsed.map((x) => x.sr).filter((x): x is number => x != null);
  const maxRate = rates.length ? Math.max(...rates) : null;
  const avgRate = rates.length
    ? rates.reduce((acc, v) => acc + v, 0) / rates.length
    : null;
  const bestRisk = valid.length
    ? Math.min(
        ...valid
          .map((x) => x.riskAdjusted)
          .filter((x): x is number => x != null),
      )
    : null;

  const byId: Record<
    string,
    {
      isSkyline: boolean;
      isCheapest: boolean;
      isFastest: boolean;
      isMostReliable: boolean;
      isBestRiskAdjusted: boolean;
      numPrice: number | null;
      sec: number | null;
      sr: number | null;
      riskAdjusted: number | null;
    }
  > = {};

  for (const item of parsed) {
    byId[item.id] = {
      isSkyline: skylineIds.has(item.id),
      isCheapest: minPrice != null && item.numPrice === minPrice,
      isFastest: minSec != null && item.sec === minSec,
      isMostReliable: maxRate != null && item.sr === maxRate,
      isBestRiskAdjusted:
        bestRisk != null &&
        item.riskAdjusted != null &&
        Math.abs(item.riskAdjusted - bestRisk) < 1e-6,
      numPrice: item.numPrice,
      sec: item.sec,
      sr: item.sr,
      riskAdjusted: item.riskAdjusted,
    };
  }

  return {
    skylineIds,
    minPrice,
    maxPrice,
    minSec,
    maxSec,
    maxRate,
    avgRate,
    byId,
    valid,
  };
}

function ServiceMetrics({
  cost,
  seconds,
  history,
  riskAdjusted,
}: {
  cost: string;
  seconds?: number;
  history?: Row;
  riskAdjusted?: number | null;
}) {
  const pct = rateValue(history);
  return (
    <dl className="service-metrics">
      <div>
        <dt>参考费用</dt>
        <dd>{cost}</dd>
      </div>
      <div>
        <dt>预计耗时</dt>
        <dd>{seconds == null ? "待提供" : `${seconds} 秒`}</dd>
      </div>
      <div>
        <dt>近期成功率</dt>
        <dd>
          <span>{history ? rate(history) : "暂无记录"}</span>
          {pct != null && (
            <span className="market-rate-bar" aria-hidden="true">
              <span
                className="market-rate-fill"
                style={{ width: `${Math.max(4, Math.min(100, pct))}%` }}
              />
            </span>
          )}
        </dd>
      </div>
      {riskAdjusted != null && (
        <div>
          <dt>风险折算成本</dt>
          <dd>{riskAdjusted.toFixed(2)} USDC（按成功率折算）</dd>
        </div>
      )}
    </dl>
  );
}

function HistorySources({ providers }: { providers: Row[] }) {
  const labels: Record<string, string> = {
    demo: "平台内置演示履约记录",
    operator: "人工录入记录（未独立核验）",
    chain: "链上已核验记录",
    registry: "服务注册资料",
  };
  const sources = [
    ...new Set(providers.map((p) => labels[p.history?.source]).filter(Boolean)),
  ];
  return sources.length ? (
    <p className="service-source-note">
      成功率统计来源：{sources.join("、")}。
    </p>
  ) : null;
}

function ParetoScatterChart({ providers }: { providers: Row[] }) {
  const stats = useMemo(() => analyzeProviders(providers), [providers]);
  if (stats.valid.length < 2) return null;

  const minP = stats.minPrice ?? 0;
  const maxP = stats.maxPrice ?? 1;
  const minS = stats.minSec ?? 0;
  const maxS = stats.maxSec ?? 1;
  const padX = 44;
  const padY = 26;
  const width = 520;
  const height = 165;
  const plotW = width - padX * 2;
  const plotH = height - padY * 2;

  const toX = (sec: number) =>
    maxS === minS ? padX + plotW / 2 : padX + ((sec - minS) / (maxS - minS)) * plotW;
  const toY = (p: number) =>
    maxP === minP
      ? padY + plotH / 2
      : padY + plotH - ((p - minP) / (maxP - minP)) * plotH;

  const skylinePoints = stats.valid
    .filter((v) => stats.skylineIds.has(v.id))
    .sort((a, b) => a.sec! - b.sec! || a.numPrice! - b.numPrice!);

  return (
    <div className="market-pareto-chart" aria-label="候选商家帕累托前沿分布">
      <div className="market-pareto-header">
        <strong>QoS 帕累托天际线（Skyline）分布：费用 vs. 耗时</strong>
        <span className="market-pareto-legend">
          <span className="dot skyline" /> 帕累托占优 (Skyline)
          <span className="dot normal" /> 被支配候选
        </span>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="market-pareto-svg"
        role="img"
        aria-label="商家报价与耗时散点图"
      >
        <line
          x1={padX}
          y1={height - padY}
          x2={width - padX}
          y2={height - padY}
          stroke="#354b60"
          strokeWidth="1"
        />
        <line
          x1={padX}
          y1={padY}
          x2={padX}
          y2={height - padY}
          stroke="#354b60"
          strokeWidth="1"
        />
        <text x={width / 2} y={height - 6} fill="#8ea4b8" fontSize="10" textAnchor="middle">
          预计耗时 ({minS}s – {maxS}s，越左越快)
        </text>
        <text
          x={12}
          y={height / 2}
          fill="#8ea4b8"
          fontSize="10"
          textAnchor="middle"
          transform={`rotate(-90 12 ${height / 2})`}
        >
          报价 (USDC)
        </text>
        {skylinePoints.length > 1 && (
          <polyline
            fill="none"
            stroke="#edc56c"
            strokeWidth="1.75"
            strokeDasharray="4 3"
            points={skylinePoints
              .map((pt) => `${toX(pt.sec!)},${toY(pt.numPrice!)}`)
              .join(" ")}
          />
        )}
        {stats.valid.map((item) => {
          const cx = toX(item.sec!);
          const cy = toY(item.numPrice!);
          const isSky = stats.skylineIds.has(item.id);
          return (
            <g key={item.id}>
              <circle
                cx={cx}
                cy={cy}
                r={isSky ? 6.5 : 5}
                fill={isSky ? "#edc56c" : "#5b7a94"}
                stroke={item.raw.online ? "#81dbc0" : "#152638"}
                strokeWidth="2"
              />
              <text
                x={cx}
                y={cy - 9}
                fill={isSky ? "#f6dfb0" : "#b0c4d6"}
                fontSize="10"
                textAnchor="middle"
              >
                {item.raw.name} ({item.numPrice}U/{item.sec}s)
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

const cleanMerchant = (m: Row) => ({
  id: m.id,
  name: m.name,
  offers: m.offers.map((o: Row) => ({
    serviceId: o.serviceId,
    price: o.price,
    seconds: o.seconds,
    history: o.history || [],
    ...(o.recent ? { recent: o.recent } : {}),
  })),
});

export default function ServiceGraph({
  api,
  agents = [],
  onChange,
}: {
  api: Api;
  agents?: Row[];
  onChange: () => void;
}) {
  const [graph, setGraph] = useState<Row | null>(null),
    [corpus, setCorpus] = useState<Row | null>(null);
  const [query, setQuery] = useState(""),
    [scenarioFilter, setScenarioFilter] = useState("all"),
    [selected, setSelected] = useState("");
  const [editor, setEditor] = useState<Row | null>(null),
    [kind, setKind] = useState("merchant");
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<{ kind: string; id: string } | null>(
    null,
  );
  const [pendingDelete, setPendingDelete] = useState<Row | null>(null),
    [restore, setRestore] = useState(false);

  const refresh = useCallback(async () => {
    const [g, c] = await Promise.all([
      api(prefix + "/service-graph"),
      api(prefix + "/service-corpus"),
    ]);
    setGraph(g);
    setCorpus(c);
  }, [api]);

  useEffect(() => {
    void refresh().catch((e) => setError(String(e)));
  }, [refresh]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      // Keep the user's edits while reloading the revision for a retry.
      await refresh().catch(() => {});
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!editor || !corpus) return;
    const payload =
      kind === "merchant"
        ? { merchants: [cleanMerchant(editor)] }
        : {
            services: [
              {
                ...editor,
                keywords: editor.keywords.filter(Boolean),
                requires: editor.requires.filter(Boolean),
              },
            ],
          };
    await api(prefix + "/service-corpus", {
      ...payload,
      revision: corpus.revision,
      restoreDeleted: restore,
    });
    await refresh();
    setEditor(null);
    onChange();
    setNotice(
      "资料已保存，业务图与商家关联已更新。未注册在线接口的商家只进入资料库，不能直接接单。",
    );
  };

  const nodes: Row[] = graph?.nodes || [];
  const shown = nodes.filter((n: Row) => {
    const matchScenario =
      scenarioFilter === "all" || n.scenario === scenarioFilter;
    const matchQuery =
      !query.trim() ||
      `${n.id} ${n.name} ${n.description} ${n.providers.map((p: Row) => p.name).join(" ")}`
        .toLowerCase()
        .includes(query.trim().toLowerCase());
    return matchScenario && matchQuery;
  });

  // Topological level calculation
  const levels: Record<string, number> = {};
  for (let pass = 0; pass < nodes.length; pass++) {
    for (const n of nodes) {
      const before =
        graph?.edges
          .filter((e: Row) => e.target === n.id)
          .map((e: Row) => e.source) || [];
      if (!(n.id in levels) && before.every((id: string) => id in levels)) {
        levels[n.id] =
          Math.max(-1, ...before.map((id: string) => levels[id])) + 1;
      }
    }
  }

  // Domain-clustered swimlane layout so cross-domain workflows never tangle
  const visible = new Set(shown.map((n: Row) => n.id));
  const { flowNodes, flowEdges } = useMemo(() => {
    const scenarioOrder = [
      "cross_org_analysis",
      "travel_planning",
      "unclassified",
    ];
    const groups = new Map<string, Row[]>();
    for (const s of scenarioOrder) groups.set(s, []);
    for (const n of shown) {
      const key = groups.has(n.scenario) ? n.scenario : "unclassified";
      groups.get(key)!.push(n);
    }

    const nodeW = 264;
    const gapX = 36;
    const gapY = 178;
    const swimlaneGap = 86;
    let cursorX = 0;
    const positionedNodes: any[] = [];

    for (const [, groupNodes] of groups.entries()) {
      if (!groupNodes.length) continue;
      const byLevel = new Map<number, Row[]>();
      let maxRowCount = 1;
      for (const n of groupNodes) {
        const lv = levels[n.id] || 0;
        const list = byLevel.get(lv) || [];
        list.push(n);
        byLevel.set(lv, list);
        if (list.length > maxRowCount) maxRowCount = list.length;
      }
      const clusterWidth =
        maxRowCount * nodeW + Math.max(0, maxRowCount - 1) * gapX;

      for (const [lv, rowNodes] of byLevel.entries()) {
        const rowWidth =
          rowNodes.length * nodeW + Math.max(0, rowNodes.length - 1) * gapX;
        const offsetX = cursorX + (clusterWidth - rowWidth) / 2;
        rowNodes.forEach((n, idx) => {
          const stats = analyzeProviders(n.providers || []);
          const onlineCount = (n.providers || []).filter(
            (p: Row) => p.online,
          ).length;
          const isSelected = selected === n.id;
          const scenarioClass =
            n.scenario === "cross_org_analysis"
              ? "scenario-sales"
              : n.scenario === "travel_planning"
                ? "scenario-travel"
                : "scenario-other";

          positionedNodes.push({
            id: n.id,
            position: { x: offsetX + idx * (nodeW + gapX), y: lv * gapY },
            data: {
              label: (
                <div className={`market-flow-node ${scenarioClass}`}>
                  <div className="market-flow-node-top">
                    <span className="market-domain-tag">
                      {scenarioLabels[n.scenario] || n.scenario}
                    </span>
                    <span
                      className={`market-exec-pill ${n.executable ? "is-ready" : "is-pending"}`}
                    >
                      {n.executable
                        ? "可执行"
                        : n.adapterReady
                          ? "暂无在线商家"
                          : "执行接口待接入"}
                    </span>
                  </div>
                  <strong className="market-flow-node-title">
                    <b>{n.name}</b>
                  </strong>
                  <div className="market-flow-contract">
                    <span>{n.inputType || "原始输入"}</span>
                    <span aria-hidden="true">→</span>
                    <span>{n.outputType || "交付成果"}</span>
                  </div>
                  <div className="market-flow-qos">
                    <span>
                      {stats.minPrice != null
                        ? `≥ ${stats.minPrice.toFixed(2)} USDC`
                        : "待报价"}
                    </span>
                    <span>
                      {stats.minSec != null ? `最快 ${stats.minSec}s` : "耗时待定"}
                    </span>
                    <span>
                      {stats.maxRate != null
                        ? `最高 ${stats.maxRate.toFixed(0)}%`
                        : `${n.providers.length} 商家`}
                    </span>
                  </div>
                  <div className="market-flow-foot">
                    <span>
                      {n.providers.length} 个商家（{onlineCount} 在线）
                    </span>
                    {stats.skylineIds.size > 0 && (
                      <span className="skyline-mini">
                        ★ {stats.skylineIds.size} 帕累托最优
                      </span>
                    )}
                  </div>
                </div>
              ),
            },
            style: {
              width: nodeW,
              padding: 0,
              background: isSelected ? "#1d3b5c" : "#132334",
              color: "#eaf3ff",
              border: isSelected
                ? "2px solid #edc56c"
                : "1px solid #3c5872",
              borderRadius: "10px",
              boxShadow: isSelected
                ? "0 0 0 3px rgba(237, 197, 108, 0.22)"
                : "0 6px 18px rgba(0, 0, 0, 0.28)",
            },
          });
        });
      }
      cursorX += clusterWidth + swimlaneGap;
    }

    const builtEdges = (graph?.edges || [])
      .filter((e: Row) => visible.has(e.source) && visible.has(e.target))
      .map((e: Row) => {
        const activeEdge =
          selected && (e.source === selected || e.target === selected);
        return {
          id: `${e.source}-${e.target}`,
          source: e.source,
          target: e.target,
          label: e.type || undefined,
          animated: Boolean(activeEdge),
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: activeEdge ? "#edc56c" : "#72b5ef",
          },
          labelStyle: {
            fill: "#d7e8f7",
            fontSize: 10,
            fontWeight: 600,
          },
          labelBgStyle: {
            fill: "#0f1e2e",
            stroke: "#36526d",
            strokeWidth: 1,
            rx: 4,
            ry: 4,
          },
          labelBgPadding: [6, 3] as [number, number],
          style: {
            stroke: activeEdge ? "#edc56c" : "#72b5ef",
            strokeWidth: activeEdge ? 2.5 : 2,
          },
        };
      });

    return { flowNodes: positionedNodes, flowEdges: builtEdges };
  }, [shown, levels, graph?.edges, selected, visible]);

  // Global Market KPI summary
  const marketStats = useMemo(() => {
    const allPrices: number[] = [];
    const allSecs: number[] = [];
    const allRates: number[] = [];
    let skylineTotal = 0;
    let executableNodes = 0;

    for (const n of nodes) {
      if (n.executable) executableNodes++;
      const st = analyzeProviders(n.providers || []);
      skylineTotal += st.skylineIds.size;
      for (const v of st.valid) {
        if (v.numPrice != null) allPrices.push(v.numPrice);
        if (v.sec != null) allSecs.push(v.sec);
      }
      for (const p of n.providers || []) {
        const r = rateValue(p.history);
        if (r != null) allRates.push(r);
      }
    }

    return {
      executableNodes,
      edgeCount: (graph?.edges || []).length,
      minPrice: allPrices.length ? Math.min(...allPrices) : null,
      maxPrice: allPrices.length ? Math.max(...allPrices) : null,
      minSec: allSecs.length ? Math.min(...allSecs) : null,
      maxSec: allSecs.length ? Math.max(...allSecs) : null,
      avgRate: allRates.length
        ? allRates.reduce((a, b) => a + b, 0) / allRates.length
        : null,
      skylineTotal,
    };
  }, [nodes, graph?.edges]);

  const node =
    detail?.kind === "service"
      ? nodes.find((n: Row) => n.id === detail.id) ||
        corpus?.services.find((n: Row) => n.id === detail.id)
      : null;
  const nodeProviderAnalysis = useMemo(
    () => analyzeProviders(node?.providers || []),
    [node],
  );
  const merchant =
    detail?.kind === "merchant"
      ? corpus?.merchants.find((m: Row) => m.id === detail.id)
      : null;
  const instance =
    detail?.kind === "merchant"
      ? agents.find((a) => a.agentId === detail.id)
      : null;
  const serviceName = (id: string) =>
    corpus?.services.find((n: Row) => n.id === id)?.name || id;
  const matches = (value: string) =>
    value.toLowerCase().includes(query.trim().toLowerCase());
  const activeAgents = agents.filter(
    (a) =>
      !corpus?.removedMerchants.includes(a.agentId) &&
      matches(
        `${a.agentId} ${corpus?.merchants.find((m: Row) => m.id === a.agentId)?.name || ""} ${a.skills.map(serviceName).join(" ")}`,
      ),
  );
  const offlineMerchants = (corpus?.merchants || []).filter(
    (m: Row) =>
      !corpus?.removedMerchants.includes(m.id) &&
      !agents.some((a) => a.agentId === m.id) &&
      matches(
        `${m.id} ${m.name} ${m.offers.map((o: Row) => serviceName(o.serviceId)).join(" ")}`,
      ),
  );
  const openDetail = (kind: string, id: string) => {
    setDetail({ kind, id });
    setEditor(null);
    setPendingDelete(null);
    setError("");
    if (kind === "service") setSelected(id);
  };
  const closeDialog = () => {
    setDetail(null);
    setEditor(null);
    setPendingDelete(null);
    setError("");
  };
  const dialogOpen = !!(detail || editor || pendingDelete);
  const dialogTitle = pendingDelete
    ? `删除“${pendingDelete.name}”？`
    : editor
      ? kind === "merchant"
        ? "编辑商家及其服务"
        : "编辑服务定义"
      : node?.name || merchant?.name || instance?.agentId || "服务详情";
  const editService = (n: Row) => {
    setKind("service");
    setEditor(
      Object.fromEntries(
        [
          "id",
          "name",
          "description",
          "keywords",
          "scenario",
          "inputType",
          "outputType",
          "requires",
        ].map((k) => [k, n[k]]),
      ),
    );
    setRestore(false);
  };
  const editMerchant = (provider: Row) => {
    const saved = corpus?.merchants.find((m: Row) => m.id === provider.id);
    setKind("merchant");
    setEditor(
      saved
        ? cleanMerchant(saved)
        : { id: provider.id, name: provider.name, offers: [] },
    );
    setRestore(false);
  };

  return (
    <section className="market-page">
      <div className="market-hero">
        <div className="market-hero-text">
          <h2>服务业务图与商家</h2>
          <p>
            先有商家能提供的服务，再从图中拆解任务。点击服务节点，查看所有关联商家与帕累托（Skyline）最优候选。箭头表示服务所需的前置成果契约。
          </p>
          <p className="hint">
            初始化语料：16 个演示商家、8 类服务、32 项报价、1600
            次模拟履约记录。新增商家或服务可以追加；未配置执行和验收接口的新业务会入图，但不会被当成可执行能力。
          </p>
        </div>
        <div className="market-hero-actions">
          <button disabled={busy} onClick={() => void run(refresh)}>
            <RefreshCw size={15} />
            刷新
          </button>
          <button
            disabled={busy}
            onClick={() => {
              setKind("merchant");
              setEditor({ id: "", name: "", offers: [] });
              setRestore(false);
            }}
          >
            <Plus size={15} />
            新增商家
          </button>
          <button
            className="primary"
            disabled={busy}
            onClick={() => {
              setKind("service");
              setEditor({
                id: "",
                name: "",
                description: "",
                keywords: [],
                scenario: "unclassified",
                inputType: "",
                outputType: "",
                requires: [],
              });
              setRestore(false);
            }}
          >
            <Plus size={15} />
            新增服务定义
          </button>
        </div>
      </div>

      {error && !dialogOpen && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {busy && !dialogOpen && <p role="status">正在更新服务库…</p>}

      {graph && (
        <div className="market-kpi-grid" aria-label="市场全景指标">
          <div className="market-kpi-card">
            <div className="market-kpi-head">
              <span>服务能力拓扑</span>
              <GitBranch size={16} />
            </div>
            <strong>
              {graph.serviceCount} <small>类服务节点</small>
            </strong>
            <span className="market-kpi-sub">
              {marketStats.executableNodes} 项可执行 · {marketStats.edgeCount} 条前置契约连线
            </span>
          </div>
          <div className="market-kpi-card">
            <div className="market-kpi-head">
              <span>注册商家与算力</span>
              <Network size={16} />
            </div>
            <strong>
              {graph.merchantCount} <small>个服务商家</small>
            </strong>
            <span className="market-kpi-sub">
              {activeAgents.filter((a) => a.status === "ONLINE").length} 个在线实例 · 资料版本 {graph.revision}
            </span>
          </div>
          <div className="market-kpi-card">
            <div className="market-kpi-head">
              <span>单步报价与耗时基线</span>
              <Coins size={16} />
            </div>
            <strong>
              {marketStats.minPrice != null
                ? `${marketStats.minPrice.toFixed(2)}–${marketStats.maxPrice?.toFixed(2)}`
                : "—"}{" "}
              <small>USDC</small>
            </strong>
            <span className="market-kpi-sub">
              预计响应耗时 {marketStats.minSec ?? "—"}s – {marketStats.maxSec ?? "—"}s
            </span>
          </div>
          <div className="market-kpi-card">
            <div className="market-kpi-head">
              <span>帕累托优选与平均履约率</span>
              <ShieldCheck size={16} />
            </div>
            <strong>
              {marketStats.avgRate != null
                ? `${marketStats.avgRate.toFixed(1)}%`
                : "—"}{" "}
              <small>近期成功率</small>
            </strong>
            <span className="market-kpi-sub">
              已识别 {marketStats.skylineTotal} 个局部 Skyline 最优商家
            </span>
          </div>
        </div>
      )}

      <div className="workspace-panel market-control-bar">
        <div className="market-search-row">
          <label className="market-search-field">
            <span>
              <Search size={14} /> 搜索服务或商家
            </span>
            <input
              placeholder="输入服务名称、技能 ID、关键词或商家名称…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <div className="market-scenario-pills" role="group" aria-label="业务场景筛选">
            <span className="market-filter-label">
              <Filter size={13} /> 领域视图：
            </span>
            {[
              ["all", "全部领域"],
              ["cross_org_analysis", "销售分析"],
              ["travel_planning", "旅行规划"],
              ["unclassified", "待接入业务"],
            ].map(([id, label]) => (
              <button
                key={id}
                type="button"
                className={`market-pill ${scenarioFilter === id ? "active" : ""}`}
                onClick={() => setScenarioFilter(id)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <section className="content-section market-corpus-bar">
          <div className="market-corpus-info">
            <h3 className="section-title">
              <Database size={15} /> 批量导入 / 导出语料
            </h3>
            <p>
              JSON 包含 services 和 merchants
              数组。导出当前语料可作为格式示例；导入只新增或更新列出的对象，不会删除其他数据。
            </p>
          </div>
          <div className="market-corpus-controls">
            <label className="check-row">
              <input
                type="checkbox"
                checked={restore}
                onChange={(e) => setRestore(e.target.checked)}
              />
              允许恢复本次导入中明确列出的已删除项
            </label>
            <div className="market-corpus-buttons">
              <input
                className="market-file-input"
                aria-label="导入服务语料 JSON"
                type="file"
                accept=".json"
                disabled={busy || !corpus}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (!file) return;
                  void run(async () => {
                    if (file.size > 2_000_000)
                      throw new Error("文件不能超过 2 MB");
                    const data = JSON.parse(await file.text());
                    await api(prefix + "/service-corpus", {
                      services: data.services || [],
                      merchants: (data.merchants || []).map(cleanMerchant),
                      revision: corpus?.revision,
                      restoreDeleted: restore,
                    });
                    await refresh();
                    onChange();
                    setNotice("已导入，新增业务和商家已同步到图中。");
                  });
                }}
              />
              <button
                disabled={busy || !corpus}
                onClick={() => {
                  const data = {
                    services: corpus?.services.filter(
                      (s: Row) => !corpus.removedServices.includes(s.id),
                    ),
                    merchants: corpus?.merchants
                      .filter(
                        (m: Row) => !corpus.removedMerchants.includes(m.id),
                      )
                      .map((m: Row) =>
                        cleanMerchant({
                          ...m,
                          offers: m.offers.filter(
                            (o: Row) =>
                              !corpus.removedServices.includes(o.serviceId),
                          ),
                        }),
                      )
                      .filter((m: Row) => m.offers.length),
                  };
                  const url = URL.createObjectURL(
                    new Blob([JSON.stringify(data, null, 2)], {
                      type: "application/json",
                    }),
                  );
                  const link = document.createElement("a");
                  link.href = url;
                  link.download = "service-corpus.json";
                  link.click();
                  setTimeout(() => URL.revokeObjectURL(url), 1000);
                }}
              >
                <Download size={15} />
                导出当前语料
              </button>
            </div>
          </div>
        </section>
      </div>

      <div className="market-graph-card">
        <div className="market-graph-header">
          <div>
            <h3>领域服务依赖拓扑与实时 QoS 概览</h3>
            {graph && (
              <p className="market-graph-meta">
                当前 {graph.serviceCount} 类服务 · {graph.merchantCount} 个商家 ·
                资料版本 {graph.revision}
              </p>
            )}
          </div>
          <div className="market-graph-legend">
            <span>
              <i className="legend-swatch sales" /> 销售分析链路
            </span>
            <span>
              <i className="legend-swatch travel" /> 旅行规划链路
            </span>
            <span>
              <i className="legend-swatch skyline" /> ★ 含帕累托最优报价
            </span>
          </div>
        </div>
        <div className="planning-graph market-topology-graph">
          <ReactFlow
            nodes={flowNodes}
            edges={flowEdges}
            fitView
            fitViewOptions={{ padding: 0.16 }}
            nodesDraggable={false}
            nodesConnectable={false}
            onNodeClick={(_event, n) => openDetail("service", n.id)}
          >
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      </div>

      <section
        className="content-section service-catalog"
        aria-label="服务能力"
      >
        <div className="market-section-header">
          <h3 className="section-title">
            <Layers size={17} /> 服务能力目录与帕累托（Skyline）基准
          </h3>
          <small>点击任意服务卡片或图中节点，可对比全部关联商家、查看 QoS 散点分布并维护契约</small>
        </div>
        <div className="service-definition-grid">
          {(corpus?.services || [])
            .filter(
              (n: Row) =>
                !corpus?.removedServices.includes(n.id) &&
                (scenarioFilter === "all" || n.scenario === scenarioFilter) &&
                matches(`${n.name} ${n.description}`),
            )
            .map((n: Row) => {
              const graphNode = nodes.find((gn: Row) => gn.id === n.id);
              const providers = graphNode?.providers || [];
              const stats = analyzeProviders(providers);
              const onlineCount = providers.filter((p: Row) => p.online).length;
              return (
                <article className="service-definition" key={n.id}>
                  <div className="service-card-top">
                    <span className="market-domain-tag">
                      {scenarioLabels[n.scenario] || n.scenario}
                    </span>
                    <span
                      className={`market-exec-pill ${graphNode?.executable ? "is-ready" : "is-pending"}`}
                    >
                      {graphNode?.executable
                        ? `${onlineCount}/${providers.length} 在线`
                        : "待接入/离线"}
                    </span>
                  </div>
                  <h4>{n.name}</h4>
                  <p>{n.description}</p>
                  <div className="service-card-contract">
                    <span>输入：{n.inputType || "无"}</span>
                    <span>输出：{n.outputType || "无"}</span>
                  </div>
                  {providers.length > 0 && (
                    <div className="service-card-qos">
                      <div>
                        <small>最低费用</small>
                        <strong>
                          {stats.minPrice != null
                            ? `${stats.minPrice.toFixed(2)} USDC`
                            : "待报价"}
                        </strong>
                      </div>
                      <div>
                        <small>最快耗时</small>
                        <strong>
                          {stats.minSec != null ? `${stats.minSec}s` : "—"}
                        </strong>
                      </div>
                      <div>
                        <small>最高成功率</small>
                        <strong>
                          {stats.maxRate != null
                            ? `${stats.maxRate.toFixed(0)}%`
                            : "—"}
                        </strong>
                      </div>
                    </div>
                  )}
                  <button
                    aria-label={`查看详情：${n.name}`}
                    onClick={() => openDetail("service", n.id)}
                  >
                    查看详情
                  </button>
                </article>
              );
            })}
        </div>
      </section>

      <section className="panel service-instances" aria-label="在线服务实例">
        <div className="panel-heading">
          <h2>
            在线服务实例 ·{" "}
            {activeAgents.filter((a) => a.status === "ONLINE").length}
          </h2>
        </div>
        {!activeAgents.length && (
          <p className="collection-empty">没有匹配的服务实例。</p>
        )}
        <div className="agent-grid">
          {activeAgents.map((a) => {
            const merchantObj = corpus?.merchants.find(
              (m: Row) => m.id === a.agentId,
            );
            return (
              <article className="agent-card" key={a.agentId}>
                <div className="agent-title">
                  <span className="agent-avatar">
                    <Network size={19} />
                  </span>
                  <strong>{merchantObj?.name || a.agentId}</strong>
                  <span
                    className={`tag ${a.status === "ONLINE" ? "text-good" : ""}`}
                  >
                    {a.status === "ONLINE" ? "在线" : "离线"}
                  </span>
                </div>
                <p>{a.skills.map(serviceName).join(" / ")}</p>
                <div className="agent-meta-row">
                  <small>{a.agentId}</small>
                  {a.wallet && (
                    <code title={a.wallet}>
                      {a.wallet.slice(0, 6)}…{a.wallet.slice(-4)}
                    </code>
                  )}
                </div>
                <button
                  aria-label={`查看详情：${a.agentId}`}
                  onClick={() => openDetail("merchant", a.agentId)}
                >
                  查看详情
                </button>
              </article>
            );
          })}
        </div>
      </section>

      {!!offlineMerchants.length && (
        <section className="panel service-instances" aria-label="未在线的商家">
          <div className="panel-heading">
            <h2>未在线的商家</h2>
          </div>
          <div className="agent-grid">
            {offlineMerchants.map((m: Row) => (
              <article className="agent-card" key={m.id}>
                <div className="agent-title">
                  <strong>{m.name}</strong>
                  <span className="tag">未在线</span>
                </div>
                <p>
                  {m.offers
                    .map((o: Row) => serviceName(o.serviceId))
                    .join(" / ")}
                </p>
                <button
                  aria-label={`查看详情：${m.id}`}
                  onClick={() => openDetail("merchant", m.id)}
                >
                  查看详情
                </button>
              </article>
            ))}
          </div>
        </section>
      )}

      {dialogOpen && (
        <Modal title={dialogTitle} busy={busy} onClose={closeDialog}>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          {busy && <p role="status">正在保存更改…</p>}
          {detail?.kind === "merchant" && !editor && !pendingDelete && (
            <section className="merchant-detail">
              <dl className="service-facts">
                <div>
                  <dt>商家标识</dt>
                  <dd>{detail.id}</dd>
                </div>
                <div>
                  <dt>当前状态</dt>
                  <dd>{instance?.status === "ONLINE" ? "在线" : "未在线"}</dd>
                </div>
                {instance && (
                  <>
                    <div>
                      <dt>钱包地址</dt>
                      <dd>{instance.wallet}</dd>
                    </div>
                    <div>
                      <dt>服务地址</dt>
                      <dd>{instance.endpoint}</dd>
                    </div>
                    <div>
                      <dt>注册版本</dt>
                      <dd>{instance.bindingVersion}</dd>
                    </div>
                  </>
                )}
              </dl>
              <h3>提供的服务</h3>
              {(
                merchant?.offers ||
                instance?.skills.map((id: string) => ({ serviceId: id })) ||
                []
              )
                .filter(
                  (o: Row) => !corpus?.removedServices.includes(o.serviceId),
                )
                .map((o: Row) => {
                  const provider = nodes
                    .find((n: Row) => n.id === o.serviceId)
                    ?.providers.find((p: Row) => p.id === detail.id);
                  return (
                    <article className="service-offer" key={o.serviceId}>
                      <h4>{serviceName(o.serviceId)}</h4>
                      <ServiceMetrics
                        cost={
                          o.price != null && o.price !== ""
                            ? `${o.price} USDC`
                            : "待报价"
                        }
                        seconds={o.seconds}
                        history={provider?.history}
                      />
                    </article>
                  );
                })}
              <HistorySources
                providers={nodes.flatMap((n: Row) =>
                  n.providers.filter((p: Row) => p.id === detail.id),
                )}
              />
              <div className="modal-actions">
                <button
                  className="danger-outline"
                  disabled={busy}
                  onClick={() =>
                    setPendingDelete({
                      kind: "merchant",
                      id: detail.id,
                      name: merchant?.name || detail.id,
                    })
                  }
                >
                  删除商家
                </button>
                <button
                  className="primary"
                  disabled={busy || !corpus}
                  onClick={() =>
                    editMerchant(merchant || { id: detail.id, name: detail.id })
                  }
                >
                  修改商家和服务
                </button>
              </div>
            </section>
          )}
          {node && !editor && !pendingDelete && (
            <section className="workspace-panel">
              <div className="service-detail-summary">
                <p>{node.description}</p>
                {node.acceptance && <p>验收：{node.acceptance}</p>}
                {node.scope && (
                  <>
                    <p>范围：{node.scope.supported}</p>
                    <p>{node.scope.limits.join("；")}</p>
                    <p>执行支持：{node.scope.executionSupport}</p>
                  </>
                )}
              </div>
              <div className="service-detail-actions">
                <button disabled={busy} onClick={() => editService(node)}>
                  修改服务资料
                </button>
                <button
                  className="danger-outline"
                  disabled={busy}
                  onClick={() =>
                    setPendingDelete({
                      kind: "service",
                      id: node.id,
                      name: node.name,
                    })
                  }
                >
                  删除服务节点
                </button>
              </div>

              <ParetoScatterChart providers={node.providers || []} />

              <div className="template-cards">
                {(node.providers || []).map((p: Row) => {
                  const pMeta = nodeProviderAnalysis.byId[p.id];
                  return (
                    <article
                      className={`task-editor ${pMeta?.isSkyline ? "is-skyline-card" : ""}`}
                      key={p.id}
                    >
                      <div className="provider-card-head">
                        <h4>{p.name}</h4>
                        <div className="provider-badges">
                          {pMeta?.isSkyline && (
                            <span className="skyline-badge">
                              ★ 帕累托最优 (Skyline)
                            </span>
                          )}
                          {pMeta?.isCheapest && (
                            <span className="qos-tag">💰 最低报价</span>
                          )}
                          {pMeta?.isFastest && (
                            <span className="qos-tag">⚡ 最快响应</span>
                          )}
                          {pMeta?.isMostReliable && (
                            <span className="qos-tag">🛡️ 最高成功率</span>
                          )}
                        </div>
                      </div>
                      <p>
                        {p.online
                          ? p.busy
                            ? "在线 · 正在履约"
                            : "在线 · 可接单"
                          : p.connected
                            ? "离线"
                            : "尚未接通"}{" "}
                        · {p.id}
                        {p.executionMessages && (
                          <span> · 支持执行信息查询</span>
                        )}
                      </p>
                      <ServiceMetrics
                        cost={
                          p.price != null && p.price !== ""
                            ? `${price(p.price)} USDC`
                            : "待报价"
                        }
                        seconds={p.seconds}
                        history={p.history}
                        riskAdjusted={pMeta?.riskAdjusted}
                      />
                      <button disabled={busy} onClick={() => editMerchant(p)}>
                        修改商家和服务
                      </button>
                      <button
                        disabled={busy}
                        onClick={() =>
                          setPendingDelete({
                            kind: "merchant",
                            id: p.id,
                            name: p.name,
                          })
                        }
                      >
                        删除商家
                      </button>
                    </article>
                  );
                })}
              </div>
              <HistorySources providers={node.providers || []} />
            </section>
          )}
          {pendingDelete && (
            <section className="workspace-panel">
              <p>
                将移除它在业务图中的关联，尚未付款的相关方案需要重新选择。已执行任务和历史成果保留。
              </p>
              <button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await api(
                      `${prefix}/service-graph/${pendingDelete.kind}/${pendingDelete.id}/delete`,
                      { revision: corpus?.revision },
                    );
                    setPendingDelete(null);
                    setDetail(null);
                    await refresh();
                    onChange();
                    setNotice("已删除；注册心跳不会自动恢复该项。");
                  })
                }
              >
                确认删除
              </button>
              <button disabled={busy} onClick={() => setPendingDelete(null)}>
                取消
              </button>
            </section>
          )}
          {editor && (
            <section className="workspace-panel">
              <fieldset disabled={busy}>
                <label>
                  唯一标识（与注册接口一致）
                  <input
                    value={editor.id}
                    disabled={!!detail}
                    maxLength={64}
                    onChange={(e) =>
                      setEditor({ ...editor, id: e.target.value })
                    }
                  />
                </label>
                <label>
                  显示名称
                  <input
                    value={editor.name}
                    maxLength={120}
                    onChange={(e) =>
                      setEditor({ ...editor, name: e.target.value })
                    }
                  />
                </label>
                {kind === "service" ? (
                  <>
                    <label>
                      业务说明
                      <textarea
                        value={editor.description}
                        maxLength={1500}
                        onChange={(e) =>
                          setEditor({ ...editor, description: e.target.value })
                        }
                      />
                    </label>
                    <label>
                      检索词（逗号分隔）
                      <input
                        value={editor.keywords.join(",")}
                        onChange={(e) =>
                          setEditor({
                            ...editor,
                            keywords: e.target.value
                              .split(/[,，]/)
                              .map((s) => s.trim()),
                          })
                        }
                      />
                    </label>
                    <label>
                      场景
                      <select
                        value={editor.scenario}
                        onChange={(e) =>
                          setEditor({ ...editor, scenario: e.target.value })
                        }
                      >
                        <option value="cross_org_analysis">销售分析</option>
                        <option value="travel_planning">旅行规划</option>
                        <option value="unclassified">新业务 / 待接入</option>
                      </select>
                    </label>
                    <section className="content-section">
                      <h3 className="section-title">输入输出与依赖契约</h3>
                      <label>
                        输入类型
                        <input
                          value={editor.inputType}
                          onChange={(e) =>
                            setEditor({ ...editor, inputType: e.target.value })
                          }
                        />
                      </label>
                      <label>
                        输出类型
                        <input
                          value={editor.outputType}
                          onChange={(e) =>
                            setEditor({ ...editor, outputType: e.target.value })
                          }
                        />
                      </label>
                      <label>
                        需要的前置成果类型（逗号分隔）
                        <input
                          value={editor.requires.join(",")}
                          onChange={(e) =>
                            setEditor({
                              ...editor,
                              requires: e.target.value
                                .split(/[,，]/)
                                .map((s) => s.trim()),
                            })
                          }
                        />
                      </label>
                    </section>
                    <p className="hint">
                      新增定义还需关联至少一个商家才会成为图节点。已有服务的执行与验收契约不能通过改名称替换。
                    </p>
                  </>
                ) : (
                  <>
                    {editor.offers.map((offer: Row, i: number) => {
                      const h = offer.recent || {
                        successes: offer.history.filter((x: Row) => x.success)
                          .length,
                        total: offer.history.length,
                        asOf:
                          offer.history.at(-1)?.at ||
                          Math.floor(Date.now() / 1000),
                      };
                      const change = (patch: Row) =>
                        setEditor({
                          ...editor,
                          offers: editor.offers.map((x: Row, j: number) =>
                            j === i ? { ...x, ...patch } : x,
                          ),
                        });
                      return (
                        <section className="task-editor" key={i}>
                          <label>
                            服务标识
                            <input
                              list="service-ids"
                              value={offer.serviceId}
                              onChange={(e) =>
                                change({ serviceId: e.target.value })
                              }
                            />
                          </label>
                          <label>
                            预计费用（USDC）
                            <input
                              inputMode="decimal"
                              value={offer.price}
                              onChange={(e) =>
                                change({ price: e.target.value })
                              }
                            />
                          </label>
                          <label>
                            预计耗时（秒）
                            <input
                              type="number"
                              min={1}
                              max={3600}
                              value={offer.seconds}
                              onChange={(e) =>
                                change({ seconds: Number(e.target.value) })
                              }
                            />
                          </label>
                          <p className="hint">
                            最近最多 50
                            次履约；人工录入会与链上核验、演示数据分别标明。
                          </p>
                          <label>
                            统计次数
                            <input
                              type="number"
                              min={0}
                              max={50}
                              value={h.total}
                              onChange={(e) =>
                                change({
                                  history: [],
                                  recent: {
                                    ...h,
                                    total: Number(e.target.value),
                                    asOf: Math.floor(Date.now() / 1000),
                                  },
                                })
                              }
                            />
                          </label>
                          <label>
                            成功次数
                            <input
                              type="number"
                              min={0}
                              max={h.total}
                              value={h.successes}
                              onChange={(e) =>
                                change({
                                  history: [],
                                  recent: {
                                    ...h,
                                    successes: Number(e.target.value),
                                    asOf: Math.floor(Date.now() / 1000),
                                  },
                                })
                              }
                            />
                          </label>
                          <button
                            type="button"
                            onClick={() =>
                              setEditor({
                                ...editor,
                                offers: editor.offers.filter(
                                  (_x: Row, j: number) => j !== i,
                                ),
                              })
                            }
                          >
                            移除此服务关联
                          </button>
                        </section>
                      );
                    })}
                    <datalist id="service-ids">
                      {corpus?.services
                        .filter(
                          (s: Row) => !corpus.removedServices.includes(s.id),
                        )
                        .map((s: Row) => (
                          <option value={s.id} key={s.id}>
                            {s.name}
                          </option>
                        ))}
                    </datalist>
                    <button
                      type="button"
                      onClick={() =>
                        setEditor({
                          ...editor,
                          offers: [
                            ...editor.offers,
                            {
                              serviceId: "",
                              price: "1",
                              seconds: 10,
                              history: [],
                            },
                          ],
                        })
                      }
                    >
                      添加提供的服务
                    </button>
                  </>
                )}
                <label className="check-row">
                  <input
                    type="checkbox"
                    checked={restore}
                    onChange={(e) => setRestore(e.target.checked)}
                  />
                  这是重新加入已删除的对象
                </label>
                <button
                  className="primary"
                  type="button"
                  onClick={() => void run(save)}
                >
                  保存资料
                </button>
                <button type="button" onClick={() => setEditor(null)}>
                  取消
                </button>
              </fieldset>
            </section>
          )}
        </Modal>
      )}
    </section>
  );
}
