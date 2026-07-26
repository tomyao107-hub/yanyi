interface ProgressBarProps {
  value: number;
  label?: string;
  compact?: boolean;
  tone?: "default" | "success";
}

export function ProgressBar({ value, label, compact = false, tone = "default" }: ProgressBarProps) {
  const safeValue = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  return (
    <div className="w-full">
      {label ? (
        <div className="mb-1.5 flex items-center justify-between text-xs text-ink-500 dark:text-ink-400">
          <span>{label}</span>
          <span className="font-mono tabular-nums">{Math.round(safeValue)}%</span>
        </div>
      ) : null}
      <div
        className={`${compact ? "h-1.5" : "h-2"} overflow-hidden rounded-full bg-ink-100 dark:bg-ink-800`}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(safeValue)}
        aria-label={label ?? "翻译进度"}
      >
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${
            tone === "success" ? "bg-emerald-600" : "bg-cinnabar-600"
          }`}
          style={{ width: `${safeValue}%` }}
        />
      </div>
    </div>
  );
}
