import { FormEvent, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { BookOpenText, LockKeyhole, LogIn } from "lucide-react";
import { api, errorMessage } from "../api/client";

export function LoginPage({ onAuthenticated }: { onAuthenticated: () => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");

  const login = useMutation({
    mutationFn: () => api.login({ username: username.trim(), password }),
    onSuccess: () => onAuthenticated(),
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password) return;
    login.mutate();
  };

  return (
    <main className="grid min-h-screen place-items-center px-4 py-10">
      <section className="w-full max-w-sm">
        <div className="mb-7 flex items-center gap-3">
          <span className="grid size-10 place-items-center rounded-xl bg-cinnabar-700 text-white shadow-sm dark:bg-cinnabar-600">
            <BookOpenText className="size-5" strokeWidth={1.8} />
          </span>
          <div>
            <h1 className="font-serif text-2xl font-semibold text-ink-950 dark:text-white">
              砚译
            </h1>
            <p className="text-xs uppercase tracking-[0.18em] text-ink-500">
              BOOK TRANSLATION STUDIO
            </p>
          </div>
        </div>

        <form className="surface rounded-2xl p-5" onSubmit={submit}>
          <div className="mb-5 flex items-center gap-2 text-ink-700 dark:text-ink-200">
            <LockKeyhole className="size-4" />
            <h2 className="text-sm font-semibold">管理员登录</h2>
          </div>

          <label className="field-label" htmlFor="login-username">
            用户名
          </label>
          <input
            id="login-username"
            className="field"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />

          <label className="field-label mt-4" htmlFor="login-password">
            密码
          </label>
          <input
            id="login-password"
            className="field"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoFocus
          />

          {login.isError ? (
            <p className="mt-4 rounded-lg bg-cinnabar-50 px-3 py-2 text-sm text-cinnabar-700 dark:bg-cinnabar-950/40 dark:text-cinnabar-200">
              {errorMessage(login.error)}
            </p>
          ) : null}

          <button
            type="submit"
            className="btn-primary mt-5 w-full justify-center"
            disabled={!username.trim() || !password || login.isPending}
          >
            {login.isPending ? (
              <span className="size-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
            ) : (
              <LogIn className="size-4" />
            )}
            登录
          </button>
        </form>
      </section>
    </main>
  );
}
