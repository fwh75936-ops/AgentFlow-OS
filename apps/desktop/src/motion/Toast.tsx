import { AnimatePresence, m } from "motion/react";
import { create } from "zustand";
import { TRANSITION } from "./tokens";

/**
 * Minimal in-house toast (stage 3 decision: the plan allowed sonner as a
 * candidate OR a small self-built toast; this ~60-line implementation
 * covers the app's actual needs — transient success/error feedback — with
 * zero extra dependency and full AgentFlow Pure Light styling).
 *
 * Toasts are feedback for real, already-completed actions only; they are
 * never used to simulate progress.
 */

export interface ToastItem {
  id: number;
  kind: "success" | "error" | "info";
  text: string;
}

interface ToastState {
  items: ToastItem[];
  push: (kind: ToastItem["kind"], text: string) => void;
  dismiss: (id: number) => void;
}

let seq = 1;

const useToastStore = create<ToastState>((set) => ({
  items: [],
  push: (kind, text) => {
    const id = seq++;
    set((s) => ({ items: [...s.items.slice(-3), { id, kind, text }] }));
    window.setTimeout(() => {
      set((s) => ({ items: s.items.filter((t) => t.id !== id) }));
    }, 4200);
  },
  dismiss: (id) => set((s) => ({ items: s.items.filter((t) => t.id !== id) })),
}));

export function showToast(kind: ToastItem["kind"], text: string) {
  useToastStore.getState().push(kind, text);
}

const KIND_CLASS: Record<ToastItem["kind"], string> = {
  success: "toast-success",
  error: "toast-error",
  info: "toast-info",
};

export function ToastViewport() {
  const items = useToastStore((s) => s.items);
  const dismiss = useToastStore((s) => s.dismiss);
  return (
    <div className="toast-viewport" aria-live="polite">
      <AnimatePresence>
        {items.map((t) => (
          <m.button
            key={t.id}
            type="button"
            className={`toast ${KIND_CLASS[t.kind]}`}
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, transition: TRANSITION.exit }}
            transition={TRANSITION.menuEnter}
            onClick={() => dismiss(t.id)}
            title="点击关闭"
          >
            {t.text}
          </m.button>
        ))}
      </AnimatePresence>
    </div>
  );
}
