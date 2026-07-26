import { AlertTriangle, RefreshCw } from "lucide-react";
import { isRouteErrorResponse, useRouteError } from "react-router-dom";

export function PageError({
  title = "页面暂时无法显示",
  message,
  onRetry,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="mx-auto flex min-h-[50vh] max-w-lg flex-col items-center justify-center px-6 text-center">
      <div className="grid size-12 place-items-center rounded-2xl bg-cinnabar-50 text-cinnabar-700 dark:bg-cinnabar-950/50 dark:text-cinnabar-300">
        <AlertTriangle className="size-5" />
      </div>
      <h1 className="mt-4 font-serif text-xl font-semibold text-ink-950 dark:text-white">{title}</h1>
      <p className="mt-2 text-sm leading-6 text-ink-500 dark:text-ink-400">
        {message ?? "请确认后端服务已启动，然后重新尝试。"}
      </p>
      <button type="button" className="btn-secondary mt-5" onClick={onRetry ?? (() => window.location.reload())}>
        <RefreshCw className="size-4" />
        重新加载
      </button>
    </div>
  );
}

export function RouteErrorPage() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : undefined;
  return <PageError title="这个页面走丢了" message={message} />;
}
