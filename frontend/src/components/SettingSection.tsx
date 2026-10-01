import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function SettingSection({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="surface rounded-2xl">
      <div className="flex items-start gap-3 border-b hairline px-5 py-4 sm:px-6">
        <span className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-xl bg-ink-100 text-ink-600 dark:bg-ink-800 dark:text-ink-300">
          <Icon className="size-4" />
        </span>
        <div>
          <h2 className="font-serif text-lg font-semibold text-ink-950 dark:text-white">{title}</h2>
          <p className="mt-0.5 text-xs leading-5 text-ink-500 dark:text-ink-400">{description}</p>
        </div>
      </div>
      <div className="p-5 sm:p-6">{children}</div>
    </section>
  );
}
