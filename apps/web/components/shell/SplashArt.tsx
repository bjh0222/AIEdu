"use client";

import Image from "next/image";
import styles from "./SplashArt.module.css";
import type { Variant } from "./Splash";

export default function SplashArt({ variant }: { variant: Variant | null }) {
  if (variant === null) return null;

  return (
    <div className={styles.canvas} data-variant={variant}>
      <Image
        className={styles.image}
        src={`/splash/${variant}.webp`}
        alt=""
        fill
        priority
        sizes="100vw"
        unoptimized
      />
      <div className={styles.haze} aria-hidden="true" />
    </div>
  );
}
