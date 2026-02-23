# Next Action

**Single next action:** Run Soma pipeline via exit node; verify Phase0 baseline PASS.

- Kajabi via Mac exit node (laptop-only): `ops/with_exit_node.sh` wraps soma_kajabi_unblock_and_run; config at `/etc/ai-ops-runner/config/soma_kajabi_exit_node.txt`. Laptop must be on/awake.
- Use Soma Connectors UI (Kajabi Bootstrap, Gmail Connect) or server route `POST /api/projects/soma_kajabi/run`; proof in `artifacts/ui_smoke_prod/<run_id>/`.
- Do not unlock orb.backtest.* until Soma Phase0 baseline PASS + gate flipped.
- pred_markets phase0 mirror remains available (read-only); no trading/execution code.
- noVNC hardened: supervised systemd unit (auto-restart), Tailscale-only 6080, reconnection guidance in instructions.txt.
