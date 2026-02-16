"use client";

import { useState } from "react";

interface CollapsibleOutputProps {
  label?: string;
  output: string;
  defaultOpen?: boolean;
  maxHeight?: string;
}

export default function CollapsibleOutput({
  label = "Raw Output",
  output,
  defaultOpen = false,
  maxHeight = "400px",
}: CollapsibleOutputProps) {
  const [open, setOpen] = useState(defaultOpen);

  if (!output) return null;

  return (
    <div className="mt-3">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-xs text-white/50 hover:text-white/90 transition-colors"
      >
        <svg
          className={`w-3 h-3 transition-transform duration-200 ${
            open ? "rotate-90" : ""
          }`}
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
        {label}
      </button>
      {open && (
        <div className="output-block mt-2" style={{ maxHeight }}>
          {output}
        </div>
      )}
    </div>
  );
}
