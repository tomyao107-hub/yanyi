import { AlertCircle, Check, Circle, Clock3, LoaderCircle, Pause, type LucideIcon } from "lucide-react";

const statusMap: Record<
  string,
  { label: string; className: string; icon: LucideIcon }
> = {
  created: {
    label: "待解析",
    className: "border-ink-200 bg-ink-50 text-ink-600 dark:border-ink-700 dark:bg-ink-800",
    icon: Circle,
  },
  parsing: {
    label: "解析中",
    className: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/50 dark:text-blue-300",
    icon: LoaderCircle,
  },
  ready: {
    label: "待翻译",
    className: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/50 dark:text-amber-300",
    icon: Clock3,
  },
  pending: {
    label: "待翻译",
    className: "border-ink-200 bg-ink-50 text-ink-600 dark:border-ink-700 dark:bg-ink-800",
    icon: Clock3,
  },
  translating: {
    label: "翻译中",
    className: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/50 dark:text-blue-300",
    icon: LoaderCircle,
  },
  processing: {
    label: "翻译中",
    className: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/50 dark:text-blue-300",
    icon: LoaderCircle,
  },
  stopped: {
    label: "已暂停",
    className: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/50 dark:text-amber-300",
    icon: Pause,
  },
  done: {
    label: "已翻译",
    className: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-300",
    icon: Check,
  },
  reviewed: {
    label: "已校对",
    className: "border-violet-200 bg-violet-50 text-violet-700 dark:border-violet-900 dark:bg-violet-950/50 dark:text-violet-300",
    icon: Check,
  },
  error: {
    label: "需处理",
    className: "border-cinnabar-200 bg-cinnabar-50 text-cinnabar-700 dark:border-cinnabar-900 dark:bg-cinnabar-950/50 dark:text-cinnabar-300",
    icon: AlertCircle,
  },
};

export function statusLabel(status: string): string {
  return statusMap[status]?.label ?? status;
}

export function StatusBadge({ status, showIcon = true }: { status: string; showIcon?: boolean }) {
  const config = statusMap[status] ?? statusMap.created;
  const Icon = config.icon;
  const spinning = status === "processing" || status === "translating" || status === "parsing";
  return (
    <span className={`badge whitespace-nowrap ${config.className}`}>
      {showIcon ? <Icon className={`size-3 ${spinning ? "animate-spin" : ""}`} /> : null}
      {statusMap[status]?.label ?? status}
    </span>
  );
}
