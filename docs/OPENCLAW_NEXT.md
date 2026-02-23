# Next Action

**Single next action:** Connect Kajabi + Gmail and run Phase0 until baseline PASS.

- Use Soma Connectors UI (Kajabi Bootstrap, Gmail Connect) or server route `POST /api/projects/soma_kajabi/run`; proof in `artifacts/ui_smoke_prod/<run_id>/`.
- Do not unlock orb.backtest.* until Soma Phase0 baseline PASS + gate flipped.
- pred_markets phase0 mirror remains available (read-only); no trading/execution code.
- noVNC hardened: supervised systemd unit (auto-restart), Tailscale-only 6080, reconnection guidance in instructions.txt.
