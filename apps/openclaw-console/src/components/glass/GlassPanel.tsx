"use client";

interface GlassPanelProps {
  children: React.ReactNode;
  className?: string;
  strong?: boolean;
}

export default function GlassPanel({
  children,
  className = "",
  strong = false,
}: GlassPanelProps) {
  return (
    <div
      className={`${
        strong ? "glass-surface-strong" : "glass-surface"
      } rounded-2xl overflow-hidden ${className}`}
    >
      {children}
    </div>
  );
}
