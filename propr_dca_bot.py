"""
Propr DCA Bot — Open Source Trading Bot for Propr.xyz Prop Firm
================================================================

A Python bot that auto-manages DCA (Dollar Cost Averaging) positions on Propr.xyz,
a crypto prop firm built on Hyperliquid. Designed for paper trading first, with strict
risk management built-in (stop loss, daily loss check, max drawdown).

Repo: https://github.com/leroyaliste9/propr-dca-bot
License: MIT


Stratégie :
- Place un BUY initial sur BTC (ou autre asset)
- Si le prix descend de X% sous le prix moyen → re-buy (DCA, max 3 niveaux)
- Si le prix monte au-dessus de TP1 → vend 50% (sécurise)
- Si le prix monte au-dessus de TP2 → close tout (profite)
- Stop loss GLOBAL si la position en perte dépasse le risque max
- Arrêt automatique si daily loss touchée ou drawdown approché

SÉCURITÉ par défaut :
- DRY_RUN = True (simule, ne passe pas d'ordre réel)
- USE_BETA = True (endpoint testnet, pas de fonds réels)
- N_LEVELS = 3 max (pas un grid infini)
- Stop loss obligatoire

⚠️ AVANT de passer en LIVE :
1. Run en DRY_RUN sur Beta pendant au moins 1 semaine
2. Vérifier les logs et le PnL simulé
3. Run en LIVE sur Beta avec petits montants
4. SEULEMENT après → passer en production avec ton vrai compte
"""
from __future__ import annotations
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Literal

# ============================================================
# ULID generator (no external dependency)
# ============================================================
import secrets
def generate_ulid() -> str:
    """ULID format: 26 chars, Crockford base32. Sufficient for idempotency."""
    alphabet = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
    ts = int(time.time() * 1000)
    ts_part = ''
    for _ in range(10):
        ts_part = alphabet[ts & 31] + ts_part
        ts >>= 5
    rand_part = ''.join(secrets.choice(alphabet) for _ in range(16))
    return ts_part + rand_part


# ============================================================
# CONFIG
# ============================================================
@dataclass
class BotConfig:
    # === API ===
    api_key: str = field(default_factory=lambda: os.getenv('PROPR_API_KEY', ''))
    account_id_override: str = field(default_factory=lambda: os.getenv('PROPR_ACCOUNT_ID', ''))
    use_beta: bool = True              # Beta = testnet. False = production (real funds)
    dry_run: bool = True               # True = simulate, no real orders placed

    # === Asset ===
    symbol: str = 'HYPE'               # Trading symbol (HYPE for Hyperliquid native, BTC/ETH/SOL via Binance fallback)
    base: str = 'HYPE'
    quote: str = 'USDC'

    # === Money management (calibrated sur Propr 5K 1-Step) ──
    starting_balance: float = 5_000.0
    risk_per_grid_usd: float = 50.0    # Total risk per grid cycle in USD
    daily_loss_limit_usd: float = 150.0  # 3% of capital
    max_drawdown_usd: float = 300.0    # 6% of capital
    leverage: int = 2                  # Max leverage. HYPE/Other crypto = 2x on Propr

    # === Grid DCA ===
    n_levels: int = 2                  # Number of DCA levels (entry + N-1 DCA buys)
    grid_spacing_pct: float = 0.020    # Percentage between DCA levels
    size_multiplier: float = 1.0       # Position size multiplier per level (1.0 = uniform, >1 = martingale)

    # === Take Profits ===
    tp1_pct: float = 0.025             # First TP at +2.5% above average (sells tp1_fraction of position)
    tp2_pct: float = 0.045             # Final TP at +4.5% above average (closes everything)
    tp1_fraction: float = 0.5          # Fraction of position to sell at TP1 (0.5 = half)
    position_size_per_level_usd: float = 0.0  # Direct notional per level. 0 = auto-calc from risk

    # ── Trailing Break-Even ──
    enable_trailing_be: bool = True       # Enable trailing break-even
    trailing_trigger_pct: float = 0.011   # Price must hit +1.1% to arm trailing
    trailing_sl_pct: float = 0.001        # New SL position once armed (+0.1%)

    # ── Quantity precision per asset ──
    # Different assets have different min quantity steps (Propr/Hyperliquid)
    # BTC=0.001, ETH=0.01, SOL=0.1, HYPE=0.01 (auto-detected from symbol)
    qty_decimals: int = 0                 # 0 = auto-detect from symbol

    # === Loop ===
    poll_interval_sec: int = 30        # Price refresh interval in seconds
    direction: Literal['long'] = 'long'   # LONG-ONLY pour HYPE

    # === Logging ===
    log_file: str = 'propr_dca_bot.log'

    @property
    def base_url(self) -> str:
        if self.use_beta:
            return 'https://beta-api.propr.xyz/v1'
        return 'https://api.propr.xyz/v1'

    @property
    def size_per_level_usd(self) -> float:
        """Notional USD per level. Uses position_size_per_level_usd directly if set."""
        if self.position_size_per_level_usd > 0:
            return self.position_size_per_level_usd
        # Fallback: auto-calculation based on risk per grid
        total_weight = sum(self.size_multiplier ** i for i in range(self.n_levels))
        return (self.risk_per_grid_usd * self.leverage * 4) / total_weight

    @property
    def qty_precision(self) -> int:
        """Auto-detect quantity decimal precision based on asset (overridable)."""
        if self.qty_decimals > 0:
            return self.qty_decimals
        precision_map = {
            'BTC': 3, 'ETH': 2, 'SOL': 1, 'DOGE': 0, 'XRP': 0, 'AVAX': 1,
            'LINK': 1, 'HYPE': 2, 'PURR': 2,
        }
        return precision_map.get(self.base.upper(), 2)


# ============================================================
# LOGGER
# ============================================================
def setup_logger(log_file: str) -> logging.Logger:
    logger = logging.getLogger('propr_dca_bot')
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter('[%(asctime)s] %(levelname)s %(message)s', '%H:%M:%S')
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        logger.addHandler(ch)
        fh = logging.FileHandler(log_file)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


# ============================================================
# API CLIENT
# ============================================================
class ProprClient:
    def __init__(self, cfg: BotConfig, logger: logging.Logger):
        self.cfg = cfg
        self.log = logger
        self.account_id: str | None = None
        if not cfg.api_key and not cfg.dry_run:
            raise ValueError("PROPR_API_KEY required (set env var or BotConfig.api_key)")

    def _headers(self) -> dict:
        return {
            'X-API-Key': self.cfg.api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'propr-dca-bot/1.0 (+https://github.com/leroyaliste9/propr-dca-bot)',
        }

    def _request(self, method: str, path: str, params: dict = None,
                 body: dict = None) -> tuple[int, dict]:
        url = self.cfg.base_url + path
        if params:
            url += '?' + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, {'error': str(e)}
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            self.log.error(f"Network error on {method} {path}: {e}")
            return 0, {'error': str(e), 'network': True}
        except json.JSONDecodeError as e:
            self.log.error(f"Malformed JSON response from {path}: {e}")
            return 0, {'error': 'bad_json'}

    def get(self, path: str, **params) -> dict:
        status, data = self._request('GET', path, params)
        return data

    def post(self, path: str, body: dict = None) -> tuple[int, dict]:
        return self._request('POST', path, body=body)

    def setup_account(self) -> str:
        """Find a tradeable accountId. Priority: env override > funded > active challenge."""
        if self.cfg.dry_run:
            self.account_id = 'urn:prp-account:DRY_RUN'
            self.log.info(f"DRY_RUN mode -> fake accountId: {self.account_id}")
            return self.account_id

        # PRIORITE 1: override via env var PROPR_ACCOUNT_ID
        if self.cfg.account_id_override:
            override = self.cfg.account_id_override
            if not override.startswith('urn:prp-account:'):
                override = f'urn:prp-account:{override}'
            self.account_id = override
            self.log.info(f"Account override used: {self.account_id}")
            # Validation: test /users/me to verify API key
            r = self.get('/users/me')
            if 'userId' in r or 'data' in r:
                self.log.info(f"API key VALID: /users/me returned user data")
            else:
                self.log.warning(f"API key check inconclusive: {json.dumps(r)[:200]}")
            return self.account_id

        # Otherwise auto-scan
        self.log.info(f"DEBUG: Scanning endpoints at {self.cfg.base_url}")
        r_me = self.get('/users/me')
        self.log.info(f"DEBUG: /users/me -> {json.dumps(r_me)[:200]}")

        r = self.get('/book-account-issuances')
        issuances = r.get('data', [])
        active = [i for i in issuances if i.get('status') == 'active']
        if active:
            self.account_id = active[0]['accountId']
            self.log.info(f"Funded account found: {self.account_id}")
            return self.account_id

        r = self.get('/challenge-attempts')
        attempts = r.get('data', [])
        active = [a for a in attempts if a.get('status') == 'active']
        if active:
            self.account_id = active[0]['accountId']
            self.log.info(f"Challenge account found: {self.account_id}")
            return self.account_id

        raise Exception("No tradeable account. Set PROPR_ACCOUNT_ID env var or purchase a challenge.")

    def get_positions(self) -> list[dict]:
        if self.cfg.dry_run:
            return []
        r = self.get(f'/accounts/{self.account_id}/positions', status='open',
                     base=self.cfg.base)
        return [p for p in r.get('data', []) if Decimal(p.get('quantity', '0')) > 0]

    def get_mark_price(self) -> float:
        """Fetch current price from Hyperliquid first, fallback to Binance for non-HL tokens."""
        # Priorité 1 : API Hyperliquid (assets natifs comme HYPE)
        try:
            url = 'https://api.hyperliquid.xyz/info'
            body = json.dumps({"type": "allMids"}).encode()
            req = urllib.request.Request(url, data=body,
                                          headers={'Content-Type': 'application/json'},
                                          method='POST')
            with urllib.request.urlopen(req, timeout=5) as r:
                mids = json.loads(r.read())
                if self.cfg.base in mids:
                    return float(mids[self.cfg.base])
        except Exception as e:
            self.log.debug(f"Hyperliquid price fetch failed: {e}")
        # Priorité 2 : Binance (BTC, ETH, SOL, etc.)
        try:
            url = f'https://api.binance.com/api/v3/ticker/price?symbol={self.cfg.base}USDT'
            with urllib.request.urlopen(url, timeout=5) as r:
                return float(json.loads(r.read())['price'])
        except Exception as e:
            self.log.warning(f"Price fetch failed for {self.cfg.base}: {e}")
            return 0.0

    def place_market_order(self, side: Literal['buy', 'sell'], quantity: float,
                           position_side: Literal['long', 'short'] = 'long',
                           reduce_only: bool = False, close_position: bool = False) -> dict | None:
        if self.cfg.dry_run:
            mock = {
                'orderId': f'mock-{generate_ulid()}', 'status': 'filled',
                'side': side, 'quantity': str(quantity),
                'averageFillPrice': str(self.get_mark_price()),
            }
            self.log.info(f"[DRY_RUN] Order placed: {side.upper()} {quantity:.6f} {self.cfg.base} "
                          f"({'REDUCE' if reduce_only else 'OPEN'})")
            return mock
        body = {'orders': [{
            'accountId': self.account_id,
            'intentId': generate_ulid(),
            'exchange': 'hyperliquid',
            'type': 'market',
            'side': side,
            'positionSide': position_side,
            'productType': 'perp',
            'timeInForce': 'IOC',
            'asset': self.cfg.base,
            'base': self.cfg.base,
            'quote': self.cfg.quote,
            'quantity': str(quantity),
            'reduceOnly': reduce_only,
            'closePosition': close_position,
        }]}
        status, data = self.post(f'/accounts/{self.account_id}/orders', body)
        if status not in (200, 201):
            self.log.error(f"Order failed: {status} {data}")
            return None
        order = data['data'][0]
        self.log.info(f"Order placed: {side.upper()} {quantity:.6f} {self.cfg.base} "
                      f"@ ~{order.get('averageFillPrice', 'market')}")
        return order


# ============================================================
# BOT STATE
# ============================================================
@dataclass
class GridLevel:
    level: int
    target_price: float    # Trigger price for this level
    qty: float
    notional: float
    filled: bool = False
    fill_price: float | None = None
    fill_time: datetime | None = None

@dataclass
class BotState:
    levels: list[GridLevel] = field(default_factory=list)
    total_qty: float = 0.0          # Quantité totale long (somme des fills)
    avg_entry: float = 0.0          # Prix moyen pondéré
    realized_pnl: float = 0.0
    daily_pnl: float = 0.0
    last_reset: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tp1_done: bool = False
    trailing_armed: bool = False
    halted: bool = False
    halt_reason: str = ''

    def has_position(self) -> bool:
        return self.total_qty > 1e-9

    def recompute_avg(self) -> None:
        filled = [l for l in self.levels if l.filled]
        if not filled:
            self.total_qty = 0.0
            self.avg_entry = 0.0
            return
        total_q = sum(l.qty for l in filled)
        total_cost = sum(l.qty * l.fill_price for l in filled)
        self.total_qty = total_q
        self.avg_entry = total_cost / total_q if total_q > 0 else 0


# ============================================================
# BOT LOGIC
# ============================================================
class DCABot:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.log = setup_logger(cfg.log_file)
        self.client = ProprClient(cfg, self.log)
        self.state = BotState()

    def init_grid(self, ref_price: float) -> None:
        """Calculate grid levels based on the reference price."""
        size_usd = self.cfg.size_per_level_usd
        self.state.levels = []
        for i in range(self.cfg.n_levels):
            target = ref_price * (1 - self.cfg.grid_spacing_pct * i)
            notional = size_usd * (self.cfg.size_multiplier ** i)
            qty = notional / target
            # Arrondir à la précision minimum (BTC = 0.001)
            step = Decimal(10) ** -self.cfg.qty_precision
            qty = float(Decimal(str(qty)).quantize(step, rounding=ROUND_DOWN))
            if qty <= 0:
                self.log.error(f"Computed qty=0 for level {i} (notional ${notional:.2f}, "
                              f"price {target:.4f}, precision={self.cfg.qty_precision}). "
                              f"Increase position_size_per_level_usd or check asset precision.")
                continue
            self.state.levels.append(GridLevel(level=i, target_price=target,
                                                qty=qty, notional=notional))
            self.log.info(f"  Level {i}: target={target:.2f} qty={qty} notional=${notional:.2f}")

    sim_now: datetime | None = None  # for simulator

    def check_safety(self) -> bool:
        """Returns False if the bot must halt."""
        # Reset daily PnL on new day
        now = self.sim_now or datetime.now(timezone.utc)
        if now.date() != self.state.last_reset.date():
            self.log.info(f"Daily reset. Yesterday pnl: ${self.state.daily_pnl:.2f}")
            self.state.daily_pnl = 0
            self.state.last_reset = now

        # Daily loss check
        if self.state.daily_pnl <= -self.cfg.daily_loss_limit_usd:
            self.state.halted = True
            self.state.halt_reason = 'daily_loss_limit'
            self.log.error(f"⛔ HALT: daily loss limit hit (${self.state.daily_pnl:.2f})")
            return False
        # Drawdown check (sur PnL cumulé)
        if self.state.realized_pnl <= -self.cfg.max_drawdown_usd:
            self.state.halted = True
            self.state.halt_reason = 'max_drawdown'
            self.log.error(f"⛔ HALT: max drawdown hit (${self.state.realized_pnl:.2f})")
            return False
        return True

    def step(self, current_price: float) -> None:
        """One iteration of the decision loop."""
        if not self.check_safety():
            return

        # No position → init grid and place first order
        if not self.state.has_position():
            if not self.state.levels:
                self.log.info(f"Initializing grid around ${current_price:.2f}...")
                self.init_grid(current_price)
            # Place level 0 (entry)
            level0 = self.state.levels[0]
            if not level0.filled:
                order = self.client.place_market_order('buy', level0.qty,
                                                       position_side=self.cfg.direction)
                if order:
                    level0.filled = True
                    level0.fill_price = float(order['averageFillPrice'])
                    level0.fill_time = datetime.now(timezone.utc)
                    self.state.recompute_avg()
                    self.log.info(f"✅ Position OPENED @ ${level0.fill_price:.2f}")
            return

        # Position open → check DCA, TP, SL
        avg = self.state.avg_entry
        if avg <= 0:
            self.log.warning(f"avg_entry={avg}, skipping step (position state may be corrupt)")
            return
        pnl_pct = (current_price - avg) / avg
        pnl_usd = (current_price - avg) * self.state.total_qty

        # DCA: price below next unfilled level?
        for lvl in self.state.levels:
            if not lvl.filled and current_price <= lvl.target_price:
                order = self.client.place_market_order('buy', lvl.qty,
                                                       position_side=self.cfg.direction)
                if order:
                    lvl.filled = True
                    lvl.fill_price = float(order['averageFillPrice'])
                    lvl.fill_time = datetime.now(timezone.utc)
                    self.state.recompute_avg()
                    self.log.info(f"📉 DCA level {lvl.level} filled @ ${lvl.fill_price:.2f} "
                                  f"| new avg=${self.state.avg_entry:.2f} | total qty={self.state.total_qty}")
                break  # Only one DCA per step

        # Trailing break-even: arm (only if TP1 not already hit)
        if self.cfg.enable_trailing_be and not self.state.trailing_armed \
                and not self.state.tp1_done \
                and current_price >= avg * (1 + self.cfg.trailing_trigger_pct):
            self.state.trailing_armed = True
            self.log.info(f"Trailing ARMED at ${current_price:.4f} | "
                          f"new SL = ${avg * (1 + self.cfg.trailing_sl_pct):.4f}")

        # Trailing break-even: trigger (close remaining position if price drops below +0.1%)
        if self.state.trailing_armed and current_price <= avg * (1 + self.cfg.trailing_sl_pct):
            qty_to_sell = self.state.total_qty
            close_side = 'sell' if self.cfg.direction == 'long' else 'buy'
            close_pos_side = 'short' if self.cfg.direction == 'long' else 'long'
            order = self.client.place_market_order(close_side, qty_to_sell,
                                                   position_side=close_pos_side,
                                                   reduce_only=True, close_position=True)
            if order:
                realized = (current_price - avg) * qty_to_sell
                self.state.realized_pnl += realized
                self.state.daily_pnl += realized
                self.log.info(f"🟡 TRAILING BE HIT @ ${current_price:.4f} | "
                              f"realized=${realized:+.2f} | break-even locked")
                self.state.levels = []
                self.state.total_qty = 0
                self.state.tp1_done = False
                self.state.trailing_armed = False
            return

        # TP1: price up enough → sell half
        if not self.state.tp1_done and current_price >= avg * (1 + self.cfg.tp1_pct):
            qty_to_sell = self.state.total_qty * self.cfg.tp1_fraction
            step = Decimal(10) ** -self.cfg.qty_precision
            qty_to_sell = float(Decimal(str(qty_to_sell)).quantize(step, rounding=ROUND_DOWN))
            if qty_to_sell > 0:
                # Fix Propr API: to close a long, positionSide must be "short" (Propr convention)
                close_side = 'sell' if self.cfg.direction == 'long' else 'buy'
                close_pos_side = 'short' if self.cfg.direction == 'long' else 'long'
                order = self.client.place_market_order(close_side, qty_to_sell,
                                                       position_side=close_pos_side,
                                                       reduce_only=True)
                if order:
                    realized = (current_price - avg) * qty_to_sell
                    self.state.realized_pnl += realized
                    self.state.daily_pnl += realized
                    self.state.total_qty -= qty_to_sell
                    self.state.tp1_done = True
                    self.log.info(f"💰 TP1 hit → sold {qty_to_sell} @ ${current_price:.2f} "
                                  f"| realized=${realized:.2f} | remaining qty={self.state.total_qty}")

        # TP2: close all
        elif current_price >= avg * (1 + self.cfg.tp2_pct):
            qty_to_sell = self.state.total_qty
            close_side = 'sell' if self.cfg.direction == 'long' else 'buy'
            close_pos_side = 'short' if self.cfg.direction == 'long' else 'long'
            order = self.client.place_market_order(close_side, qty_to_sell,
                                                   position_side=close_pos_side,
                                                   reduce_only=True, close_position=True)
            if order:
                realized = (current_price - avg) * qty_to_sell
                self.state.realized_pnl += realized
                self.state.daily_pnl += realized
                self.log.info(f"🎯 TP2 hit → CLOSED ALL @ ${current_price:.2f} "
                              f"| realized=${realized:.2f}")
                # Reset state for next cycle
                self.state.levels = []
                self.state.total_qty = 0
                self.state.tp1_done = False
                self.state.trailing_armed = False

        # Global stop loss: floating loss exceeds max risk
        elif pnl_usd <= -self.cfg.risk_per_grid_usd:
            qty_to_sell = self.state.total_qty
            close_side = 'sell' if self.cfg.direction == 'long' else 'buy'
            close_pos_side = 'short' if self.cfg.direction == 'long' else 'long'
            order = self.client.place_market_order(close_side, qty_to_sell,
                                                   position_side=close_pos_side,
                                                   reduce_only=True, close_position=True)
            if order:
                self.state.realized_pnl += pnl_usd
                self.state.daily_pnl += pnl_usd
                self.log.warning(f"🛑 STOP LOSS hit @ ${current_price:.2f} | loss=${pnl_usd:.2f}")
                self.state.levels = []
                self.state.total_qty = 0
                self.state.tp1_done = False
                self.state.trailing_armed = False

    def run(self) -> None:
        """Main monitoring loop."""
        self.log.info("=" * 60)
        self.log.info(f"Propr DCA Bot starting | symbol={self.cfg.symbol} "
                      f"| dry_run={self.cfg.dry_run} | beta={self.cfg.use_beta}")
        self.log.info("=" * 60)
        self.client.setup_account()
        self.log.info(f"Account ready: {self.client.account_id}")
        self.log.info(f"Config: levels={self.cfg.n_levels} spacing={self.cfg.grid_spacing_pct*100:.1f}% "
                      f"size/level=${self.cfg.size_per_level_usd:.2f} risk=${self.cfg.risk_per_grid_usd}")

        while not self.state.halted:
            try:
                price = self.client.get_mark_price()
                if price > 0:
                    self.step(price)
                    if self.state.has_position():
                        pnl = (price - self.state.avg_entry) * self.state.total_qty
                        self.log.info(f"[tick] price=${price:.2f} avg=${self.state.avg_entry:.2f} "
                                      f"qty={self.state.total_qty} floating=${pnl:+.2f}")
                    else:
                        self.log.info(f"[tick] price=${price:.2f} | no position | "
                                      f"realized=${self.state.realized_pnl:+.2f} daily=${self.state.daily_pnl:+.2f}")
                time.sleep(self.cfg.poll_interval_sec)
            except KeyboardInterrupt:
                self.log.info("Interrupted by user. Shutting down.")
                break
            except Exception as e:
                self.log.error(f"Loop error: {e}")
                time.sleep(self.cfg.poll_interval_sec)

        self.log.info(f"Bot stopped. Halt reason: {self.state.halt_reason or 'manual'}")
        self.log.info(f"Final stats: realized=${self.state.realized_pnl:.2f} daily=${self.state.daily_pnl:.2f}")


# ============================================================
# SIMULATOR — fait tourner le bot sur historique
# ============================================================
def simulate(cfg: BotConfig, ohlc_csv: str = None, lookback_days: int = 180) -> dict:
    """
    Fait tourner la logique du bot sur historique BTC pour valider la stratégie.
    Pas d'appel API, pas de réseau (sauf pour fetch les données 1x).
    """
    import pandas as pd
    cfg_sim = BotConfig(**{**cfg.__dict__, 'dry_run': True})
    log = setup_logger('simulate.log')
    log.info("=" * 60)
    log.info("SIMULATOR: running bot logic on historical data")
    log.info("=" * 60)

    # Load data
    if ohlc_csv and Path(ohlc_csv).exists():
        df = pd.read_csv(ohlc_csv, parse_dates=['timestamp']).set_index('timestamp')
    else:
        end_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        start_ms = end_ms - lookback_days * 24 * 60 * 60 * 1000

        if cfg.base in ('HYPE',):
            # Fetch via Hyperliquid API (HYPE is not on Binance)
            log.info(f"Fetching {cfg.base} historical data from Hyperliquid...")
            all_data = []
            cur = start_ms
            while cur < end_ms:
                body = json.dumps({
                    "type": "candleSnapshot",
                    "req": {"coin": cfg.base, "interval": "1h", "startTime": cur, "endTime": end_ms}
                }).encode()
                req = urllib.request.Request(
                    "https://api.hyperliquid.xyz/info",
                    data=body,
                    headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=15) as r:
                    chunk = json.loads(r.read())
                if not chunk:
                    break
                all_data.extend(chunk)
                last_t = chunk[-1].get("T", chunk[-1].get("t"))
                if last_t and last_t > cur:
                    cur = last_t + 1
                else:
                    break
                if len(chunk) < 100:  # no more data
                    break
            df = pd.DataFrame(all_data)
            df['timestamp'] = pd.to_datetime(df['t'], unit='ms', utc=True)
            df.set_index('timestamp', inplace=True)
            df = df.rename(columns={'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close'})
            df = df[['open', 'high', 'low', 'close']].astype(float)
            df = df[~df.index.duplicated(keep='first')]
        else:
            # Fetch via Binance (BTC, ETH, SOL, etc.)
            all_data = []
            cur = start_ms
            while cur < end_ms:
                url = f"https://api.binance.com/api/v3/klines?symbol={cfg.base}USDT&interval=1h&startTime={cur}&limit=1000"
                with urllib.request.urlopen(url, timeout=15) as r:
                    data = json.loads(r.read())
                if not data: break
                all_data.extend(data)
                cur = data[-1][6] + 1
            df = pd.DataFrame(all_data, columns=['open_time','open','high','low','close','volume',
                                                  'close_time','q','t','tb','tq','ig'])
            df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms', utc=True)
            df.set_index('timestamp', inplace=True)
            df = df[['open','high','low','close']].astype(float)

    log.info(f"Loaded {len(df)} candles for simulation")

    # Bot with mock API (no calls)
    bot = DCABot(cfg_sim)
    bot.state.last_reset = df.index[0].to_pydatetime()
    # On override get_mark_price pour utiliser les bougies
    sim_state = {'cycles': 0, 'tp1_hits': 0, 'tp2_hits': 0, 'sl_hits': 0,
                 'dca_fills': 0, 'final_pnl': 0}

    # Simulate each candle: bot sees close only (simplification)
    bot.client.account_id = 'SIM'
    daily_pnl_by_day = {}

    last_realized = 0
    for ts, row in df.iterrows():
        # On simule sur close (simplification ; un vrai simulator regarderait high/low pour TP/SL intrabar)
        current_price = row['close']

        # Override get_mark_price for place_market_order which uses it
        bot.client.get_mark_price = lambda p=current_price: p

        bot.sim_now = ts.to_pydatetime()

        # Force intrabar check: use high for TP, low for SL (realistic)
        # Run 2 steps: 1 on high (TP), 1 on low (SL/DCA)
        # But this can cause double-fill, so we simplify: just close
        prev_total = bot.state.total_qty
        prev_tp1 = bot.state.tp1_done
        prev_realized = bot.state.realized_pnl
        bot.step(current_price)

        if bot.state.total_qty > prev_total:
            sim_state['dca_fills'] += 1
        if bot.state.realized_pnl > prev_realized:
            if bot.state.tp1_done and not prev_tp1:
                sim_state['tp1_hits'] += 1
            elif not bot.state.has_position() and bot.state.realized_pnl - prev_realized > 0:
                sim_state['tp2_hits'] += 1
                sim_state['cycles'] += 1
            elif not bot.state.has_position() and bot.state.realized_pnl - prev_realized < 0:
                sim_state['sl_hits'] += 1
                sim_state['cycles'] += 1

        if bot.state.halted:
            log.warning(f"Bot halted at {ts}: {bot.state.halt_reason}")
            break

    sim_state['final_pnl'] = bot.state.realized_pnl
    sim_state['final_floating'] = (df['close'].iloc[-1] - bot.state.avg_entry) * bot.state.total_qty \
        if bot.state.has_position() else 0

    log.info(f"\n=== SIMULATOR RESULTS ===")
    log.info(f"Cycles completed      : {sim_state['cycles']}")
    log.info(f"TP1 hits (partials)   : {sim_state['tp1_hits']}")
    log.info(f"TP2 hits (full close) : {sim_state['tp2_hits']}")
    log.info(f"Stop Loss hits        : {sim_state['sl_hits']}")
    log.info(f"DCA fills             : {sim_state['dca_fills']}")
    log.info(f"PnL realized total    : ${sim_state['final_pnl']:+.2f}")
    log.info(f"PnL floating final    : ${sim_state['final_floating']:+.2f}")
    log.info(f"% of capital           : {sim_state['final_pnl']/cfg.starting_balance*100:+.2f}%")
    log.info(f"Halted ?              : {bot.state.halted} ({bot.state.halt_reason})")
    return sim_state


# ============================================================
# CLI
# ============================================================
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(
        description='Propr DCA Bot - Open source trading bot for Propr.xyz',
        epilog='Defaults are safe: dry-run mode, beta endpoint, simulate mode if no args.'
    )
    parser.add_argument('mode', nargs='?', default='simulate',
                       choices=['simulate', 'run'],
                       help='simulate = backtest, run = live monitoring (default: simulate)')
    parser.add_argument('--live', action='store_true',
                       help='Place real orders (default: dry-run, no real orders)')
    parser.add_argument('--prod', action='store_true',
                       help='Use production endpoint (default: beta testnet)')
    args = parser.parse_args()

    cfg = BotConfig()
    if args.live:
        cfg.dry_run = False
        print("LIVE MODE: orders WILL be placed.")
    if args.prod:
        cfg.use_beta = False
        print("PRODUCTION endpoint: real funds.")

    if args.live and not cfg.api_key:
        print("ERROR: --live requires PROPR_API_KEY env variable.")
        print("       Get yours at https://app.propr.xyz/settings (Developer tab)")
        sys.exit(1)

    if args.mode == 'simulate':
        simulate(cfg)
    elif args.mode == 'run':
        DCABot(cfg).run()
