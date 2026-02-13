"use client";

interface ActionButtonProps {
  label: string;
  description: string;
  onClick: () => void;
  loading?: boolean;
  variant?: "primary" | "secondary" | "danger";
  disabled?: boolean;
}

const VARIANT_STYLES = {
  primary:
    "bg-apple-blue text-white hover:bg-blue-700 active:bg-blue-800",
  secondary:
    "bg-apple-card text-apple-text border border-apple-border hover:bg-gray-50 active:bg-gray-100",
  danger:
    "bg-apple-red text-white hover:bg-red-700 active:bg-red-800",
};

export default function ActionButton({
  label,
  description,
  onClick,
  loading = false,
  variant = "secondary",
  disabled = false,
}: ActionButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={loading || disabled}
      className={`w-full text-left rounded-apple p-4 transition-all duration-150 shadow-apple
        ${VARIANT_STYLES[variant]}
        ${loading || disabled ? "opacity-60 cursor-not-allowed" : "cursor-pointer"}
      `}
    >
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm font-semibold">{label}</p>
          <p
            className={`text-xs mt-0.5 ${
              variant === "secondary" ? "text-apple-muted" : "text-white/70"
            }`}
          >
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
