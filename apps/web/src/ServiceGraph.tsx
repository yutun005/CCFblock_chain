import { useCallback, useEffect, useState } from "react";
import { Background, Controls, ReactFlow } from "@xyflow/react";
import { price } from "./amount";
import { Network } from "lucide-react";
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
const rate = (h: Row) =>
  h?.window
    ? `${((100 * h.settled) / h.window).toFixed(2)}%（${h.settled}/${h.window}）`
    : "未知";

function ServiceMetrics({
  cost,
  seconds,
  history,
}: {
  cost: string;
  seconds?: number;
  history?: Row;
}) {
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
        <dd>{history ? rate(history) : "暂无记录"}</dd>
      </div>
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
  const nodes = graph?.nodes || [];
  const shown = nodes.filter(
    (n: Row) =>
      !query.trim() ||
      `${n.id} ${n.name} ${n.description} ${n.providers.map((p: Row) => p.name).join(" ")}`
        .toLowerCase()
        .includes(query.trim().toLowerCase()),
  );
  const levels: Record<string, number> = {};
  for (let pass = 0; pass < nodes.length; pass++)
    for (const n of nodes) {
      const before =
        graph?.edges
          .filter((e: Row) => e.target === n.id)
          .map((e: Row) => e.source) || [];
      if (!(n.id in levels) && before.every((id: string) => id in levels))
        levels[n.id] =
          Math.max(-1, ...before.map((id: string) => levels[id])) + 1;
    }
  const columns: Record<number, number> = {};
  const visible = new Set(shown.map((n: Row) => n.id));
  const flowNodes = shown.map((n: Row) => {
    const level = levels[n.id] || 0,
      column = columns[level] || 0;
    columns[level] = column + 1;
    return {
      id: n.id,
      position: { x: column * 260, y: level * 140 },
      data: {
        label: (
          <>
            <b>{n.name}</b>
            <br />
            {n.providers.length} 个商家 ·{" "}
            {n.executable
              ? "可执行"
              : n.adapterReady
                ? "暂无在线商家"
                : "执行接口待接入"}
          </>
        ),
      },
      style: {
        width: 230,
        background: selected === n.id ? "#244d78" : "#152638",
        color: "#eaf3ff",
        border: "1px solid #668ba9",
      },
    };
  });
  const flowEdges = (graph?.edges || [])
    .filter((e: Row) => visible.has(e.source) && visible.has(e.target))
    .map((e: Row) => ({
      id: `${e.source}-${e.target}`,
      source: e.source,
      target: e.target,
      style: { stroke: "#72b5ef", strokeWidth: 2 },
    }));
  const node =
    detail?.kind === "service"
      ? nodes.find((n: Row) => n.id === detail.id) ||
        corpus?.services.find((n: Row) => n.id === detail.id)
      : null;
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
    <section>
      <h2>服务业务图与商家</h2>
      <p>
        先有商家能提供的服务，再从图中拆解任务。点击服务节点，查看所有关联商家。箭头表示服务所需的前置成果。
      </p>
      <p className="hint">
        初始化语料：16 个演示商家、8 类服务、32 项报价、1600
        次模拟履约记录。新增商家或服务可以追加；未配置执行和验收接口的新业务会入图，但不会被当成可执行能力。
      </p>
      {error && !dialogOpen && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {busy && !dialogOpen && <p role="status">正在更新服务库…</p>}
      <div className="workspace-panel">
        <button disabled={busy} onClick={() => void run(refresh)}>
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
          新增商家
        </button>
        <button
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
          新增服务定义
        </button>
        <label>
          搜索服务或商家
          <input value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <section className="content-section">
          <h3 className="section-title">批量导入 / 导出语料</h3>
          <p>
            JSON 包含 services 和 merchants
            数组。导出当前语料可作为格式示例；导入只新增或更新列出的对象，不会删除其他数据。
          </p>
          <label className="check-row">
            <input
              type="checkbox"
              checked={restore}
              onChange={(e) => setRestore(e.target.checked)}
            />
            允许恢复本次导入中明确列出的已删除项
          </label>
          <input
            aria-label="导入服务语料 JSON"
            type="file"
            accept=".json"
            disabled={busy || !corpus}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (!file) return;
              void run(async () => {
                if (file.size > 2_000_000) throw new Error("文件不能超过 2 MB");
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
                  .filter((m: Row) => !corpus.removedMerchants.includes(m.id))
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
            导出当前语料
          </button>
        </section>
      </div>
      {graph && (
        <p>
          当前 {graph.serviceCount} 类服务 · {graph.merchantCount} 个商家 ·
          资料版本 {graph.revision}
        </p>
      )}
      <div className="planning-graph">
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          fitView
          nodesDraggable={false}
          nodesConnectable={false}
          onNodeClick={(_event, n) => openDetail("service", n.id)}
        >
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
      <section
        className="content-section service-catalog"
        aria-label="服务能力"
      >
        <h3 className="section-title">服务能力</h3>
        <div className="service-definition-grid">
          {(corpus?.services || [])
            .filter(
              (n: Row) =>
                !corpus?.removedServices.includes(n.id) &&
                matches(`${n.name} ${n.description}`),
            )
            .map((n: Row) => (
              <article className="service-definition" key={n.id}>
                <h4>{n.name}</h4>
                <p>{n.description}</p>
                <button
                  aria-label={`查看详情：${n.name}`}
                  onClick={() => openDetail("service", n.id)}
                >
                  查看详情
                </button>
              </article>
            ))}
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
          {activeAgents.map((a) => (
            <article className="agent-card" key={a.agentId}>
              <div className="agent-title">
                <span className="agent-avatar">
                  <Network size={19} />
                </span>
                <strong>
                  {corpus?.merchants.find((m: Row) => m.id === a.agentId)
                    ?.name || a.agentId}
                </strong>
                <span
                  className={`tag ${a.status === "ONLINE" ? "text-good" : ""}`}
                >
                  {a.status === "ONLINE" ? "在线" : "离线"}
                </span>
              </div>
              <p>{a.skills.map(serviceName).join(" / ")}</p>
              <small>{a.agentId}</small>
              <button
                aria-label={`查看详情：${a.agentId}`}
                onClick={() => openDetail("merchant", a.agentId)}
              >
                查看详情
              </button>
            </article>
          ))}
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
              <p>{node.description}</p>
              {node.acceptance && <p>验收：{node.acceptance}</p>}
              {node.scope && (
                <>
                  <p>范围：{node.scope.supported}</p>
                  <p>{node.scope.limits.join("；")}</p>
                  <p>执行支持：{node.scope.executionSupport}</p>
                </>
              )}
              <button disabled={busy} onClick={() => editService(node)}>
                修改服务资料
              </button>
              <button
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
              <div className="template-cards">
                {(node.providers || []).map((p: Row) => (
                  <article className="task-editor" key={p.id}>
                    <h4>{p.name}</h4>
                    <p>
                      {p.online
                        ? p.busy
                          ? "在线 · 正在履约"
                          : "在线 · 可接单"
                        : p.connected
                          ? "离线"
                          : "尚未接通"}{" "}
                      · {p.id}
                      {p.executionMessages && <span> · 支持执行信息查询</span>}
                    </p>
                    <ServiceMetrics
                      cost={
                        p.price != null && p.price !== ""
                          ? `${price(p.price)} USDC`
                          : "待报价"
                      }
                      seconds={p.seconds}
                      history={p.history}
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
                ))}
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
