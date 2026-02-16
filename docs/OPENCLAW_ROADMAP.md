# OpenClaw Roadmap

## Phases

- **Phase 0 (Zane read-only)**  
  - Kajabi snapshot (read-only)  
  - Gmail harvest  
  - Manifest + `mirror_plan` artifacts  
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
- [ ] Phase 0: soma_kajabi project skeleton + read-only actions + artifacts  
- [ ] Phase 1: apply_mirror_plan + approval gate  
- [ ] Phase 2: scheduled cadence + alerts  
