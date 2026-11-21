"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

const Ctx = createContext<(message: string) => void>(() => {});
export const useToast = () => useContext(Ctx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<{ id: number; message: string }[]>([]);
  const push = useCallback((message: string) => {
    const id = Date.now() + Math.random();
    setItems((list) => [...list, { id, message }]);
    setTimeout(() => setItems((list) => list.filter((i) => i.id !== id)), 4200);
  }, []);
  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="toast-zone" role="status" aria-live="polite">
        {items.map((i) => (
          <div key={i.id} className="toast">{i.message}</div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
