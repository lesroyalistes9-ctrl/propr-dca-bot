"""
Quick start launcher for the Propr DCA Bot.

By default, runs a backtest (no API key needed). Edit config below and uncomment
the appropriate mode to switch behavior.

Examples (via CLI):
    python propr_dca_bot.py simulate            # backtest
    python propr_dca_bot.py run                 # dry-run (live prices, no orders)
    python propr_dca_bot.py run --live          # live on Beta
    python propr_dca_bot.py run --live --prod   # PRODUCTION (real money)

This run.py is a convenience entry point with config customization.
"""
from propr_dca_bot import BotConfig, simulate, DCABot

if __name__ == '__main__':
    # Configure your strategy here
    cfg = BotConfig(
        symbol='HYPE',
        base='HYPE',
        starting_balance=5_000.0,
        risk_per_grid_usd=50.0,
        daily_loss_limit_usd=150.0,
        max_drawdown_usd=300.0,
        n_levels=2,
        grid_spacing_pct=0.020,
        tp1_pct=0.025,
        tp2_pct=0.045,
        enable_trailing_be=True,
        trailing_trigger_pct=0.011,
        trailing_sl_pct=0.001,
    )

    # Default: backtest (no API key required)
    simulate(cfg)

    # Uncomment for live monitoring (dry-run, no real orders):
    # cfg.dry_run = True
    # DCABot(cfg).run()

    # Uncomment for LIVE TRADING (requires PROPR_API_KEY env variable):
    # cfg.dry_run = False
    # cfg.use_beta = False
    # DCABot(cfg).run()
