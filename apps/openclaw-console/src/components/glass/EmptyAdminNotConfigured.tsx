"use client";

export default function EmptyAdminNotConfigured() {
  return (
    <div className="glass-surface rounded-2xl p-8 text-center border border-amber-500/20">
      <p className="text-sm text-white/80">
        Admin not configured. Deploy+Verify and other privileged actions are unavailable.
      </p>
      <p className="text-xs text-white/50 mt-2">
        No additional guidance is provided for security reasons.
      </p>
    </div>
  );
}
