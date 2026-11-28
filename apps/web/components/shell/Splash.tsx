"use client";

import Image from "next/image";
import { useCallback, useEffect, useRef, useState, type AnimationEvent as ReactAnimationEvent } from "react";
import SplashArt from "./SplashArt";
import styles from "./Splash.module.css";

const PLAYED_KEY = "aiedu.splash.played.v1";
const INDEX_KEY = "aiedu.splash.index.v1";
const VARIANTS = ["sky", "green", "distance"] as const;
const COPY_LINES: Record<(typeof VARIANTS)[number], string[]> = {
  sky: ["愿你有天蓝可往", "有皓白可依"],
  green: ["愿你踏遍山川", "愿你满怀青绿"],
  distance: ["愿你始终自由", "始终美好", "始终奔赴热爱"],
};

export type Variant = (typeof VARIANTS)[number];
type Phase = "pending" | "playing" | "closing" | "done";

declare global {
  interface Window {
    __AIEDU_SPLASH_PLAYED__?: boolean;
  }
}

let memoryPlayed = false;

export const splashBootstrap = `(function(){try{if(sessionStorage.getItem("${PLAYED_KEY}")==="1"){document.documentElement.dataset.splash="hidden";window.__AIEDU_SPLASH_PLAYED__=true;}else{window.__AIEDU_SPLASH_PLAYED__=false;}}catch(e){window.__AIEDU_SPLASH_PLAYED__=false;}})();`;

function readPlayedMark(): boolean {
  if (memoryPlayed) return true;
  try { return sessionStorage.getItem(PLAYED_KEY) === "1"; } catch { return false; }
}

function writePlayedMark() {
  memoryPlayed = true;
  try { sessionStorage.setItem(PLAYED_KEY, "1"); } catch { /* 页面内存兜底 */ }
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
  try { sessionStorage.setItem(INDEX_KEY, String(index)); } catch { /* 单页降级时不保留轮换 */ }
}

export default function Splash() {
  const [phase, setPhase] = useState<Phase>(() =>
    typeof window === "undefined" || !window.__AIEDU_SPLASH_PLAYED__ ? "pending" : "done",
  );
  const [variant, setVariant] = useState<Variant | null>(null);
  const closeTimerRef = useRef<number | null>(null);
  const closeAtRef = useRef(0);
  const startedAtRef = useRef(0);

  const close = useCallback(() => setPhase((current) => (current === "playing" ? "closing" : current)), []);

  useEffect(() => {
    if (phase !== "playing") return;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const delay = reduceMotion ? 900 : 3050;
    closeAtRef.current = Date.now() + delay;
    closeTimerRef.current = window.setTimeout(close, delay);
    return () => {
      if (closeTimerRef.current !== null) {
        window.clearTimeout(closeTimerRef.current);
        closeTimerRef.current = null;
      }
    };
  }, [close, phase]);

  useEffect(() => {
    if (phase !== "closing") return;
    const timer = window.setTimeout(() => setPhase("done"), 300);
    return () => window.clearTimeout(timer);
  }, [phase]);

  useEffect(() => {
    let cancelled = false;
    let onVisible: () => void = () => undefined;

    function start() {
      if (cancelled || readPlayedMark()) return;
      writePlayedMark();
      const currentIndex = readRotationIndex();
      const nextIndex = currentIndex === null ? 0 : (currentIndex + 1) % VARIANTS.length;
      startedAtRef.current = Date.now();
      setVariant(VARIANTS[nextIndex]);
      setPhase("playing");
      writeRotationIndex(nextIndex);
    }

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
  }, []);

  useEffect(() => {
    const onPageshow = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      writePlayedMark();
      setPhase("done");
    };
    const onVisibility = () => {
      if (document.visibilityState !== "visible" || phase !== "playing") return;
      const remaining = Math.max(0, closeAtRef.current - Date.now());
      if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = window.setTimeout(close, remaining);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape" && event.key !== "Enter") return;
      if (phase !== "playing" || Date.now() - startedAtRef.current < 400) return;
      close();
    };
    window.addEventListener("pageshow", onPageshow);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("pageshow", onPageshow);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [close, phase]);

  const onAnimationEnd = useCallback((event: ReactAnimationEvent<HTMLElement>) => {
    if (phase === "closing" && event.animationName.includes("splashExit")) setPhase("done");
  }, [phase]);

  const skip = useCallback(() => {
    if (phase !== "playing" || Date.now() - startedAtRef.current < 400) return;
    close();
  }, [close, phase]);

  if (phase === "done") return null;

  return (
    <section
      className={`${styles.splash} ${phase === "closing" ? styles.closing : ""}`}
      onAnimationEnd={onAnimationEnd}
      data-phase={phase}
      data-variant={variant}
      aria-label="AI Edu 开屏"
    >
      <button type="button" className={styles.skip} onClick={skip}>跳过开屏</button>
      <div className={styles.scene} aria-hidden="true"><SplashArt variant={variant} /></div>
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
