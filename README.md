# 🤖 Propr DCA Bot — Open Source

> A transparent, open-source Python bot for the [Propr.xyz](https://app.propr.xyz/login?r=ROYA) prop firm. Auto-manages DCA positions with strict risk controls. Built and documented publicly.

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Open Source](https://img.shields.io/badge/Open%20Source-Yes-brightgreen.svg)](#)

## 📖 What is this?

This is a complete trading bot written in Python that connects to [Propr.xyz](https://app.propr.xyz/login?r=ROYA), a 100% on-chain crypto prop firm built on Hyperliquid.

The strategy: **DCA (Dollar Cost Averaging) long-only** with built-in risk management.

**Key safety features:**

- ✅ Global stop loss per cycle
- ✅ Daily loss limit (auto-halt)
- ✅ Max drawdown limit (auto-halt)
- ✅ Trailing break-even (locks in profits)
- ✅ Maximum 2 DCA levels (no infinite grid)
- ✅ Dry-run mode by default (no real orders)
- ✅ Built-in backtest engine

**No "magic" claims. The code does exactly what's in this README. Backtest it yourself.**

## 📊 Backtest Results (6 months)

Run on real historical data:

| Asset | Cycles | Stop Losses | PnL |
|-------|--------|-------------|-----|
| HYPE | 48 | 0 | +3.05% |
| BTC | 25 | 0 | +2.14% |

Not a moonshot. But **zero blown accounts** on 6 months of data.

## ⚡ Quick Start

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Backtest (no API needed)

```bash
python propr_dca_bot.py simulate
```

This downloads 6 months of HYPE data and runs the strategy. Takes ~30 seconds.

### 3. Dry-run (live prices, no real orders)

```bash
python propr_dca_bot.py run
```

Watch the bot make decisions in real-time without placing actual orders. Recommended for 24-48h before going live.

### 4. Live trading

First, get an API key from [Propr.xyz Settings → Developer](https://app.propr.xyz/settings).

Create a `.env` file (copy `.env.example`) and fill your key:

```
PROPR_API_KEY=pk_live_your_key_here
```

Then:

```bash
python propr_dca_bot.py run --live --prod
```

⚠️ **Real orders will be placed on your real Propr account.** Make sure you've tested in dry-run first.

## 🎯 Strategy explained simply

The bot follows 4 simple rules:

1. **Buy** at the current price
2. **Buy more** if price drops by 2% (DCA, max 2 levels)
3. **Sell 50%** if price rises 2.5% above your average entry (TP1)
4. **Sell everything** if price rises 4.5% above your average entry (TP2)
5. **Trailing break-even**: once price hits +1.1%, move stop to +0.1% (you can't lose anymore)
6. **Emergency stop**: close everything if floating loss exceeds your configured limit

That's it. No machine learning. No "secret indicator". Just disciplined execution.

## 🛡️ Risk Management

The bot is designed to NEVER blow your account. Here's how:

| Layer | What it does |
|---|---|
| **Per-trade stop loss** | Closes position if floating loss > `risk_per_grid_usd` |
| **Trailing break-even** | Once profitable, lock minimum gain |
| **Daily loss limit** | Halts bot if daily realized PnL hits `-daily_loss_limit_usd` |
| **Max drawdown** | Halts bot if cumulative PnL hits `-max_drawdown_usd` |
| **Max 2 DCA levels** | Can't infinitely buy the dip into oblivion |

Configure these in `BotConfig` according to your prop firm's rules.

## ⚙️ Configuration

All settings are in the `BotConfig` dataclass at the top of `propr_dca_bot.py`:

```python
@dataclass
class BotConfig:
    # Asset
    symbol: str = 'HYPE'
    
    # Capital management (calibrated for Propr 5K Starter challenge by default)
    starting_balance: float = 5_000.0
    risk_per_grid_usd: float = 50.0       # Stop loss
    daily_loss_limit_usd: float = 150.0    # 3% of capital
    max_drawdown_usd: float = 300.0        # 6% of capital
    leverage: int = 2                      # HYPE = 2x max on Propr
    
    # DCA Grid
    n_levels: int = 2
    grid_spacing_pct: float = 0.020        # 2% between levels
    
    # Take profits
    tp1_pct: float = 0.025                 # +2.5% sells 50%
    tp2_pct: float = 0.045                 # +4.5% closes all
    
    # Trailing break-even
    enable_trailing_be: bool = True
    trailing_trigger_pct: float = 0.011    # Arm at +1.1%
    trailing_sl_pct: float = 0.001          # Move SL to +0.1%
```

Adapt to your account size — e.g. for a 10K challenge, multiply everything by 2.

## 📁 Project Structure

```
propr-dca-bot/
├── propr_dca_bot.py    # Main bot code (~600 lines)
├── run.py              # Quick start entry point
├── requirements.txt    # Python dependencies
├── .env.example        # Template for your API credentials
├── .gitignore          # Protects your secrets from being committed
├── LICENSE             # MIT
└── README.md           # This file
```

## 🧪 Test it on Propr (free demo)

If you don't have a Propr account yet, you can test the platform in **paper trading mode for free** (no payment required).

👉 **[Sign up free on Propr](https://app.propr.xyz/login?r=ROYA)** *(affiliate link — supports the project at no cost to you)*

Once signed up:
1. Go to Dashboard → take the Free Trial or Starter
2. You get a paper account ($5K simulated)
3. Generate an API key in Settings → Developer
4. Plug it into this bot
5. Watch it work

## 📺 Following the project

I'm building this bot **in public** as part of a 3-month experiment documenting my journey from marketer to prop trader. Wins, blows, lessons — all transparent.

- 🐦 **Twitter / X**: [@LeRoyaliste9](https://x.com/LeRoyaliste9)
- 💬 **Telegram (live updates + trades)**: [t.me/roya_proptrading](https://t.me/roya_proptrading)

## ⚠️ Disclaimers

- This bot is for **educational purposes**. Trading involves substantial risk.
- Past backtest performance does **not** guarantee future results.
- Test extensively in **dry-run mode** before going live.
- Never share your API key publicly.
- This is **not financial advice**.

## 🤝 Contributing

Found a bug? Have an improvement? Open an issue or PR.

Ideas welcome:
- WebSocket integration (vs polling)
- Multi-asset support
- Trend filter (EMA H4 confirmation)
- Telegram alerts on TP/SL events
- Backtest report generator

## 📜 License

[MIT](LICENSE) — fork it, modify it, share it. Just don't blame me if you blow your account 😅

---

**If this repo helps you, drop a ⭐ on GitHub. It costs nothing and helps the project reach more people.**

*Built with stubbornness by [@LeRoyaliste9](https://x.com/LeRoyaliste9) in May 2026, after blowing 2 prop firm challenges in 1 week. The bot is what I should've been all along.*
