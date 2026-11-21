"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, isRun, type Snapshot } from "@/lib/v2/api";
import { ApiError, explain } from "@/lib/v2/client";

export type SnapState = {
  snapshot: Snapshot | null;
  error: string;
  stale: boolean;
  loading: boolean;
  /** 立即重新读取（忽略 ETag），并恢复轮询。 */
  refresh: () => Promise<void>;
  /** 让轮询跟随某个 run（续跑后切到新 run）；null 表示该任务的最新 run。 */
  followRun: (runId: string | null) => void;
};

const ACTIVE = new Set(["queued", "running"]);

/**
 * 读 snapshot：ETag/304、指数退避（失败不清空已显示内容）、页面不可见时暂停、读到终态即停。
 * 只发 GET：刷新页面不会续跑，也不会发任何 POST。
 * 所有可变轮询状态放在一个 ref 对象里，只在事件/副作用中读写；渲染只读 state。
 */
export function useSnapshot(taskId: string): SnapState {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const [loading, setLoading] = useState(true);
  const poller = useRef({
    etag: null as string | null,
    run: null as string | null,
    failures: 0,
    timer: null as ReturnType<typeof setTimeout> | null,
    alive: false,
    ctl: null as AbortController | null,
    last: null as Snapshot | null,
    generation: 0,
    tick: null as ((generation: number) => Promise<void>) | null,
  });

  const poll = useCallback(async (generation: number): Promise<void> => {
    const p = poller.current;
    p.timer = null;
    if (!p.alive || generation !== p.generation) return;
    if (document.visibilityState === "visible") {
      p.ctl?.abort();
      const ctl = new AbortController();
      p.ctl = ctl;
      try {
        const res = await api.snapshot(taskId, p.etag, p.run, ctl.signal);
        if (!p.alive || generation !== p.generation) return;
        p.failures = 0;
        setError("");
        setStale(false);
        if (res.data) {
          p.etag = res.etag;
          p.last = res.data;
          setSnapshot(res.data);
        }
      } catch (e) {
        if (!p.alive || generation !== p.generation || (e instanceof DOMException && e.name === "AbortError")) return;
        p.failures += 1;
        setError(explain(e));
        setStale(true); // 保留上次内容，标注“可能已过期”
        if (e instanceof ApiError && (e.status === 404 || e.status === 403)) p.failures = 99; // 不可恢复，停止
      }
      setLoading(false);
    }
    if (!p.alive || generation !== p.generation || p.failures >= 99) return;
    const active = !p.last || (isRun(p.last) && ACTIVE.has(p.last.state));
    // 运行中 1.5s；失败后 1.5s·2^n 指数退避，最长 30s；终态不再轮询。
    const delay = p.failures > 0 ? Math.min(30_000, 1500 * 2 ** p.failures) : active ? 1500 : 0;
    if (delay > 0) p.timer = setTimeout(() => void p.tick?.(generation), delay);
  }, [taskId]);

  const restart = useCallback(() => {
    const p = poller.current;
    p.tick = poll;
    if (p.timer) clearTimeout(p.timer);
    p.generation += 1;
    void poll(p.generation);
  }, [poll]);

  useEffect(() => {
    const p = poller.current;
    p.alive = true;
    restart();
    const vis = () => {
      if (document.visibilityState === "visible" && !p.timer) restart();
    };
    document.addEventListener("visibilitychange", vis);
    return () => {
      p.alive = false;
      if (p.timer) clearTimeout(p.timer);
      p.ctl?.abort();
      document.removeEventListener("visibilitychange", vis);
    };
  }, [restart]);

  const refresh = useCallback(async () => {
    const p = poller.current;
    p.etag = null;
    p.failures = 0;
    restart();
  }, [restart]);

  const followRun = useCallback((runId: string | null) => {
    const p = poller.current;
    p.run = runId;
    p.etag = null;
    p.failures = 0;
    p.last = null;
    restart();
  }, [restart]);

  return { snapshot, error, stale, loading, refresh, followRun };
}
