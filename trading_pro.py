"""Renda Digital IA Trading PRO v1: paper trading only.

Zero exchange credentials, zero real order endpoints. This module is intentionally
isolated from the existing sales/product functionality.
"""
import hmac
import math
import secrets
from datetime import datetime, timezone
from statistics import mean, pstdev

import requests
from flask import jsonify, render_template, request, session

INITIAL_CASH = 10_000.0
FEE_RATE = 0.001
SLIPPAGE_RATE = 0.0005
PER_BOT_EXPOSURE = 0.10
TOTAL_EXPOSURE = 0.40
RISK_PER_TRADE = 0.005
DAILY_LOSS_LIMIT = 0.02
DRAWDOWN_LIMIT = 0.08
BINANCE_PUBLIC_API = "https://api.binance.com/api/v3"

BOTS = (
    {"id": "trend", "name": "Atlas", "strategy": "Tendência EMA 20/50", "symbol": "BTCUSDT"},
    {"id": "breakout", "name": "Orion", "strategy": "Rompimento de 20 períodos", "symbol": "ETHUSDT"},
    {"id": "reversion", "name": "Nexus", "strategy": "Reversão à média", "symbol": "BTCUSDT"},
    {"id": "momentum", "name": "Pulse", "strategy": "Momentum e RSI", "symbol": "SOLUSDT"},
    {"id": "defensive", "name": "Sentinel", "strategy": "Tendência de baixa volatilidade", "symbol": "ETHUSDT"},
)
BOT_BY_ID = {bot["id"]: bot for bot in BOTS}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ema(values, period):
    if not values or period < 1:
        raise ValueError("Série inválida")
    alpha = 2.0 / (period + 1)
    current = float(values[0])
    for value in values[1:]:
        current = float(value) * alpha + current * (1.0 - alpha)
    return current


def rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0
    changes = [values[i] - values[i - 1] for i in range(len(values) - period, len(values))]
    gain = sum(max(change, 0.0) for change in changes) / period
    loss = sum(max(-change, 0.0) for change in changes) / period
    if loss == 0:
        return 100.0 if gain else 50.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


def volatility(values, period=20):
    recent = values[-(period + 1):]
    returns = [recent[i] / recent[i - 1] - 1.0 for i in range(1, len(recent))]
    return pstdev(returns) if len(returns) >= 2 else 0.0


def stop_distance(closes):
    recent = [abs(closes[i] / closes[i - 1] - 1) for i in range(-14, 0)]
    return min(0.06, max(0.02, mean(recent) * 3.0))


def strategy_signal(bot_id, bars):
    """Signal uses CLOSED hourly bars only; never the currently forming bar."""
    if bot_id not in BOT_BY_ID or len(bars) < 100:
        return "HOLD", "Histórico insuficiente"
    closes = [bar["close"] for bar in bars]
    highs = [bar["high"] for bar in bars]
    last = closes[-1]
    fast, slow = ema(closes, 20), ema(closes, 50)
    strength = rsi(closes)
    if bot_id == "trend":
        if fast > slow * 1.0015 and last > fast:
            return "BUY", "EMA20 acima da EMA50 e preço confirmado"
        if fast < slow or last < slow:
            return "SELL", "Perda da tendência EMA50"
    elif bot_id == "breakout":
        if last > max(highs[-21:-1]) and strength < 80:
            return "BUY", "Fechamento acima da máxima dos 20 candles anteriores"
        if last < mean(closes[-10:]):
            return "SELL", "Preço abaixo da média de 10 períodos"
    elif bot_id == "reversion":
        average = mean(closes[-20:])
        if strength < 32 and last < average * 0.98:
            return "BUY", "RSI baixo e desvio da média de 20 períodos"
        if strength > 55 or last >= average:
            return "SELL", "Reversão atingiu a média ou RSI recuperou"
    elif bot_id == "momentum":
        if 58 < strength < 75 and last > closes[-7] * 1.02 and last > ema(closes, 30):
            return "BUY", "Impulso confirmado por RSI e variação de 6 horas"
        if strength < 48 or last < ema(closes, 30):
            return "SELL", "Momentum enfraquecido"
    elif bot_id == "defensive":
        vol = volatility(closes)
        if ema(closes, 15) > ema(closes, 60) * 1.002 and vol < 0.018:
            return "BUY", "Tendência positiva com volatilidade moderada"
        if ema(closes, 15) < ema(closes, 60) or vol > 0.035:
            return "SELL", "Volatilidade elevada ou tendência perdida"
    return "HOLD", "Sem confirmação de entrada ou saída"


def order_budget(equity, cash, total_exposure, stop_pct):
    """Capital exposure limits take precedence over risk-size suggestions."""
    if any(not math.isfinite(x) for x in (equity, cash, total_exposure, stop_pct)):
        return 0.0
    if min(equity, cash, stop_pct) <= 0:
        return 0.0
    available_exposure = max(0.0, equity * TOTAL_EXPOSURE - total_exposure)
    allowed = min(
        equity * PER_BOT_EXPOSURE,
        equity * RISK_PER_TRADE / stop_pct,
        available_exposure,
        cash / (1 + FEE_RATE),
    )
    return round(max(0.0, allowed), 6) if allowed >= 20 else 0.0


def fetch_markets(http_get=None):
    """Fetch public SPOT data only. Never fake market data on API failure."""
    getter = http_get or requests.get
    result = {}
    for symbol in sorted({bot["symbol"] for bot in BOTS}):
        params = {"symbol": symbol, "interval": "1h", "limit": 160}
        response = getter(BINANCE_PUBLIC_API + "/klines", params=params, timeout=8)
        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, list):
            raise ValueError("Resposta inesperada da Binance")
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        bars = []
        for k in raw:
            if len(k) < 7:
                raise ValueError("Candle incompleto na resposta")
            # Binance fields: open time, OHLC and candle close time.
            if int(k[6]) >= now_ms:
                continue
            bar = {"time": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                   "low": float(k[3]), "close": float(k[4])}
            if not all(math.isfinite(v) and v > 0 for v in
                       (bar["open"], bar["high"], bar["low"], bar["close"])):
                raise ValueError("Dados de mercado inválidos")
            bars.append(bar)
        if len(bars) < 100:
            raise ValueError("Histórico insuficiente para " + symbol)
        ticker = getter(BINANCE_PUBLIC_API + "/ticker/price",
                        params={"symbol": symbol}, timeout=8)
        ticker.raise_for_status()
        price = float(ticker.json()["price"])
        if not math.isfinite(price) or price <= 0:
            raise ValueError("Cotação inválida para " + symbol)
        result[symbol] = {"bars": bars, "price": price, "last_candle": bars[-1]["time"]}
    return result


def init_trading_tables(db_conn):
    conn = db_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tp_accounts (
                user_id INTEGER PRIMARY KEY,
                cash REAL NOT NULL,
                peak_equity REAL NOT NULL,
                day_start_equity REAL NOT NULL,
                day_utc TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'active',
                last_run TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tp_positions (
                user_id INTEGER NOT NULL,
                bot_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                quantity REAL NOT NULL,
                entry_price REAL NOT NULL,
                entry_cost REAL NOT NULL,
                stop_price REAL NOT NULL,
                target_price REAL NOT NULL,
                last_price REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, bot_id)
            );
            CREATE TABLE IF NOT EXISTS tp_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                bot_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                fee REAL NOT NULL,
                realized_pnl REAL NOT NULL DEFAULT 0,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tp_signals (
                user_id INTEGER NOT NULL,
                bot_id TEXT NOT NULL,
                candle_time INTEGER NOT NULL,
                signal TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, bot_id, candle_time)
            );
        """)
        conn.commit()
    finally:
        conn.close()


def ensure_account(conn, user_id):
    today = datetime.now(timezone.utc).date().isoformat()
    conn.execute(
        "INSERT OR IGNORE INTO tp_accounts "
        "(user_id, cash, peak_equity, day_start_equity, day_utc, mode, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?)",
        (user_id, INITIAL_CASH, INITIAL_CASH, INITIAL_CASH, today, utc_now()),
    )
    return conn.execute("SELECT * FROM tp_accounts WHERE user_id = ?", (user_id,)).fetchone()


def open_positions(conn, user_id):
    return conn.execute("SELECT * FROM tp_positions WHERE user_id = ? ORDER BY bot_id",
                        (user_id,)).fetchall()


def equity_of(account, positions, market_prices=None):
    prices = market_prices or {}
    return float(account["cash"]) + sum(
        float(p["quantity"]) * float(prices.get(p["symbol"], p["last_price"]))
        for p in positions
    )


def _record_trade(conn, user_id, bot_id, symbol, side, qty, price, fee, pnl, reason):
    conn.execute(
        "INSERT INTO tp_trades (user_id, bot_id, symbol, side, quantity, price, fee, "
        "realized_pnl, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, bot_id, symbol, side, qty, price, fee, pnl, reason, utc_now()),
    )


def buy_simulated(conn, user_id, account, bot, price, budget, stop_pct):
    if budget < 20 or price <= 0:
        return
    execution = price * (1 + SLIPPAGE_RATE)
    quantity = budget / execution
    fee = budget * FEE_RATE
    conn.execute("UPDATE tp_accounts SET cash = cash - ? WHERE user_id = ?",
                 (budget + fee, user_id))
    conn.execute(
        "INSERT INTO tp_positions (user_id, bot_id, symbol, quantity, entry_price, "
        "entry_cost, stop_price, target_price, last_price, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, bot["id"], bot["symbol"], quantity, execution, budget + fee,
         execution * (1 - stop_pct), execution * (1 + 2 * stop_pct), price, utc_now()),
    )
    _record_trade(conn, user_id, bot["id"], bot["symbol"], "BUY",
                  quantity, execution, fee, 0, "Compra simulada: " + bot["strategy"])


def sell_simulated(conn, user_id, position, price, reason):
    if price <= 0:
        return
    execution = price * (1 - SLIPPAGE_RATE)
    proceeds = float(position["quantity"]) * execution
    fee = proceeds * FEE_RATE
    pnl = proceeds - fee - float(position["entry_cost"])
    conn.execute("UPDATE tp_accounts SET cash = cash + ? WHERE user_id = ?",
                 (proceeds - fee, user_id))
    conn.execute("DELETE FROM tp_positions WHERE user_id = ? AND bot_id = ?",
                 (user_id, position["bot_id"]))
    _record_trade(conn, user_id, position["bot_id"], position["symbol"],
                  "SELL", float(position["quantity"]), execution, fee, pnl, reason)


def state_of(conn, user_id):
    account = ensure_account(conn, user_id)
    positions = open_positions(conn, user_id)
    equity = equity_of(account, positions)
    trades = conn.execute(
        "SELECT bot_id, symbol, side, quantity, price, fee, realized_pnl, reason, created_at "
        "FROM tp_trades WHERE user_id = ? ORDER BY id DESC LIMIT 30", (user_id,)
    ).fetchall()
    signals = conn.execute(
        "SELECT bot_id, signal, reason, candle_time, created_at FROM tp_signals "
        "WHERE user_id = ? ORDER BY created_at DESC LIMIT 10", (user_id,)
    ).fetchall()
    fees = conn.execute("SELECT COALESCE(SUM(fee),0) FROM tp_trades WHERE user_id = ?",
                        (user_id,)).fetchone()[0]
    return {
        "currency": "USDT (fictício)",
        "exchange": "Binance API pública — cotações reais, ordens fictícias",
        "execution_mode": "PAPER_ONLY",
        "last_run": account["last_run"],
        "mode": account["mode"],
        "cash": round(account["cash"], 4),
        "equity": round(equity, 4),
        "pnl": round(equity - INITIAL_CASH, 4),
        "pnl_pct": round((equity / INITIAL_CASH - 1) * 100, 3),
        "drawdown_pct": round(max(0, 1 - equity / max(account["peak_equity"], 0.01)) * 100, 3),
        "day_loss_pct": round(max(0, 1 - equity / max(account["day_start_equity"], 0.01)) * 100, 3),
        "fees_total": round(fees or 0, 4),
        "positions": [dict(p) for p in positions],
        "trades": [dict(t) for t in trades],
        "signals": [dict(s) for s in signals],
        "bots": list(BOTS),
        "limits": {
            "per_bot_exposure_pct": PER_BOT_EXPOSURE * 100,
            "total_exposure_pct": TOTAL_EXPOSURE * 100,
            "risk_per_trade_pct": RISK_PER_TRADE * 100,
            "daily_loss_pct": DAILY_LOSS_LIMIT * 100,
            "drawdown_pct": DRAWDOWN_LIMIT * 100,
            "fee_pct": FEE_RATE * 100,
            "slippage_pct": SLIPPAGE_RATE * 100,
        },
    }


def execute_cycle(db_conn, user_id, markets):
    prices = {symbol: entry["price"] for symbol, entry in markets.items()}
    conn = db_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        account = ensure_account(conn, user_id)
        if account["mode"] != "active":
            conn.rollback()
            raise ValueError("Robôs pausados. Retome primeiro, se permitido.")
        positions = open_positions(conn, user_id)
        # Refresh the last known (possibly stale in the dashboard) market price.
        for p in positions:
            conn.execute(
                "UPDATE tp_positions SET last_price = ?, updated_at = ? "
                "WHERE user_id = ? AND bot_id = ?",
                (prices[p["symbol"]], utc_now(), user_id, p["bot_id"]),
            )
        positions = open_positions(conn, user_id)
        equity = equity_of(account, positions, prices)
        today = datetime.now(timezone.utc).date().isoformat()
        if account["day_utc"] != today:
            conn.execute("UPDATE tp_accounts SET day_utc=?, day_start_equity=? "
                         "WHERE user_id=?", (today, equity, user_id))
            account = ensure_account(conn, user_id)
        peak = max(float(account["peak_equity"]), equity)
        conn.execute("UPDATE tp_accounts SET peak_equity=? WHERE user_id=?", (peak, user_id))
        day_loss = 1 - equity / max(float(account["day_start_equity"]), 0.01)
        drawdown = 1 - equity / max(peak, 0.01)
        if day_loss >= DAILY_LOSS_LIMIT or drawdown >= DRAWDOWN_LIMIT:
            # Paper-only emergency liquidation. Never resume this automatically.
            for p in positions:
                sell_simulated(conn, user_id, p, prices[p["symbol"]], "Circuit breaker: risco excedido")
            conn.execute("UPDATE tp_accounts SET mode='risk_paused', last_run=? WHERE user_id=?",
                         (utc_now(), user_id))
            conn.commit()
            return state_of(conn, user_id)
        for bot in BOTS:
            data = markets[bot["symbol"]]
            pos = conn.execute(
                "SELECT * FROM tp_positions WHERE user_id=? AND bot_id=?",
                (user_id, bot["id"]),
            ).fetchone()
            price = prices[bot["symbol"]]
            signal, reason = strategy_signal(bot["id"], data["bars"])
            if pos:
                if price <= pos["stop_price"]:
                    sell_simulated(conn, user_id, pos, price, "Stop-loss simulado")
                    continue
                if price >= pos["target_price"]:
                    sell_simulated(conn, user_id, pos, price, "Take-profit simulado")
                    continue
            # Signal evaluated once for each closed candle (idempotent).
            wrote = conn.execute(
                "INSERT OR IGNORE INTO tp_signals "
                "(user_id, bot_id, candle_time, signal, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, bot["id"], data["last_candle"], signal, reason, utc_now()),
            )
            if wrote.rowcount != 1:
                continue
            if pos and signal == "SELL":
                sell_simulated(conn, user_id, pos, price, "Saída por sinal: " + reason)
            elif not pos and signal == "BUY":
                latest = ensure_account(conn, user_id)
                remaining = open_positions(conn, user_id)
                total_exposure = sum(
                    p["quantity"] * prices[p["symbol"]] for p in remaining
                )
                live_equity = equity_of(latest, remaining, prices)
                distance = stop_distance([bar["close"] for bar in data["bars"]])
                budget = order_budget(live_equity, latest["cash"], total_exposure, distance)
                if budget:
                    buy_simulated(conn, user_id, latest, bot, price, budget, distance)
        conn.execute("UPDATE tp_accounts SET last_run=? WHERE user_id=?",
                     (utc_now(), user_id))
        conn.commit()
        return state_of(conn, user_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def register_trading_pro(app, login_required, db_conn):
    init_trading_tables(db_conn)
    from trading_backtest import (
        SUPPORTED_DAYS, build_backtest_report, fetch_historical_bars,
        init_backtest_tables, store_report, load_report,
    )
    init_backtest_tables(db_conn)

    def user_id():
        return int(session["user_id"])

    def csrf_valid():
        expected = session.get("trading_pro_csrf", "")
        got = request.headers.get("X-Trading-CSRF", "")
        return bool(expected and got and hmac.compare_digest(expected, got))

    def protected_write(action):
        def wrapped(*args, **kwargs):
            if not csrf_valid():
                return jsonify({"ok": False, "error": "Token de sessão inválido"}), 403
            return action(*args, **kwargs)
        wrapped.__name__ = action.__name__
        return login_required(wrapped)

    @app.route("/trading-pro")
    @login_required
    def trading_pro_page():
        if "trading_pro_csrf" not in session:
            session["trading_pro_csrf"] = secrets.token_urlsafe(32)
        return render_template("trading_pro.html", csrf=session["trading_pro_csrf"])

    @app.route("/trading-pro/api/status")
    @login_required
    def trading_pro_status():
        conn = db_conn()
        try:
            state = state_of(conn, user_id())
            conn.commit()
            return jsonify({"ok": True, "state": state})
        finally:
            conn.close()

    @app.route("/trading-pro/api/cycle", methods=["POST"])
    @protected_write
    def trading_pro_cycle():
        try:
            markets = fetch_markets()
            return jsonify({"ok": True, "state": execute_cycle(db_conn, user_id(), markets)})
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            return jsonify({"ok": False, "error": "Ciclo não executado: " + str(exc)[:180]}), 409

    @app.route("/trading-pro/api/pause", methods=["POST"])
    @protected_write
    def trading_pro_pause():
        conn = db_conn()
        try:
            account = ensure_account(conn, user_id())
            if account["mode"] == "active":
                conn.execute("UPDATE tp_accounts SET mode='paused' WHERE user_id=?", (user_id(),))
            conn.commit()
            return jsonify({"ok": True, "state": state_of(conn, user_id())})
        finally:
            conn.close()

    @app.route("/trading-pro/api/resume", methods=["POST"])
    @protected_write
    def trading_pro_resume():
        conn = db_conn()
        try:
            account = ensure_account(conn, user_id())
            if account["mode"] == "risk_paused":
                conn.commit()
                return jsonify({"ok": False, "error": "Bloqueio de risco: revisão obrigatória"}), 409
            conn.execute("UPDATE tp_accounts SET mode='active' WHERE user_id=?", (user_id(),))
            conn.commit()
            return jsonify({"ok": True, "state": state_of(conn, user_id())})
        finally:
            conn.close()


    @app.route("/trading-pro/backtest")
    @login_required
    def trading_backtest_page():
        if "trading_pro_csrf" not in session:
            session["trading_pro_csrf"] = secrets.token_urlsafe(32)
        return render_template(
            "trading_backtest.html", csrf=session["trading_pro_csrf"],
            supported_days=SUPPORTED_DAYS,
        )

    @app.route("/trading-pro/api/backtest/latest")
    @login_required
    def trading_backtest_latest():
        try:
            days = int(request.args.get("days", "30"))
            if days not in SUPPORTED_DAYS:
                raise ValueError("Período indisponível")
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "Período inválido"}), 400
        saved = load_report(db_conn, user_id(), days)
        return jsonify({"ok": True, "report": saved})

    @app.route("/trading-pro/api/backtest/run", methods=["POST"])
    @protected_write
    def trading_backtest_run():
        payload = request.get_json(silent=True) or {}
        try:
            days = int(payload.get("days", 30))
            if days not in SUPPORTED_DAYS:
                raise ValueError("Período inválido: escolha 14, 30 ou 60 dias")
            markets = {
                symbol: fetch_historical_bars(symbol, days)
                for symbol in sorted({bot["symbol"] for bot in BOTS})
            }
            report = build_backtest_report(days, markets)
            store_report(db_conn, user_id(), report)
            return jsonify({"ok": True, "report": report})
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            return jsonify({
                "ok": False,
                "error": "Backtest não concluído: " + str(exc)[:180],
            }), 409
