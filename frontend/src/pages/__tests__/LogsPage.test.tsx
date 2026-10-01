import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { RuntimeLog } from "../../api/types";
import { LogsPage } from "../LogsPage";

const mocks = vi.hoisted(() => ({ project: vi.fn(), logs: vi.fn(), clear: vi.fn(), notify: vi.fn() }));
vi.mock("../../api/client", () => ({
  api: { project: mocks.project, runtimeLogs: mocks.logs, deleteRuntimeLogs: mocks.clear },
  errorMessage: () => "请求失败",
}));
vi.mock("../../store/toast", () => ({ useToast: () => ({ notify: mocks.notify }) }));

function entry(id: number): RuntimeLog {
  return { id, project_id: 1, job_id: null, segment_id: null, chapter_id: null, level: "info",
    event_type: "provider.responded", message: `log-${id}`, details_json: {}, created_at: "2026-08-01T00:00:00Z" };
}
function showPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/projects/1/logs"]}>
        <Routes><Route path="/projects/:projectId/logs" element={<LogsPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}
beforeEach(() => {
  vi.resetAllMocks();
  mocks.project.mockResolvedValue({ title: "Test" });
  mocks.logs.mockImplementation(async (_id, options) => options.page === 2
    ? { items: [entry(3), entry(2), entry(1)], total: 102, has_more: false }
    : { items: Array.from({ length: 100 }, (_, i) => entry(102 - i)), total: 102, has_more: true });
  mocks.clear.mockResolvedValue(undefined);
});

describe("runtime log page", () => {
  it("deduplicates overlapping pages while retaining older logs", async () => {
    const user = showPage();
    await user.click(await screen.findByRole("button", { name: "加载更多" }));
    expect(await screen.findByText("log-1")).toBeInTheDocument();
    expect(screen.getAllByText("log-3")).toHaveLength(1);
  });
  it("removes already loaded history when clearing logs", async () => {
    const user = showPage();
    await user.selectOptions(screen.getByLabelText("日志级别"), "error");
    await user.click(await screen.findByRole("button", { name: "加载更多" }));
    await screen.findByText("log-1");
    await user.click(screen.getByRole("button", { name: "清空日志" }));
    mocks.logs.mockResolvedValue({ items: [], total: 0, has_more: false });
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "清空" }));
    await waitFor(() => expect(screen.queryByText("log-1")).not.toBeInTheDocument());
    expect(await screen.findByText("暂无运行日志")).toBeInTheDocument();
  });
  it("passes level and event filters to the API", async () => {
    const user = showPage();
    await screen.findByText("log-102");
    await user.selectOptions(screen.getByLabelText("日志级别"), "error");
    await user.type(screen.getByLabelText("事件类型过滤"), "segment.failed");
    await waitFor(() => expect(mocks.logs).toHaveBeenCalledWith(1,
      expect.objectContaining({ level: "error", eventType: "segment.failed" })));
  });
});
