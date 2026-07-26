import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  compact = false,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={`mx-auto flex max-w-md flex-col items-center text-center ${compact ? "py-8" : "py-20"}`}>
      <div className="mb-4 grid size-12 place-items-center rounded-2xl border border-ink-200 bg-white text-ink-500 shadow-sm dark:border-ink-700 dark:bg-ink-900 dark:text-ink-400">
        <Icon className="size-5" />
      </div>
      <h2 className="font-serif text-lg font-semibold text-ink-900 dark:text-white">{title}</h2>
      <p className="mt-2 text-sm leading-6 text-ink-500 dark:text-ink-400">{description}</p>
      {action ? <div className="mt-5">{action}</div> : null}
    </div>
  );
}
