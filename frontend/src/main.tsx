import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { RouterProvider } from "react-router-dom";
import { router } from "./router";
import { SettingsProvider } from "./store/settings";
import { ToastProvider } from "./store/toast";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      gcTime: 5 * 60_000,
      retry: (failureCount, error) => {
        const status =
          typeof error === "object" && error !== null && "status" in error
            ? Number((error as unknown as { status: unknown }).status)
            : 0;
        return status >= 400 && status < 500 ? false : failureCount < 2;
      },
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: false,
    },
  },
});

document.documentElement.dataset.appBuild = "2026-07-27-cache-refresh";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <SettingsProvider>
        <ToastProvider>
          <RouterProvider router={router} />
        </ToastProvider>
      </SettingsProvider>
      {import.meta.env.DEV ? <ReactQueryDevtools initialIsOpen={false} /> : null}
    </QueryClientProvider>
  </React.StrictMode>,
);
