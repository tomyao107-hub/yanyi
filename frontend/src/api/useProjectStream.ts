import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { InfiniteData } from "@tanstack/react-query";
import type { SegmentPage, StreamEvent } from "./types";
import { queryKeys } from "./queryKeys";

export type StreamConnection = "idle" | "connecting" | "open" | "retrying";

function parseEvent(raw: string, fallbackType?: string): StreamEvent | null {
  try {
    const payload = JSON.parse(raw) as StreamEvent;
    const message =
      typeof payload.message === "string"
        ? payload.message
        : typeof payload.error === "string"
          ? payload.error
          : undefined;
    return {
      ...payload,
      type: payload.type ?? fallbackType ?? "message",
      ...(message ? { message } : {}),
    };
  } catch {
    if (!raw) return null;
    return { type: fallbackType ?? "message", message: raw };
  }
}

export function useProjectStream(
  projectId: number,
  enabled: boolean,
  onEvent?: (event: StreamEvent) => void,
): StreamConnection {
  const queryClient = useQueryClient();
  const [connection, setConnection] = useState<StreamConnection>("idle");
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!enabled || !Number.isFinite(projectId)) {
      setConnection("idle");
      return;
    }

    setConnection("connecting");
    const source = new EventSource(`/api/projects/${projectId}/stream`);

    const invalidateFilteredSegments = () =>
      queryClient.invalidateQueries({
        predicate: (query) => {
          const key = query.queryKey;
          return (
            key[0] === "segments" &&
            key[1] === projectId &&
            key[3] !== undefined &&
            key[3] !== "all"
          );
        },
        refetchType: "active",
      });

    const handle = (event: MessageEvent<string>, fallbackType?: string) => {
      const data = parseEvent(event.data, fallbackType);
      if (!data) return;
      onEventRef.current?.(data);

      if (data.type === "segment_done" || data.type === "segment_updated") {
        const segmentId = Number(data.segment_id);
        queryClient.setQueriesData<InfiniteData<SegmentPage>>(
          { queryKey: queryKeys.allSegments(projectId) },
          (previous) => {
            if (!previous || !Number.isFinite(segmentId)) return previous;
            return {
              ...previous,
              pages: previous.pages.map((page) => ({
                ...page,
                items: page.items.map((segment) =>
                  segment.id === segmentId
                    ? {
                        ...segment,
                        target_text: String(data.target_text ?? data.target ?? segment.target_text ?? ""),
                        status: data.status ?? "done",
                        error_msg: null,
                      }
                    : segment,
                ),
              })),
            };
          },
        );
        void invalidateFilteredSegments();
      }
      if (data.type === "segment_delta") {
        const segmentId = Number(data.segment_id);
        queryClient.setQueriesData<InfiniteData<SegmentPage>>(
          { queryKey: queryKeys.allSegments(projectId) },
          (previous) => {
            if (!previous || !Number.isFinite(segmentId)) return previous;
            return {
              ...previous,
              pages: previous.pages.map((page) => ({
                ...page,
                items: page.items.map((segment) => {
                  if (segment.id !== segmentId) return segment;
                  const targetText =
                    data.target_text !== undefined
                      ? String(data.target_text)
                      : `${segment.target_text ?? ""}${String(data.delta ?? data.text ?? "")}`;
                  return {
                    ...segment,
                    target_text: targetText,
                    status: "processing",
                    error_msg: null,
                  };
                }),
              })),
            };
          },
        );
      }
      if (data.type === "segment_reset") {
        const segmentId = Number(data.segment_id);
        queryClient.setQueriesData<InfiniteData<SegmentPage>>(
          { queryKey: queryKeys.allSegments(projectId) },
          (previous) => {
            if (!previous || !Number.isFinite(segmentId)) return previous;
            return {
              ...previous,
              pages: previous.pages.map((page) => ({
                ...page,
                items: page.items.map((segment) =>
                  segment.id === segmentId
                    ? {
                        ...segment,
                        target_text: "",
                        status: "processing",
                        error_msg: null,
                      }
                    : segment,
                ),
              })),
            };
          },
        );
        void invalidateFilteredSegments();
      }

      if (
        [
          "progress",
          "segment_done",
          "segment_delta",
          "segment_reset",
          "segment_updated",
          "segment_queued",
          "segments_bulk_updated",
          "chapter_summary",
          "completed",
          "stopped",
          "parsed",
          "project_deleted",
          "error",
        ].includes(data.type)
      ) {
        const refetchType = data.type === "segment_delta" ? "none" : "active";
        void queryClient.invalidateQueries({
          queryKey: queryKeys.project(projectId),
          refetchType,
        });
        void queryClient.invalidateQueries({
          queryKey: queryKeys.projects,
          refetchType,
        });
      }
      if (
        data.type === "error" ||
        data.type === "progress" ||
        data.type === "completed" ||
        data.type === "stopped" ||
        data.type === "segments_bulk_updated"
      ) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.allSegments(projectId) });
      }
      if (data.type === "segment_done") {
        void queryClient.invalidateQueries({ queryKey: queryKeys.tmStats(projectId) });
        void queryClient.invalidateQueries({
          queryKey: queryKeys.qa(projectId),
          refetchType: "none",
        });
      }
      if (data.type === "error" && data.segment_id !== undefined) {
        void queryClient.invalidateQueries({
          queryKey: queryKeys.qa(projectId),
          refetchType: "none",
        });
      }
      if (
        data.type === "completed" ||
        data.type === "stopped" ||
        data.type === "segments_bulk_updated" ||
        (data.type === "error" && data.segment_id === undefined) ||
        (data.type === "progress" && data.running === false)
      ) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.qa(projectId) });
        void queryClient.invalidateQueries({ queryKey: queryKeys.tmStats(projectId) });
      }
    };

    const listeners: Array<[string, EventListener]> = [];
    [
      "progress",
      "segment_done",
      "segment_delta",
      "segment_reset",
      "segment_updated",
      "segment_queued",
      "segments_bulk_updated",
      "chapter_summary",
      "completed",
      "stopped",
      "parsed",
      "project_deleted",
      "error",
    ].forEach((type) => {
      const listener = ((event: MessageEvent<string>) => handle(event, type)) as EventListener;
      source.addEventListener(type, listener);
      listeners.push([type, listener]);
    });

    source.onmessage = (event) => handle(event);
    source.onopen = () => setConnection("open");
    source.onerror = (event) => {
      // A server-sent `event: error` is a MessageEvent and is handled above.
      // Only transport-level EventSource failures should show "reconnecting".
      if (event instanceof MessageEvent && typeof event.data === "string") return;
      setConnection("retrying");
    };

    return () => {
      listeners.forEach(([type, listener]) => source.removeEventListener(type, listener));
      source.close();
      setConnection("idle");
    };
  }, [enabled, projectId, queryClient]);

  return connection;
}
