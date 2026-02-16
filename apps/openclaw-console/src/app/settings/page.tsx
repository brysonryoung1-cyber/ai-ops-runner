"use client";

export default function SettingsPage() {
  return (
    <div>
      <div className="mb-8">
        <h2 className="text-2xl font-bold text-white/95 tracking-tight">
          Settings
        </h2>
        <p className="text-sm text-white/60 mt-1">
          OpenClaw HQ configuration
        </p>
      </div>
      <div className="glass-surface rounded-2xl p-6">
        <p className="text-sm text-white/70">
          HQ binds to 127.0.0.1 only. Authentication uses X-OpenClaw-Token when configured. Admin actions require OPENCLAW_ADMIN_TOKEN.
        </p>
      </div>
    </div>
  );
}
