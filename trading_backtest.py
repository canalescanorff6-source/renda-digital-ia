"""Trading PRO v2 — historic, paper-only validation.

No exchange credentials, no live orders, no synthetic data substitution.
All signals use prior closed candles and execute at the NEXT bar open.
"""
import hashlib
import json
import math
from datetime import datetime, timezone
from statistics import mean, pstdev

import requests

from trading_pro import (
    BOTS, BINANCE_PUBLIC_API, DAILY_LOSS_LIMIT, DRAWDOWN_LIMIT,
    FEE_RATE, INITIAL_CASH, SLIPPAGE_RATE, order_budget, stop_distance,
    strategy_signal,
)

HOUR_MS = 3_600_000
WARMUP_BARS = 160
SUPPORTED_DAYS = (14, 30, 60)
ANNUAL_HOURS = 365 * 24
MAX_MARKERS = 90
SOURCE_NAME = "Binance Spot API pública (candles de 1h)"


def _utc_millis():
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _finite_positive(value):
    num = float(value)
    if not math.isfinite(num) or num <= 0:
        raise ValueError("Preço não finito ou não positivo no histórico")
    return num


def validate_bars(bars, min_count=1):
    """Fail closed on missing or malformed OHLC bars: never interpolate prices."""
    if len(bars) < min_count:
        raise ValueError("Histórico insuficiente para análise")
    previous = None
    for bar in bars:
        timestamp = int(bar["time"])
        opening, high, low, closing = (
            _finite_positive(bar[k]) for k in ("open", "high", "low", "close")
        )
        if low > min(opening, closing) or high < max(opening, closing) or high < low:
            raise ValueError("Inconsistência OHLC encontrada")
        if previous is not None and timestamp - previous != HOUR_MS:
            raise ValueError("Candles ausentes, duplicados ou fora de ordem")
        previous = timestamp
    return bars


def fetch_historical_bars(symbol, days=30, http_get=None, now_ms=None):
    """Bounded pagination of fully-closed public hourly candles.

    Binance returns up to 1000 candles per request. No endpoint here can
    authenticate, place orders, or withdraw funds.
    """
    if days not in SUPPORTED_DAYS:
        raise ValueError("Escolha 14, 30 ou 60 dias")
    if symbol not in {bot["symbol"] for bot in BOTS}:
        raise ValueError("Par não autorizado")
    getter = http_get or requests.get
    now = _utc_millis() if now_ms is None else int(now_ms)
    closed_before = now // HOUR_MS * HOUR_MS
    count = days * 24 + WARMUP_BARS
    first_open = closed_before - count * HOUR_MS
    next_open = first_open
    collected = []
    requests_made = 0

    while next_open < closed_before and len(collected) < count:
        requests_made += 1
        if requests_made > 4:
            raise ValueError("Paginação histórica interrompida")
        response = getter(
            BINANCE_PUBLIC_API + "/klines",
            params={
                "symbol": symbol, "interval": "1h",
                "startTime": next_open, "endTime": closed_before - 1,
                "limit": min(1000, count - len(collected)),
            },
            timeout=10,
        )
        response.raise_for_status()
        raw = response.json()
        if not isinstance(raw, list) or not raw:
            raise ValueError("API sem dados históricos completos")
        new_bars = []
        for row in raw:
            if not isinstance(row, list) or len(row) < 7:
                raise ValueError("Resposta OHLC incompleta")
            timestamp = int(row[0])
            candle_closed = int(row[6]) < closed_before
            if not candle_closed:
                continue
            bar = {
                "time": timestamp,
                "open": _finite_positive(row[1]),
                "high": _finite_positive(row[2]),
                "low": _finite_positive(row[3]),
                "close": _finite_positive(row[4]),
            }
            new_bars.append(bar)
        if not new_bars:
            raise ValueError("Nenhum candle fechado retornado")
        if new_bars[0]["time"] != next_open:
            raise ValueError("Intervalo histórico com lacuna no início")
        validate_bars(new_bars)
        if collected and new_bars[0]["time"] - collected[-1]["time"] != HOUR_MS:
            raise ValueError("Lacuna na paginação do histórico")
        collected.extend(new_bars)
        next_open = new_bars[-1]["time"] + HOUR_MS

    if len(collected) != count or collected[-1]["time"] != closed_before - HOUR_MS:
        raise ValueError("Histórico retornado não cobre o período solicitado")
    validate_bars(collected, min_count=count)
    return collected


def _isoday(millis):
    return datetime.fromtimestamp(millis / 1000, timezone.utc).date().isoformat()


def _timestamp(millis):
    return datetime.fromtimestamp(millis / 1000, timezone.utc).isoformat(timespec="seconds")


def _execute_buy(cash, spot, budget):
    fill = spot * (1 + SLIPPAGE_RATE)
    qty = budget / fill
    fee = budget * FEE_RATE
    return cash - budget - fee, qty, fee, qty * (fill - spot)


def _execute_sell(cash, qty, spot):
    fill = spot * (1 - SLIPPAGE_RATE)
    gross = qty * fill
    fee = gross * FEE_RATE
    return cash + gross - fee, fee, qty * (spot - fill)


def _sample_curve(samples):
    if not samples:
        return []
    stride = max(1, math.ceil(len(samples) / MAX_MARKERS))
    keep = samples[::stride]
    if keep[-1][0] != samples[-1][0]:
        keep.append(samples[-1])
    return [{"time": _timestamp(time), "equity": round(equity, 4)}
            for time, equity in keep]


def _build_metrics(cash, total_fees, total_slippage, realized, samples,
                   win_trades, halted, completed_trades, stopped_trades):
    equity = cash
    gains = sum(x for x in realized if x > 0)
    losses = -sum(x for x in realized if x < 0)
    peak = INITIAL_CASH
    max_dd = 0.0
    hourly_returns = []
    previous = INITIAL_CASH
    for _, point in samples:
        peak = max(peak, point)
        max_dd = max(max_dd, 1 - point / max(peak, 0.01))
        hourly_returns.append(point / max(previous, 0.01) - 1)
        previous = point
    standard = pstdev(hourly_returns) if len(hourly_returns) >= 2 else 0.0
    sharpe = (mean(hourly_returns) / standard * math.sqrt(ANNUAL_HOURS)
              if standard > 1e-12 else None)
    return {
        "ending_equity": round(equity, 4),
        "return_pct": round((equity / INITIAL_CASH - 1) * 100, 3),
        "max_drawdown_pct": round(max_dd * 100, 3),
        "closed_trades": completed_trades,
        "wins": win_trades,
        "win_rate_pct": round(100 * win_trades / completed_trades, 2)
                        if completed_trades else None,
        "profit_factor": round(gains / losses, 3) if losses > 0 else None,
        "sharpe_annualized": round(sharpe, 3) if sharpe is not None else None,
        "fees_usdt": round(total_fees, 4),
        "slippage_usdt": round(total_slippage, 4),
        "halted_by_risk": halted,
        "stop_exits": stopped_trades,
        "curve": _sample_curve(samples),
    }


def simulate_strategy(bot, bars, start, stop):
    """Execute orders at bar[i].open based ONLY on bars[:i].

    Stops/targets are evaluated within the same bar with conservative
    stop-first priority if both were touched; stop gaps fill at the worse
    opening price. Force-closes at the final candle close.
    """
    if bot["id"] not in {b["id"] for b in BOTS} or not 160 <= start < stop <= len(bars):
        raise ValueError("Janela de simulação inválida")
    cash = INITIAL_CASH
    position = None
    fees = slippage = 0.0
    closed = wins = stops = 0
    realized = []
    samples = []
    peak = INITIAL_CASH
    day_start = INITIAL_CASH
    date_key = None
    halted = False
    for i in range(start, stop):
        candle = bars[i]
        day = _isoday(candle["time"])
        if date_key != day:
            # Previous candle equity prevents ignoring overnight gaps.
            day_start = samples[-1][1] if samples else INITIAL_CASH
            date_key = day
        open_mark = cash + (position["qty"] * candle["open"] if position else 0)
        if position and (
            open_mark / max(peak, .01) - 1 <= -DRAWDOWN_LIMIT
            or open_mark / max(day_start, .01) - 1 <= -DAILY_LOSS_LIMIT
        ):
            cash, fee, slip = _execute_sell(cash, position["qty"], candle["open"])
            fees += fee
            slippage += slip
            pnl = position["qty"] * candle["open"] * (1 - SLIPPAGE_RATE) - fee - position["cost"]
            closed += 1
            wins += int(pnl > 0)
            realized.append(pnl)
            position = None
            halted = True

        if not halted:
            # The 160-hour lookback mirrors the live module's fixed fetch window.
            history = bars[max(0, i - WARMUP_BARS):i]
            signal, _ = strategy_signal(bot["id"], history)
            if position and signal == "SELL":
                old = position
                cash, fee, slip = _execute_sell(cash, old["qty"], candle["open"])
                fees += fee
                slippage += slip
                pnl = old["qty"] * candle["open"] * (1 - SLIPPAGE_RATE) - fee - old["cost"]
                closed += 1
                wins += int(pnl > 0)
                realized.append(pnl)
                position = None
            elif position is None and signal == "BUY":
                equity_at_open = cash
                distance = stop_distance([bar["close"] for bar in history])
                budget = order_budget(equity_at_open, cash, 0.0, distance)
                if budget > 0:
                    cash, qty, fee, slip = _execute_buy(cash, candle["open"], budget)
                    fees += fee
                    slippage += slip
                    execution = candle["open"] * (1 + SLIPPAGE_RATE)
                    position = {
                        "qty": qty, "cost": budget + fee,
                        "stop": execution * (1 - distance),
                        "target": execution * (1 + 2 * distance),
                    }
            if position:
                # Pessimistic if both stop and target are inside one OHLC bar.
                if candle["low"] <= position["stop"]:
                    raw_exit = min(candle["open"], position["stop"])
                    stops += 1
                elif candle["high"] >= position["target"]:
                    raw_exit = position["target"]
                else:
                    raw_exit = None
                if raw_exit is not None:
                    old = position
                    cash, fee, slip = _execute_sell(cash, old["qty"], raw_exit)
                    fees += fee
                    slippage += slip
                    pnl = old["qty"] * raw_exit * (1 - SLIPPAGE_RATE) - fee - old["cost"]
                    closed += 1
                    wins += int(pnl > 0)
                    realized.append(pnl)
                    position = None

        equity = cash + (position["qty"] * candle["close"] if position else 0)
        peak = max(peak, equity)
        if not halted and (
            equity / max(peak, .01) - 1 <= -DRAWDOWN_LIMIT
            or equity / max(day_start, .01) - 1 <= -DAILY_LOSS_LIMIT
        ):
            if position:
                old = position
                cash, fee, slip = _execute_sell(cash, old["qty"], candle["close"])
                fees += fee
                slippage += slip
                pnl = old["qty"] * candle["close"] * (1 - SLIPPAGE_RATE) - fee - old["cost"]
                closed += 1
                wins += int(pnl > 0)
                realized.append(pnl)
                position = None
            halted = True
            equity = cash
        samples.append((candle["time"], equity))

    if position:
        last = bars[stop - 1]
        old = position
        cash, fee, slip = _execute_sell(cash, old["qty"], last["close"])
        fees += fee
        slippage += slip
        pnl = old["qty"] * last["close"] * (1 - SLIPPAGE_RATE) - fee - old["cost"]
        closed += 1
        wins += int(pnl > 0)
        realized.append(pnl)
        samples[-1] = (last["time"], cash)
    return _build_metrics(cash, fees, slippage, realized, samples, wins, halted, closed, stops)


def buy_and_hold(bars, start, stop):
    """Fee/slippage-adjusted benchmark with one buy at open and sell at close."""
    entry, exit_ = bars[start]["open"], bars[stop - 1]["close"]
    budget = INITIAL_CASH / (1 + FEE_RATE)
    cash, qty, fee_a, _ = _execute_buy(INITIAL_CASH, entry, budget)
    cash, fee_b, _ = _execute_sell(cash, qty, exit_)
    return {
        "return_pct": round((cash / INITIAL_CASH - 1) * 100, 3),
        "fees_usdt": round(fee_a + fee_b, 4),
    }


def build_backtest_report(days, markets):
    if days not in SUPPORTED_DAYS:
        raise ValueError("Período de backtest inválido")
    expected = WARMUP_BARS + days * 24
    symbols = sorted({b["symbol"] for b in BOTS})
    if set(markets) != set(symbols):
        raise ValueError("Conjunto incompleto de ativos")
    end_times = set()
    fingerprints = {}
    for symbol in symbols:
        bars = validate_bars(markets[symbol], min_count=expected)
        if len(bars) != expected:
            raise ValueError("Histórico incorreto para " + symbol)
        end_times.add(bars[-1]["time"])
        fingerprints[symbol] = hashlib.sha256(
            json.dumps(bars, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    if len(end_times) != 1:
        raise ValueError("Históricos de ativos não sincronizados")

    start = WARMUP_BARS
    split = start + int(days * 24 * .70)
    end = expected
    benchmark = {}
    for symbol in symbols:
        bars = markets[symbol]
        benchmark[symbol] = {
            "evaluation": buy_and_hold(bars, start, split),
            "validation": buy_and_hold(bars, split, end),
        }

    results = []
    for bot in BOTS:
        history = markets[bot["symbol"]]
        a = simulate_strategy(bot, history, start, split)
        b = simulate_strategy(bot, history, split, end)
        results.append({
            "id": bot["id"], "name": bot["name"], "strategy": bot["strategy"],
            "symbol": bot["symbol"], "evaluation": a, "validation": b,
            "benchmark": benchmark[bot["symbol"]],
        })
    return {
        "execution_mode": "BACKTEST_ONLY",
        "source": SOURCE_NAME,
        "fee_pct_each_side": FEE_RATE * 100,
        "slippage_pct_each_side": SLIPPAGE_RATE * 100,
        "days": days,
        "bars_per_symbol": expected,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "earliest_evaluation": _timestamp(markets[symbols[0]][start]["time"]),
        "validation_from": _timestamp(markets[symbols[0]][split]["time"]),
        "last_closed_bar": _timestamp(markets[symbols[0]][end - 1]["time"]),
        "sections": {
            "evaluation": {"bars": split - start, "share_pct": 70},
            "validation": {"bars": end - split, "share_pct": 30},
        },
        "fingerprints": fingerprints,
        "strategies": results,
        "disclaimer": (
            "Retornos retrospectivos hipotéticos, não auditados por corretora; "
            "sem book de ofertas, latência real, liquidez ou impacto de mercado. "
            "Divisão cronológica é apenas descritiva, sem otimização de parâmetros."
        ),
    }


def init_backtest_tables(db_conn):
    conn = db_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tp_backtest_reports (
                user_id INTEGER NOT NULL,
                days INTEGER NOT NULL,
                generated_at TEXT NOT NULL,
                report_json TEXT NOT NULL,
                PRIMARY KEY (user_id, days)
            )
        """)
        conn.commit()
    finally:
        conn.close()


def store_report(db_conn, user_id, report):
    conn = db_conn()
    try:
        conn.execute(
            "INSERT INTO tp_backtest_reports (user_id,days,generated_at,report_json) "
            "VALUES (?,?,?,?) ON CONFLICT(user_id,days) DO UPDATE SET "
            "generated_at=excluded.generated_at, report_json=excluded.report_json",
            (user_id, report["days"], report["created_at"],
             json.dumps(report, separators=(",", ":"), ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def load_report(db_conn, user_id, days):
    conn = db_conn()
    try:
        row = conn.execute(
            "SELECT report_json FROM tp_backtest_reports WHERE user_id=? AND days=?",
            (user_id, days),
        ).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        conn.close()
