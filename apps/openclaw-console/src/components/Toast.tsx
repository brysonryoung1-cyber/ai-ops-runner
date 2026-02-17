"use client";

import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from "react";

export interface ToastData {
  id: string;
  message: string;
  variant: "success" | "error" | "info";
  detail?: string;
}

interface ToastContextType {
  toasts: ToastData[];
  addToast: (toast: Omit<ToastData, "id">) => void;
  removeToast: (id: string) => void;
}

const ToastContext = createContext<ToastContextType>({
  toasts: [],
  addToast: () => {},
  removeToast: () => {},
});

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastData[]>([]);

  const addToast = useCallback((toast: Omit<ToastData, "id">) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
    setToasts((prev) => [...prev, { ...toast, id }]);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ toasts, addToast, removeToast }}>
      {children}
      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </ToastContext.Provider>
  );
}

function ToastContainer({
  toasts,
  removeToast,
}: {
  toasts: ToastData[];
  removeToast: (id: string) => void;
}) {
  if (toasts.length === 0) return null;
  return (
    <div
      aria-live="polite"
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm"
    >
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={() => removeToast(toast.id)} />
      ))}
    </div>
  );
}

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: ToastData;
  onDismiss: () => void;
}) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 6000);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  const borderColor =
    toast.variant === "success"
      ? "border-emerald-500/30"
      : toast.variant === "error"
        ? "border-red-500/30"
        : "border-blue-500/30";

  const iconColor =
    toast.variant === "success"
      ? "text-emerald-400"
      : toast.variant === "error"
        ? "text-red-400"
        : "text-blue-400";

  return (
    <div
      role="alert"
      className={`glass-surface rounded-xl p-3 border ${borderColor} shadow-lg animate-slide-in-right`}
    >
      <div className="flex items-start gap-2">
        <span className={`text-sm flex-shrink-0 ${iconColor}`}>
          {toast.variant === "success" ? "✓" : toast.variant === "error" ? "✕" : "ℹ"}
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-xs text-white/90">{toast.message}</p>
          {toast.detail && (
            <p className="text-[10px] text-white/50 mt-0.5 font-mono truncate">
              {toast.detail}
            </p>
          )}
        </div>
        <button
          onClick={onDismiss}
          className="text-white/40 hover:text-white/70 text-xs flex-shrink-0"
          aria-label="Dismiss"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
