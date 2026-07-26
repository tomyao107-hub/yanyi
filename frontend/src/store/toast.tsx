import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";

type ToastTone = "success" | "error" | "info";

interface Toast {
  id: number;
  message: string;
  tone: ToastTone;
}

interface ToastContextValue {
  notify: (message: string, tone?: ToastTone) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const notify = useCallback(
    (message: string, tone: ToastTone = "info") => {
      const id = Date.now() + Math.round(Math.random() * 1000);
      setToasts((current) => [...current.slice(-3), { id, message, tone }]);
      window.setTimeout(() => dismiss(id), 3800);
    },
    [dismiss],
  );

  const value = useMemo(() => ({ notify }), [notify]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="false"
        className="fixed bottom-4 right-4 z-[100] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
      >
        {toasts.map((toast) => {
          const Icon =
            toast.tone === "success" ? CheckCircle2 : toast.tone === "error" ? AlertCircle : Info;
          return (
            <div
              key={toast.id}
              role={toast.tone === "error" ? "alert" : "status"}
              className="surface flex animate-slide-up items-start gap-3 rounded-xl p-3.5 text-sm"
            >
              <Icon
                className={`mt-0.5 size-4 shrink-0 ${
                  toast.tone === "success"
                    ? "text-emerald-600 dark:text-emerald-400"
                    : toast.tone === "error"
                      ? "text-cinnabar-600 dark:text-cinnabar-400"
                      : "text-blue-600 dark:text-blue-400"
                }`}
              />
              <span className="min-w-0 flex-1 leading-5">{toast.message}</span>
              <button
                type="button"
                className="icon-btn -m-2 size-8"
                onClick={() => dismiss(toast.id)}
                aria-label="关闭通知"
              >
                <X className="size-4" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider");
  return context;
}
