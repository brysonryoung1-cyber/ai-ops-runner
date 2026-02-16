"use client";

interface GlassCardProps {
  children: React.ReactNode;
  className?: string;
}

export default function GlassCard({ children, className = "" }: GlassCardProps) {
  return (
    <div
      className={`glass-surface rounded-2xl overflow-hidden ${className}`}
    >
      {children}
    </div>
  );
}
