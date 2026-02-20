# Third-Party Code and Runtime Dependencies

This document notes third-party code used by ai-ops-runner (runtime fetch or optional use). No unlicensed code is vendored without notice.

## microgpt (Karpathy)

- **Use**: Offline canary job `llm.microgpt.canary` (test_runner).
- **Source**: [GitHub Gist](https://gist.github.com/karpathy/8627fe009c40f57531cb18360106ce95) — single-file pure Python GPT training/inference.
- **License**: See the gist; typically MIT for Karpathy’s public code.
- **Integration**: We do **not** vendor the file. At runtime the canary script fetches the raw file from the pinned gist URL, verifies a hardcoded SHA256, applies a minimal local patch (reduced steps/samples) to a temp copy only, and runs it. Cached copy may be stored under `artifacts/cache/microgpt_canary/` with SHA verification for offline runs.
