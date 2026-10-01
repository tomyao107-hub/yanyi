import { AlertTriangle, Bug, CircleAlert, Info } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { RuntimeLogLevel } from "../api/types";

export interface LevelMeta {
  label: string;
  className: string;
  dotClass: string;
  icon: LucideIcon;
}

export const levelMeta: Record<RuntimeLogLevel, LevelMeta> = {
  debug: {
    label: "调试",
    className:
      "border-ink-200 bg-ink-50 text-ink-500 dark:border-ink-700 dark:bg-ink-800 dark:text-ink-300",
    dotClass: "bg-ink-400",
    icon: Bug,
  },
  info: {
    label: "信息",
    className:
      "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300",
    dotClass: "bg-blue-500",
    icon: Info,
  },
  warning: {
    label: "警告",
    className:
      "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300",
    dotClass: "bg-amber-500",
    icon: AlertTriangle,
  },
  error: {
    label: "错误",
    className:
      "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300",
    dotClass: "bg-red-500",
    icon: CircleAlert,
  },
};

export function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}
