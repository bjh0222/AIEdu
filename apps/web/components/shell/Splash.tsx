"use client";

import Image from "next/image";
import { useCallback, useEffect, useRef, useState } from "react";
import styles from "./Splash.module.css";

const PLAYED_KEY = "aiedu.splash.played.v1";
const INDEX_KEY = "aiedu.splash.index.v1";
const VARIANTS = ["sky", "green", "distance"] as const;
const COPY_LINES: Record<(typeof VARIANTS)[number], string[]> = {
  sky: ["愿你有天蓝可往", "有皓白可依"],
  green: ["愿你踏遍山川", "愿你满怀青绿"],
  distance: ["愿你始终自由", "始终美好", "始终奔赴热爱"],
};
type Variant = (typeof VARIANTS)[number];
type Phase = "pending" | "playing" | "closing" | "done";

declare global {
  interface Window {
    __AIEDU_SPLASH_PLAYED__?: boolean;
  }
}

// sessionStorage 不可用时，只保页面内内存标记；宁可漏播，也不在后台恢复时重播。
let memoryPlayed = false;

// 在首帧前同步隐藏已播开屏，避免刷新时出现一段可用但不必展示的应用闪屏。
export const splashBootstrap = `(function(){try{if(sessionStorage.getItem("${PLAYED_KEY}")==="1"){document.documentElement.dataset.splash="hidden";window.__AIEDU_SPLASH_PLAYED__=true;}else{window.__AIEDU_SPLASH_PLAYED__=false;}}catch(e){window.__AIEDU_SPLASH_PLAYED__=false;}})();`

function readPlayedMark(): boolean {
  if (memoryPlayed) return true;
  try { return sessionStorage.getItem(PLAYED_KEY) === "1"; } catch { return false; }
}

function writePlayedMark() {
  memoryPlayed = true;
  try { sessionStorage.setItem(PLAYED_KEY, "1"); } catch { /* 页面内内存标记已兜底 */ }
}

function readRotationIndex(): number | null {
  const sources: Storage[] = [];
  try { sources.push(localStorage); } catch { /* 忽略不可用存储 */ }
  try { sources.push(sessionStorage); } catch { /* 忽略不可用存储 */ }
  for (const storage of sources) {
    try {
      const value = Number(storage.getItem(INDEX_KEY));
      if (Number.isInteger(value) && value >= 0 && value < VARIANTS.length) return value;
    } catch { /* 尝试下一个可用存储 */ }
  }
  return null;
}

function writeRotationIndex(index: number) {
  try { localStorage.setItem(INDEX_KEY, String(index)); return; } catch { /* 退回会话级记录 */ }
  try { sessionStorage.setItem(INDEX_KEY, String(index)); } catch { /* 静态单页降级时不保留轮换 */ }
}

export default function Splash() {
  const [phase, setPhase] = useState<Phase>(() =>
    typeof window === "undefined" || !window.__AIEDU_SPLASH_PLAYED__ ? "pending" : "done",
  );
  const [variant, setVariant] = useState<Variant | null>(null);
  const closeTimerRef = useRef<number | null>(null);
  const doneTimerRef = useRef<number | null>(null);
  const startedAtRef = useRef(0);

  const clearCloseTimer = useCallback(() => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  const close = useCallback(() => {
    clearCloseTimer();
    if (doneTimerRef.current !== null) window.clearTimeout(doneTimerRef.current);
    setPhase((current) => (current === "playing" ? "closing" : current));
    doneTimerRef.current = window.setTimeout(() => setPhase("done"), 300);
  }, [clearCloseTimer]);

  const scheduleClose = useCallback((delay: number) => {
    clearCloseTimer();
    closeTimerRef.current = window.setTimeout(close, delay);
  }, [clearCloseTimer, close]);

  const start = useCallback(() => {
    if (phase !== "pending" || readPlayedMark()) return;
    writePlayedMark();
    const currentIndex = readRotationIndex();
    const nextIndex = currentIndex === null ? 0 : (currentIndex + 1) % VARIANTS.length;
    const nextVariant = VARIANTS[nextIndex];
    setVariant(nextVariant);
    setPhase("playing");
    startedAtRef.current = Date.now();
    writeRotationIndex(nextIndex);
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    scheduleClose(reduceMotion ? 900 : 3050);
  }, [phase, scheduleClose]);

  const skip = useCallback(() => {
    if (phase !== "playing" || Date.now() - startedAtRef.current < 400) return;
    close();
  }, [close, phase]);

  useEffect(() => {
    if (readPlayedMark()) return;
    let cancelled = false;
    let onVisible: () => void = () => undefined;
    const frame = requestAnimationFrame(() => {
      if (cancelled) return;
      if (document.visibilityState === "visible") {
        start();
        return;
      }
      onVisible = () => {
        if (document.visibilityState === "visible") start();
      };
      document.addEventListener("visibilitychange", onVisible, { once: true });
    });
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [start]);

  useEffect(() => {
    const onPageshow = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      writePlayedMark();
      clearCloseTimer();
      setPhase("done");
    };
    const onVisibility = () => {
      // 只补偿隐藏页造成的计时延迟；不重置标记、不重启动画、不轮换下一屏。
      if (document.visibilityState !== "visible" || phase !== "playing") return;
      const elapsed = Date.now() - startedAtRef.current;
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const total = reduceMotion ? 900 : 3050;
      scheduleClose(Math.max(0, total - elapsed));
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" || event.key === "Enter") skip();
    };
    window.addEventListener("pageshow", onPageshow);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pageshow", onPageshow);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("keydown", onKeyDown);
      clearCloseTimer();
    };
  }, [clearCloseTimer, phase, scheduleClose, skip]);

  useEffect(() => () => {
    if (doneTimerRef.current !== null) window.clearTimeout(doneTimerRef.current);
  }, []);

  if (phase === "done") return null;

  return (
    <section
      className={`${styles.splash} ${phase === "closing" ? styles.closing : ""}`}
      data-phase={phase}
      data-variant={variant}
      aria-label="AI Edu 开屏"
    >
      <button type="button" className={styles.skip} onClick={skip}>跳过开屏</button>

      <div className={styles.scene} aria-hidden="true">
        {variant === "sky" && (
          <div className={styles.sky}>
            <span className={`${styles.cloud} ${styles.cloudBack}`} />
            <span className={`${styles.cloud} ${styles.cloudMid}`} />
            <span className={`${styles.cloud} ${styles.cloudFront}`} />
          </div>
        )}
        {variant === "green" && (
          <div className={styles.green}>
            <span className={styles.ridgeFar} />
            <span className={styles.ridgeMid} />
            <span className={styles.ridgeNear} />
            <span className={styles.mist} />
          </div>
        )}
        {variant === "distance" && (
          <div className={styles.distance}>
            <span className={styles.lightPath} />
            <span className={styles.shore} />
          </div>
        )}
      </div>

      <p className={styles.copy}>
        {variant !== null && COPY_LINES[variant].map((line) => (
          <span key={line} className={styles.copyLine}>{line}</span>
        ))}
      </p>

      <div className={styles.brand}>
        <Image className={styles.logo} src="/ai-edu-mark.svg" alt="" width={84} height={84} unoptimized />
        <span className={styles.brandName}>AI Edu</span>
      </div>
    </section>
  );
}






