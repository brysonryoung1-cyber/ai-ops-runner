# OpenClaw Roadmap

## Soma-first policy (code-level)

- ORB backtest execution is **locked** until Soma Phase 0 baseline PASS.
- Unlock: set `gates.allow_orb_backtests=true` in `config/project_state.json` after `projects.soma_kajabi.phase0_baseline_status=PASS`.
- HQ backtest actions show lock banner and link to baseline artifact dir.

## Phases

- **Phase 0 (Zane read-only)**  
  - Kajabi snapshot (read-only)  
  - Gmail harvest  
  - Manifest + `mirror_plan` artifacts + BASELINE_OK.json  
  - Phase 0 inventory permitted even when kill_switch=true  
  - No writes to external systems  

- **Phase 1 (Zane apply with gate)**  
  - `apply_mirror_plan` with approval gate  
  - Human-in-the-loop before any mirror writes  

- **Phase 2 (Zane scheduled + alerts)**  
  - Scheduled cadence for snapshots/harvest  
  - Alerts on drift or failures  

## Milestones

- [x] HQ + runner + doctor + guard + deploy pipeline  
- [x] Project Brain (canonical docs + state + HQ panel)  
- [x] Phase 0: soma_kajabi project + read-only actions + artifacts + baseline state  
- [x] Soma-first gate (orb.backtest.* locked until baseline PASS)  
- [x] API cost tracking (usage.jsonl, /api/costs/summary, /api/costs/timeseries) + guardrails (hourly/daily limits)  
- [x] Bulk backtest lane (Tier 1) implemented, execution locked by Soma-first  
- [x] Tier 2 NT8 confirm stub (NT8_EXECUTOR_NOT_CONFIGURED)  
- [ ] Phase 1: apply_mirror_plan + approval gate  
- [ ] Phase 2: scheduled cadence + alerts  
