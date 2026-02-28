"use client";

type PolicyTier = "readonly" | "low_risk_ops" | "privileged_ops" | "destructive_ops";

interface ActionButtonProps {
  label: string;
  description: string;
  onClick: () => void;
  loading?: boolean;
  variant?: "primary" | "secondary" | "danger";
  disabled?: boolean;
  tier?: PolicyTier;
  requiresApproval?: boolean;
}

const VARIANT_STYLES = {
  primary: "bg-white/20 text-white hover:bg-white/30 border-white/20 backdrop-blur-md",
  secondary: "bg-white/10 text-white/90 border-white/10 hover:bg-white/15 backdrop-blur-md",
  danger: "bg-red-500/20 text-red-200 hover:bg-red-500/30 border-red-500/30 backdrop-blur-md",
};

const TIER_BADGE: Record<PolicyTier, { label: string; className: string } | null> = {
  readonly: null,
  low_risk_ops: null,
  privileged_ops: {
    label: "privileged_ops via rootd",
    className: "bg-amber-500/20 text-amber-200 border-amber-500/30",
  },
  destructive_ops: {
    label: "destructive_ops — approval required",
    className: "bg-red-500/20 text-red-200 border-red-500/30",
  },
};

export default function ActionButton({
  label,
  description,
  onClick,
  loading = false,
  variant = "secondary",
  disabled = false,
  tier,
  requiresApproval = false,
}: ActionButtonProps) {
  const badge = tier ? TIER_BADGE[tier] : null;

  return (
    <button
      onClick={onClick}
      disabled={loading || disabled}
      className={`w-full text-left rounded-2xl p-4 transition-all duration-150 border
        ${VARIANT_STYLES[variant]}
        ${loading || disabled ? "opacity-60 cursor-not-allowed" : "cursor-pointer"}
      `}
    >
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <p className="text-sm font-semibold">{label}</p>
            {badge && (
              <span className={`text-[10px] px-1.5 py-0.5 rounded border ${badge.className}`}>
                {badge.label}
              </span>
            )}
          </div>
          <p className="text-xs mt-0.5 text-white/60">
            {description}
          </p>
        </div>
        {loading ? (
          <svg
            className="w-5 h-5 animate-spin"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
            />
          </svg>
        ) : (
          <svg
            className="w-4 h-4 opacity-40"
            fill="none"
            viewBox="0 0 24 24"
            strokeWidth={2}
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M8.25 4.5l7.5 7.5-7.5 7.5"
            />
          </svg>
        )}
      </div>
    </button>
  );
}
