// Format display amounts without converting token balances through floating point.
export function tokenUnits(value: unknown): bigint {
  const parts = /^(-?)(\d+)(?:\.(\d{0,6}))?$/.exec(String(value));
  if (!parts) throw new Error("Invalid token amount");
  const units =
    BigInt(parts[2]) * 1000000n + BigInt((parts[3] || "").padEnd(6, "0"));
  return parts[1] ? -units : units;
}

export function amount(value: unknown): string {
  if (value === undefined || value === null) return "—";
  const units = BigInt(String(value));
  const cents = ((units < 0n ? -units : units) + 5000n) / 10000n;
  return (
    (units < 0n && cents !== 0n ? "-" : "") +
    (cents / 100n).toString() +
    "." +
    (cents % 100n).toString().padStart(2, "0")
  );
}

export function price(value: unknown): string {
  return value === undefined || value === null
    ? "—"
    : amount(tokenUnits(value));
}
