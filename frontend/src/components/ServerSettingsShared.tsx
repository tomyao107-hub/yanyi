import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function ServerSection({
  icon: Icon,
  title,
  description,
  action,
  children,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="surface overflow-hidden rounded-2xl">
      <div className="flex flex-col gap-4 border-b hairline px-5 py-4 sm:flex-row sm:items-start sm:justify-between sm:px-6">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-xl bg-ink-100 text-ink-600 dark:bg-ink-800 dark:text-ink-300">
            <Icon className="size-4" />
          </span>
          <div className="min-w-0">
            <h2 className="font-serif text-lg font-semibold text-ink-950 dark:text-white">{title}</h2>
            <p className="mt-0.5 text-xs leading-5 text-ink-500 dark:text-ink-400">{description}</p>
          </div>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

export function EmptyMessage({ children }: { children: ReactNode }) {
  return (
    <div className="px-5 py-8 text-center text-sm text-ink-500 sm:px-6">
      {children}
    </div>
  );
}

export function StatusPill({
  tone,
  children,
}: {
  tone: "success" | "warning" | "neutral";
  children: ReactNode;
}) {
  const classes =
    tone === "success"
      ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300"
      : tone === "warning"
        ? "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300"
        : "border-ink-200 bg-ink-50 text-ink-600 dark:border-ink-700 dark:bg-ink-800 dark:text-ink-300";
  return <span className={`badge ${classes}`}>{children}</span>;
}
