"""Surge Rider: a deterministic paper-trading bot for the pump.fun replay.

Strategy in one paragraph: watch every bonding curve's tape, but trade only
late-stage curves (30-85 SOL of real reserves: established runners, not fresh
launches) that print a fresh, broad-based buying surge (net SOL inflow,
several distinct buyers, price up and pinned near its window high, the latest
trade a buy). One entry per curve, ever: second entries on the same curve lose
money on the dev window, so re-entry is off even after a win. A single small
buy (0.04 SOL), no pyramid; exit whole on a wide take-profit (+30%), a wide
stop loss (-22.5%, wide because single large sells gap the price several
percent in one trade), or a 900-slot time limit. A paced probe mechanism
keeps the run above the guardrail minimums (20 coins, 0.01 SOL round trips)
on quiet tapes: 0.012 SOL probes on liquid curves with recent buy flow, sold
as soon as they land.

Deterministic by construction: no clock, no randomness, no environment reads;
iteration only over insertion-ordered dicts and lists built from the batch
stream. All money is integer lamports; prices are float ratios of reserves.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bot  # noqa: E402  (after the sys.path line: -P leaves the script dir off sys.path)

LAMPORTS = makefun_bot.LAMPORTS_PER_SOL
# Curve fee taken on every fill: protocol 95 bps + creator 30 bps.
CURVE_FEE = 0.0125
BPS = 10_000

# --- entry signal -----------------------------------------------------------
ENTRY_WINDOW = 60          # slots of tape the surge is measured over
MIN_NET_SOL = 0.8          # net buy SOL (buys minus sells) over the window
MIN_BUYS = 4               # buy trades in the window
MIN_BUYERS = 4             # distinct buying wallets in the window
MIN_UP = 0.06              # price now vs first price in the window, minus 1
MAX_UP = 2.0               # skip curves that already ran too far in one window
NEAR_HIGH = 0.92           # price now at least this share of the window high
MAX_BUYER_SHARE = 0.65     # largest single buyer at most this share of buy SOL
MIN_RQ_SOL = 30.0          # curve real SOL at entry, lower bound (late-stage only)
MAX_RQ_SOL = 85.0          # curve real SOL at entry, upper bound
MIN_AGE = 240              # slots since CreateEvent before an entry is allowed
FIRST_BUY_SOL = 0.04       # first buy of a position
ADD_BUY_SOL = 0.06         # each pyramid add
MAX_STAKE_SOL = 0.04       # most SOL into one position in total (single buy; no pyramid)
MIN_BUY_SOL = 0.01         # smallest buy worth sending
BUY_SLIP_BPS = 1200        # slippage on buys; sells always go out at 10000
ADD_STEP = 0.08            # add only once price is this far above last buy price
ADD_IF_UP = 0.02           # add only while position value >= cost * (1 + this)
ADD_NET_SOL = 0.30         # add only while net SOL over the entry window >= this
MAX_MAIN = 4               # most momentum positions open at once (1 slot kept for probes)
MAX_EXPOSURE_SOL = 3.0     # most SOL at cost across all positions at once
# --- exits ------------------------------------------------------------------
STOP_LOSS = 0.225          # sell when value < cost * (1 - STOP_LOSS)
TAKE_PROFIT = 0.30         # sell when value >= cost * (1 + TAKE_PROFIT)
PARTIAL_AT = 100.0         # bank half the tokens once value >= cost * (1 + this); 100.0 = off
TRAIL_ARM = 100.0          # trailing stop arms once value >= cost * (1 + TRAIL_ARM); 100.0 = off
TRAIL = 0.20               # ... then sell when value < peak * (1 - TRAIL)
FADE_WINDOW = 20           # slots for the fade flow check
FADE_NET_SOL = -1000.0    # exit when net SOL over FADE_WINDOW < this ... (off: never reached)
FADE_PULLBACK = 0.08       # ... and value is this far below its peak
BIG_SELL_SOL = 0.0         # exit on any single recorded sell >= this many SOL; 0 = off
BIG_SELL_IF_UP = 0.05      # ... but only while the position is up less than this much
MAX_HOLD = 900            # slots; time limit on a position
HOLD_IF_UP = 0.05          # time limit does not apply while up at least this much
COOLDOWN = 200             # slots to wait after an exit before re-entering a curve
MAX_ENTRIES = 1            # most entries per curve over the run (no re-entry: second entries lose)
# --- guardrail probes --------------------------------------------------------
TARGET_COINS = 25          # pace target: distinct coins with a round trip
TARGET_FILLS = 50          # pace target: own filled orders
PACE_SECS = 21_600         # 6h: the final window's length; pace is elapsed / this
PROBE_SOL = 0.012          # probe buy size (round trip stays >= 0.01 SOL)
MAX_PROBES = 1             # most probe round trips in flight at once
PROBE_MIN_BUYS = 2         # probe needs at least this many buys in its recent tape
PROBE_MIN_NET_SOL = 0.0    # ... and non-negative recent net flow
KEEP_SLOTS = 400           # how much tape to keep per curve (slots)


class Tape:
    """Rolling tape of one curve: (slot, is_buy, sol_lamports, user, price)."""

    __slots__ = ("created", "trades", "entries", "last_exit", "last_exit_won")

    def __init__(self):
        self.created = None      # slot of CreateEvent, None if unseen
        self.trades = []         # list of tuples, oldest first
        self.entries = 0         # momentum entries opened on this curve
        self.last_exit = -10**18  # slot of the last exit from this curve
        self.last_exit_won = False   # the last exit from this curve was a win


class Position:
    """Bookkeeping for one open momentum position."""

    __slots__ = ("opened", "peak", "invested", "exiting", "filled",
                 "last_buy_price", "last_value", "partial_taken",
                 "partial_pending", "tokens_before_partial", "exit_cost")

    def __init__(self, opened, invested, last_buy_price):
        self.opened = opened
        self.peak = 0            # best close value seen (lamports)
        self.invested = invested  # lamports sent in buys for this position
        self.exiting = False
        self.filled = False      # some buy of this position has filled
        self.last_buy_price = last_buy_price
        self.last_value = 0      # close value at the latest batch (lamports)
        self.partial_taken = False       # half the tokens already banked
        self.partial_pending = False     # partial sell sent, fill not yet seen
        self.tokens_before_partial = 0
        self.exit_cost = 0          # cost when the exit was decided (for re-entry gating)


# Insertion-ordered state only; nothing here is ever iterated as a set.
tapes = {}
held = {}
probes = {}          # mint -> False while buying, True once selling
my_coins = {}        # mint -> True, coins with at least one own fill
my_fills = 0
first_time = None    # block_time of the first event seen
last_time = None     # latest block_time seen


def tape_of(mint):
    t = tapes.get(mint)
    if t is None:
        t = Tape()
        tapes[mint] = t
    return t


def price_of(curve):
    vt = curve.virtual_token_reserves
    if vt <= 0:
        return 0.0
    return curve.virtual_quote_reserves / vt


def close_value_lamports(tokens, vq, vt, tx_fee):
    """Lamports a sale of `tokens` returns after the curve fee and tx fee."""
    if tokens <= 0 or vt <= 0:
        return 0
    gross = tokens * vq // (vt + tokens)
    return int(gross * (1.0 - CURVE_FEE)) - tx_fee


def net_flow(t, now, window):
    """Net SOL (buys minus sells, lamports) over the last `window` slots."""
    total = 0
    for i in range(len(t.trades) - 1, -1, -1):
        tr = t.trades[i]
        if tr[0] <= now - window:
            break
        total += tr[2] if tr[1] else -tr[2]
    return total


def surge(t, now, rq_sol):
    """True when the curve's recent tape is a fresh, broad-based buying surge."""
    if rq_sol < MIN_RQ_SOL or rq_sol > MAX_RQ_SOL:
        return False
    if t.created is None or now - t.created < MIN_AGE:
        return False
    start = now - ENTRY_WINDOW
    mid = now - ENTRY_WINDOW // 2
    buy_sol = 0
    sell_sol = 0
    buys = 0
    net_old = 0
    net_new = 0
    start_price = -1.0
    high = 0.0
    last_price = 0.0
    last_is_buy = False
    buyers = {}
    for tr in t.trades:
        slot, is_buy, sol, user, price = tr
        if slot <= start:
            start_price = price
            continue
        if start_price < 0:
            start_price = price
        signed = sol if is_buy else -sol
        if slot <= mid:
            net_old += signed
        else:
            net_new += signed
        if is_buy:
            buy_sol += sol
            buys += 1
            buyers[user] = buyers.get(user, 0) + sol
        else:
            sell_sol += sol
        if price > high:
            high = price
        last_price = price
        last_is_buy = is_buy
    if buys < MIN_BUYS or len(buyers) < MIN_BUYERS:
        return False
    if buy_sol - sell_sol < int(MIN_NET_SOL * LAMPORTS):
        return False
    if start_price <= 0 or last_price <= 0:
        return False
    up = last_price / start_price - 1.0
    if up < MIN_UP or up > MAX_UP:
        return False
    if last_price < high * NEAR_HIGH:
        return False
    if not last_is_buy:
        return False
    # The surge must still be building, not spent in the first half.
    if net_new <= 0 or net_new < net_old:
        return False
    top = 0
    for v in buyers.values():
        if v > top:
            top = v
    if buy_sol > 0 and top > MAX_BUYER_SHARE * buy_sol:
        return False
    return True


def probe_ok(t, now):
    """A probe still needs a real (if small) sign of life: recent buys, no net outflow."""
    buys = 0
    for i in range(len(t.trades) - 1, -1, -1):
        tr = t.trades[i]
        if tr[0] <= now - ENTRY_WINDOW:
            break
        if tr[1]:
            buys += 1
    if buys < PROBE_MIN_BUYS:
        return False
    return net_flow(t, now, ENTRY_WINDOW) >= int(PROBE_MIN_NET_SOL * LAMPORTS)


def on_batch(state, batch):
    global my_fills, first_time, last_time
    now = batch["slot"]
    caps = state.hello["caps"]
    tx = state.tx_fee_lamports()

    for f in batch["fills"]:
        if f["status"] == "filled" and not f["client_id"].startswith("_"):
            my_fills += 1
            my_coins[f["mint"]] = True

    # Fold this batch's events into the tapes; keep the traded mints in event order.
    traded = []
    seen = {}
    big_sell = {}
    for e in batch["events"]:
        bt = e.get("block_time")
        if bt is not None:
            if first_time is None:
                first_time = bt
            if last_time is None or bt > last_time:
                last_time = bt
        mint = e["mint"]
        if mint not in seen:
            seen[mint] = True
            traded.append(mint)
        kind = e["event"]
        if kind == "CreateEvent":
            tape_of(mint).created = e["slot"]
        elif kind == "TradeEvent":
            t = tape_of(mint)
            vt = int(e["virtual_token_reserves"])
            price = int(e["virtual_quote_reserves"]) / vt if vt > 0 else 0.0
            t.trades.append((e["slot"], e["is_buy"], int(e["sol_amount"]), e["user"], price))
            if not e["is_buy"] and BIG_SELL_SOL > 0 and int(e["sol_amount"]) >= int(BIG_SELL_SOL * LAMPORTS):
                big_sell[mint] = True
        else:  # CompleteEvent
            tape_of(mint)
    for mint in traded:
        t = tapes[mint]
        cut = 0
        while cut < len(t.trades) and t.trades[cut][0] <= now - KEEP_SLOTS:
            cut += 1
        if cut:
            del t.trades[:cut]

    marks = {}
    for m in batch["marks"]:
        marks[m["mint"]] = m

    pending_by_mint = {}
    for po in state.pending.values():
        pending_by_mint[po.order["mint"]] = pending_by_mint.get(po.order["mint"], 0) + 1

    orders = []
    max_orders = caps["max_orders_per_batch"]

    # --- exits first ---------------------------------------------------------
    for mint in list(held.keys()):
        h = held[mint]
        pos = state.positions.get(mint)
        curve = state.curves.get(mint)
        if pos is None:
            if mint not in pending_by_mint:
                del held[mint]
                t = tape_of(mint)
                if h.filled:
                    t.last_exit = now
                    t.last_exit_won = h.exit_cost > 0 and h.last_value >= h.exit_cost
                else:
                    t.entries -= 1
            continue
        h.filled = True
        if curve is None or curve.complete:
            continue
        mk = marks.get(mint)
        vq = int(mk["virtual_quote_reserves"]) if mk else curve.virtual_quote_reserves
        vt = int(mk["virtual_token_reserves"]) if mk else curve.virtual_token_reserves
        rq_sol = (int(mk["real_quote_reserves"]) if mk else curve.real_quote_reserves) / LAMPORTS
        tokens = pos.token_amount
        value = close_value_lamports(tokens, vq, vt, tx)
        cost = pos.cost_lamports
        # A partial sale just landed: re-base the peak and invested on the remainder.
        if h.partial_pending and tokens < h.tokens_before_partial:
            h.peak = value
            h.invested = cost
            h.partial_pending = False
        if value > h.peak:
            h.peak = value
        h.last_value = value
        t = tape_of(mint)
        # Bank half the tokens at the first modest gain; let the rest ride.
        if (not h.partial_taken and not h.partial_pending and not h.exiting
                and tokens >= 2 and value >= cost * (1.0 + PARTIAL_AT)
                and mint not in pending_by_mint and len(orders) < max_orders):
            orders.append({"side": "sell", "mint": mint, "token_amount": tokens // 2,
                           "max_slippage_bps": BPS})
            h.partial_taken = True
            h.partial_pending = True
            h.tokens_before_partial = tokens
            continue
        exit_now = h.exiting
        if not exit_now and value >= cost * (1.0 + TAKE_PROFIT):
            exit_now = True
        if not exit_now and value < cost * (1.0 - STOP_LOSS):
            exit_now = True
        if not exit_now and h.peak >= cost * (1.0 + TRAIL_ARM) and value < h.peak * (1.0 - TRAIL):
            exit_now = True
        if not exit_now and mint in big_sell and value < cost * (1.0 + BIG_SELL_IF_UP):
            exit_now = True
        if not exit_now and value < h.peak * (1.0 - FADE_PULLBACK) and net_flow(t, now, FADE_WINDOW) < int(FADE_NET_SOL * LAMPORTS):
            exit_now = True
        if not exit_now and now - h.opened > MAX_HOLD and value < cost * (1.0 + HOLD_IF_UP):
            exit_now = True
        if not exit_now:
            continue
        if not h.exiting:
            h.exit_cost = cost
        h.exiting = True
        if mint in pending_by_mint:
            sell_pending = False
            for po in state.pending.values():
                if po.order["mint"] == mint and po.order["side"] == "sell":
                    sell_pending = True
                    break
            if sell_pending:
                continue
        if len(orders) >= max_orders:
            break
        orders.append({"side": "sell", "mint": mint, "token_amount": tokens,
                       "max_slippage_bps": BPS})

    # --- probe sells: dump the probe as soon as its buy has landed -----------
    for mint in list(probes.keys()):
        pos = state.positions.get(mint)
        curve = state.curves.get(mint)
        if pos is None:
            if mint not in pending_by_mint:
                del probes[mint]
            continue
        if curve is None or curve.complete or mint in pending_by_mint:
            continue
        if len(orders) >= max_orders:
            break
        orders.append({"side": "sell", "mint": mint, "token_amount": pos.token_amount,
                       "max_slippage_bps": BPS})
        probes[mint] = True

    # --- entries and pyramid adds --------------------------------------------
    free = state.free_cash_lamports()
    slots_left = caps["max_open_positions"] - state.committed_position_count()
    main_left = MAX_MAIN - len(held)
    exposure = 0
    for mint in held:
        h = held[mint]
        pos = state.positions.get(mint)
        exposure += max(h.invested, pos.cost_lamports if pos is not None else 0)
    for mint in traded:
        if len(orders) >= max_orders:
            break
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            continue
        if mint in pending_by_mint or mint in probes:
            continue
        t = tapes[mint]
        h = held.get(mint)
        price = price_of(curve)
        rq_sol = curve.real_quote_reserves / LAMPORTS
        if h is None:
            if slots_left <= 0 or main_left <= 0:
                continue
            if (t.entries >= MAX_ENTRIES or now - t.last_exit < COOLDOWN
                    or (t.entries >= 1 and not t.last_exit_won)):
                continue
            if not surge(t, now, rq_sol):
                continue
            want = int(FIRST_BUY_SOL * LAMPORTS)
        else:
            if h.exiting or not h.filled:
                continue
            if h.invested >= int(MAX_STAKE_SOL * LAMPORTS) - int(MIN_BUY_SOL * LAMPORTS):
                continue
            pos = state.positions.get(mint)
            if pos is None:
                continue
            if price < h.last_buy_price * (1.0 + ADD_STEP):
                continue
            if h.last_value < pos.cost_lamports * (1.0 + ADD_IF_UP):
                continue
            if net_flow(t, now, ENTRY_WINDOW) < int(ADD_NET_SOL * LAMPORTS):
                continue
            if rq_sol > MAX_RQ_SOL:
                continue
            want = min(int(ADD_BUY_SOL * LAMPORTS), int(MAX_STAKE_SOL * LAMPORTS) - h.invested)
        cap = state.max_buy_lamports(mint)
        amount = min(cap, want, int(MAX_EXPOSURE_SOL * LAMPORTS) - exposure)
        if amount > free - tx:
            amount = free - tx
        if amount < int(MIN_BUY_SOL * LAMPORTS):
            continue
        orders.append({"side": "buy", "mint": mint, "quote_lamports": amount,
                       "max_slippage_bps": BUY_SLIP_BPS})
        free -= amount + tx
        exposure += amount
        if h is None:
            held[mint] = Position(now, amount, price)
            t.entries += 1
            slots_left -= 1
            main_left -= 1
        else:
            h.invested += amount
            h.last_buy_price = price

    # --- guardrail pacing: probe when fills or coins lag the pace ------------
    if len(probes) < MAX_PROBES and slots_left > 0 and first_time is not None and last_time is not None:
        pace = min(1.0, (last_time - first_time) / PACE_SECS)
        need_coins = len(my_coins) < math.ceil(TARGET_COINS * pace)
        need_fills = my_fills < math.ceil(TARGET_FILLS * pace)
        if need_coins or need_fills:
            min_rq = PROBE_SOL * BPS / caps["max_order_bps_of_real_reserve"]
            for mint in traded:
                if len(orders) >= max_orders or len(probes) >= MAX_PROBES:
                    break
                if mint in held or mint in probes or mint in pending_by_mint:
                    continue
                if mint in state.positions:
                    continue
                if need_coins and not need_fills and mint in my_coins:
                    continue
                curve = state.curves.get(mint)
                if curve is None or curve.complete:
                    continue
                if curve.real_quote_reserves / LAMPORTS < min_rq * 1.5:
                    continue
                if not probe_ok(tapes[mint], now):
                    continue
                if free < int(PROBE_SOL * LAMPORTS) + 2 * tx:
                    break
                orders.append({"side": "buy", "mint": mint,
                               "quote_lamports": int(PROBE_SOL * LAMPORTS),
                               "max_slippage_bps": BUY_SLIP_BPS})
                probes[mint] = False
                free -= int(PROBE_SOL * LAMPORTS) + tx
    return orders


if __name__ == "__main__":
    makefun_bot.run(on_batch)
