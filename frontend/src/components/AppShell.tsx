import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpenText, LibraryBig, LogOut, Settings2 } from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api } from "../api/client";
import { queryKeys } from "../api/queryKeys";
import { LoginPage } from "../pages/LoginPage";

const navigation = [
  { to: "/", label: "书库", icon: LibraryBig, end: true },
  { to: "/settings", label: "设置", icon: Settings2, end: false },
];

export function AppShell() {
  const location = useLocation();
  const queryClient = useQueryClient();
  const isWorkbench = location.pathname.startsWith("/projects/");
  const sessionQuery = useQuery({
    queryKey: queryKeys.authSession,
    queryFn: api.authSession,
    staleTime: 30_000,
  });
  const logout = useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.authSession });
      void queryClient.removeQueries({ queryKey: queryKeys.projects });
    },
  });

  if (sessionQuery.isLoading) {
    return (
      <div className="grid min-h-screen place-items-center">
        <span className="size-8 animate-spin rounded-full border-2 border-ink-200 border-t-cinnabar-700 dark:border-ink-700 dark:border-t-cinnabar-400" />
      </div>
    );
  }

  if (!sessionQuery.data?.authenticated) {
    return (
      <LoginPage
        onAuthenticated={() => {
          void queryClient.invalidateQueries({ queryKey: queryKeys.authSession });
          void queryClient.invalidateQueries({ queryKey: queryKeys.projects });
        }}
      />
    );
  }

  return (
    <div className="min-h-screen">
      <a
        href="#main-content"
        className="fixed left-3 top-3 z-[200] -translate-y-20 rounded-lg bg-ink-950 px-4 py-2 text-sm text-white transition focus:translate-y-0"
      >
        跳至主要内容
      </a>
      <header className="sticky top-0 z-40 border-b border-ink-200/80 bg-paper/90 backdrop-blur-xl dark:border-ink-800 dark:bg-ink-950/90">
        <div
          className={`mx-auto flex h-16 items-center justify-between px-4 sm:px-6 ${
            isWorkbench ? "max-w-[1920px]" : "max-w-7xl"
          }`}
        >
          <NavLink to="/" className="group flex items-center gap-3 rounded-lg">
            <span className="grid size-9 place-items-center rounded-xl bg-cinnabar-700 text-white shadow-sm transition group-hover:-rotate-2 dark:bg-cinnabar-600">
              <BookOpenText className="size-[19px]" strokeWidth={1.8} />
            </span>
            <span>
              <span className="block font-serif text-lg font-semibold leading-5 tracking-wide text-ink-950 dark:text-white">
                砚译
              </span>
              <span className="hidden text-[10px] tracking-[0.18em] text-ink-500 sm:block">
                BOOK TRANSLATION STUDIO
              </span>
            </span>
          </NavLink>

          <nav aria-label="主要导航" className="flex items-center gap-1">
            {navigation.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  `flex min-h-10 items-center gap-2 rounded-lg px-3 text-sm font-medium transition ${
                    isActive
                      ? "bg-ink-900 text-white dark:bg-ink-100 dark:text-ink-950"
                      : "text-ink-600 hover:bg-ink-100 hover:text-ink-950 dark:text-ink-300 dark:hover:bg-ink-800 dark:hover:text-white"
                  }`
                }
              >
                <Icon className="size-4" />
                <span>{label}</span>
              </NavLink>
            ))}
            <button
              type="button"
              className="icon-btn ml-1"
              onClick={() => logout.mutate()}
              disabled={logout.isPending}
              aria-label="退出登录"
              title="退出登录"
            >
              <LogOut className="size-4" />
            </button>
          </nav>
        </div>
      </header>
      <main id="main-content">
        <Outlet />
      </main>
    </div>
  );
}
