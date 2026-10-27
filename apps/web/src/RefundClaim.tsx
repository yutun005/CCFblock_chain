import { useState } from "react";
import { amount } from "./amount";

type Row = Record<string, any>;

export default function RefundClaim({
  jobId,
  chain,
  api,
  onChange,
}: {
  jobId: string;
  chain: Row;
  api: (path: string, body?: unknown) => Promise<any>;
  onChange: () => Promise<unknown>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState<Row | null>(null);
  const current = receipt || chain;
  const remaining = BigInt(current.refundCredit || "0");
  const returned = BigInt(current.refunded || "0");
  const received = returned > 0n && remaining === 0n;

  async function claim() {
    setBusy(true);
    setError("");
    try {
      const result = await api(`/api/jobs/${jobId}/refund`, {});
      setReceipt(result.chain);
      // The confirmed refund remains visible even if the following page refresh fails.
      await onChange().catch(() => {});
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="refund-panel" aria-label="领取退还代币">
      <div>
        <h2>退还剩余代币</h2>
        <p role="status">
          {received
            ? `已退还 ${amount(returned)} 代币到你的钱包。`
            : current.finalized
              ? remaining > 0n
                ? `任务已结算，剩余 ${amount(remaining)} 代币。点击领取后退回你的钱包。`
                : "本次预算已全部用于服务，无剩余代币。"
              : "任务结算后，在这里领取未使用的代币。"}
        </p>
        {error && (
          <p className="inline-error" role="alert">
            退还未完成：{error}，请重试。
          </p>
        )}
      </div>
      <button
        className="primary"
        disabled={busy || !current.finalized || remaining <= 0n}
        onClick={() => void claim()}
      >
        {busy
          ? "正在退还…"
          : received
            ? "已领取退还代币"
            : current.finalized && remaining === 0n
              ? "无剩余代币"
              : "领取退还代币"}
      </button>
    </section>
  );
}
