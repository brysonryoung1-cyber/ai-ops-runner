"use client";

interface GlassButtonProps {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md";
  type?: "button" | "submit";
  className?: string;
}

const VARIANT_STYLES = {
  primary:
    "bg-white/20 hover:bg-white/30 text-white border-white/20 backdrop-blur-md",
  secondary:
    "bg-white/10 hover:bg-white/15 text-white/90 border-white/10 backdrop-blur-md",
  danger:
    "bg-red-500/20 hover:bg-red-500/30 text-red-200 border-red-400/20 backdrop-blur-md",
  ghost:
    "bg-transparent hover:bg-white/10 text-white/80 border-white/5",
};

export default function GlassButton({
  children,
  onClick,
  disabled = false,
  variant = "secondary",
  size = "md",
  type = "button",
  className = "",
}: GlassButtonProps) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center rounded-xl border font-medium transition-all duration-150 focus-ring disabled:opacity-50 disabled:cursor-not-allowed
        ${VARIANT_STYLES[variant]}
        ${size === "sm" ? "px-3 py-1.5 text-xs" : "px-4 py-2 text-sm"}
        ${className}`}
    >
      {children}
    </button>
  );
}
