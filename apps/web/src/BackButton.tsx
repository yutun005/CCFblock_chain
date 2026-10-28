import type { ComponentPropsWithoutRef } from "react";
import { ArrowLeft } from "lucide-react";

export default function BackButton({
  children,
  className = "",
  ...props
}: ComponentPropsWithoutRef<"button">) {
  return (
    <button {...props} type="button" className={`back-button ${className}`.trim()}>
      <ArrowLeft size={18} strokeWidth={1.8} aria-hidden="true" />
      <span>{children}</span>
    </button>
  );
}
