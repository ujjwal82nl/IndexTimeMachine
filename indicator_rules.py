"""
indicator_rules.py
==================
Implements the Indicator-Based Trading Strategy (parallel to TIME MACHINE).

Strategy logic:
  - Signal source: same scrap_<Underlying>.json file (last entry).
  - Only signals with valid (non-"--") ENTRY and SL values are acted on.
  - Entry:
      Buy on Dip   -> Enter CE (LONG)  when real-time index LTP >= ENTRY price
      Sell on Rise -> Enter PE (SHORT) when real-time index LTP <= ENTRY price
  - Strike: ATM using get_atm_itm_strike on the ENTRY price.
  - Stop Loss: from signal's SL field (index level), checked real-time on LTP.
  - Targets: T1..T5 using same formula as TIME MACHINE
      T_i = Entry +/- (2 x i x |Entry - SL|)
  - Trailing SL: same logic as existing strategy (checked on 5-min candle close).
  - Time exit: universal exit at 15:25 IST.
  - ON/OFF: controlled by "indicator_strategy_enabled" flag in .env.
"""

import os
import re
import json
from datetime import datetime
from utils import IST, safe_print, load_trade_state


def get_default_indicator_state(underlying):
    """Returns a clean default state dict for the Indicator Strategy session."""
    return {
        "Date": datetime.now(IST).strftime("%Y-%m-%d"),
        "Underlying": underlying,
        "Strategy": "INDICATOR",
        "ExpiryDate": None,
        "Position": "NONE",
        "OptionSymbol": None,
        "SecurityId": None,
        "Quantity": 0,
        "EntryPrice": 0.0,
        "EntryIndexPrice": 0.0,
        "StopLossPrice": None,
        "TargetPrice": None,
        "Targets": None,
        "ActiveTargetIndex": 0,
        "LastSignalTime": None,
        "SignalEntry": None,
        "SignalSL": None,
        "Operator": None,
        "Strike": None,
        "OptionType": None,
        "TradedSignalTime": None,
    }


def load_indicator_state(state_file, underlying):
    """
    Loads today's saved Indicator Strategy state from disk.
    Returns state dict if today's date matches, else a fresh default state.
    """
    state = load_trade_state(state_file, underlying)
    if state:
        # Forward-compat: ensure all current default fields exist
        default = get_default_indicator_state(underlying)
        for k, v in default.items():
            if k not in state:
                state[k] = v
        safe_print("\n" + "="*50)
        safe_print("     [INDICATOR] RECOVERED STATE DETECTED")
        safe_print("="*50)
        safe_print(f"  * Position:        {state.get('Position')}")
        safe_print(f"  * Option Contract: {state.get('OptionSymbol')}")
        safe_print(f"  * Entry Premium:   {state.get('EntryPrice')}")
        safe_print(f"  * Entry Index:     {state.get('EntryIndexPrice')}")
        safe_print(f"  * Signal ENTRY:    {state.get('SignalEntry')}")
        safe_print(f"  * Stop Loss (Idx): {state.get('StopLossPrice')}")
        safe_print(f"  * Target (Idx):    {state.get('TargetPrice')}")
        safe_print("="*50 + "\n")
        return state
    return get_default_indicator_state(underlying)


def _parse_numeric(val_str):
    """Safely parses a numeric string field; returns float or None if '--' / invalid."""
    if val_str is None or str(val_str).strip() in ("--", "", "OPEN"):
        return None
    try:
        return float(re.sub(r'[^0-9.]', '', str(val_str)))
    except Exception:
        return None


def check_indicator_signal(scrape_file, ind_state, target_expiry):
    """
    Reads the scraper JSON. If a new signal timestamp is found with valid ENTRY and SL
    values, resets/updates the indicator state and loads the new signal parameters.
    Returns True if a new valid signal was detected and loaded.
    """
    if not os.path.exists(scrape_file):
        return False

    try:
        with open(scrape_file, "r") as f:
            data = json.load(f)
        if not data:
            return False

        last_entry = data[-1]
        signal_timestamp = last_entry.get("Timestamp")

        if signal_timestamp == ind_state.get("LastSignalTime"):
            return False  # No new signal

        # New signal found -- try to parse ENTRY and SL
        entry_price = _parse_numeric(last_entry.get("ENTRY"))
        sl_price = _parse_numeric(last_entry.get("SL"))
        operator = last_entry.get("Operator", "")

        if entry_price is None or sl_price is None:
            safe_print(
                f"[INDICATOR] New signal (ts={signal_timestamp}) but ENTRY/SL are '--'. "
                "Waiting for values to be populated."
            )
            # Record timestamp to avoid repeated warnings each loop
            ind_state["LastSignalTime"] = signal_timestamp
            ind_state["Operator"] = operator
            ind_state["SignalEntry"] = None
            ind_state["SignalSL"] = None
            return False

        # Calculate 5 target levels using the same formula as TIME MACHINE
        from trade_rules import calculate_trade_levels
        _sl_calc, targets = calculate_trade_levels(operator, entry_price, sl_price)

        # Determine new calendar day vs same-day update
        new_date_str = signal_timestamp.split(" ")[0] if signal_timestamp else ""
        old_date_str = (
            ind_state.get("LastSignalTime", "").split(" ")[0]
            if ind_state.get("LastSignalTime") else ""
        )

        if new_date_str != old_date_str:
            # New calendar day -> full reset of position state
            ind_state.update({
                "Date": datetime.now(IST).strftime("%Y-%m-%d"),
                "ExpiryDate": target_expiry.strftime("%Y-%m-%d") if target_expiry else None,
                "Position": "NONE",
                "OptionSymbol": None,
                "SecurityId": None,
                "Quantity": 0,
                "EntryPrice": 0.0,
                "EntryIndexPrice": 0.0,
                "Strike": None,
                "OptionType": None,
                "TradedSignalTime": None,
            })
        else:
            # Same-day update: do NOT reset an active position
            if target_expiry:
                ind_state["ExpiryDate"] = target_expiry.strftime("%Y-%m-%d")

        # Always update signal parameters
        ind_state.update({
            "LastSignalTime": signal_timestamp,
            "Operator": operator,
            "SignalEntry": entry_price,
            "SignalSL": sl_price,
            "StopLossPrice": sl_price,
            "TargetPrice": targets[4],  # T5 is final exit target
            "Targets": targets,
            "ActiveTargetIndex": 0,
        })

        safe_print(
            f"\n[INDICATOR] New signal loaded | ts={signal_timestamp} | "
            f"Operator={operator} | ENTRY={entry_price} | SL={sl_price}"
        )
        safe_print(
            f"  * T1={targets[0]:.2f} | T2={targets[1]:.2f} | T3={targets[2]:.2f} | "
            f"T4={targets[3]:.2f} | T5={targets[4]:.2f}"
        )
        return True

    except Exception as e:
        safe_print(f"[INDICATOR] Error checking scrape file: {e}")
        return False


def evaluate_indicator_entry(ind_state, ltp, now_time_str):
    """
    Checks whether the Indicator Strategy should enter a position.
    Entry is triggered on real-time LTP crossing the signal's ENTRY price.
      Buy on Dip   -> enter CE when ltp >= SignalEntry
      Sell on Rise -> enter PE when ltp <= SignalEntry

    Returns (should_enter: bool, option_type: str or None)
    """
    if ind_state.get("Position") != "NONE":
        return False, None
    if now_time_str >= "15:15":
        return False, None

    last_sig_time = ind_state.get("LastSignalTime")
    traded_sig_time = ind_state.get("TradedSignalTime")
    if last_sig_time is None or last_sig_time == traded_sig_time:
        return False, None

    entry_price = ind_state.get("SignalEntry")
    operator = ind_state.get("Operator")

    if entry_price is None or not operator:
        return False, None

    if operator == "Buy on Dip":
        if ltp >= entry_price:
            return True, "CE"
    elif operator == "Sell on Rise":
        if ltp <= entry_price:
            return True, "PE"

    return False, None


def evaluate_indicator_exit(ind_state, ltp, now_time_str, dhan=None, index_sec_id=None, option_ltp=None):
    """
    Checks exit conditions for the Indicator Strategy position in priority order:
      1. Max Capital Risk (live option P&L vs MAX_LOSS_PER_TRADE) — real-time premium basis.
      2. Target hit — real-time LTP basis (T5 level).
      3. Universal time exit at 15:25 IST.
      4. Index Stop Loss — 5-min candle close basis (or LTP fallback).
    Returns (should_exit: bool, reason: str)
    """
    from sizing import get_max_loss_per_trade

    position = ind_state.get("Position")
    stop_loss = ind_state.get("StopLossPrice")
    target = ind_state.get("TargetPrice")

    # 1. Max Capital Risk check (highest priority)
    #    Uses live option LTP to compute real P&L including theta decay.
    #    Triggers an immediate exit if the current loss >= max_loss.
    if option_ltp is not None:
        entry_price = ind_state.get("EntryPrice", 0.0) or 0.0
        quantity = ind_state.get("Quantity", 0) or 0
        if entry_price > 0 and quantity > 0:
            current_pnl = (option_ltp - entry_price) * quantity
            max_loss = get_max_loss_per_trade()
            if current_pnl <= -max_loss:
                return True, (
                    f"[INDICATOR] Max Capital Risk breached: live P&L ₹{current_pnl:.2f} "
                    f"<= -₹{max_loss:.2f} "
                    f"(entry ₹{entry_price:.2f}, LTP ₹{option_ltp:.2f}, qty {quantity})"
                )

    # 2. Target check (real-time LTP)
    if position == "LONG":
        if target is not None and ltp >= target:
            return True, f"[INDICATOR] Target reached ({ltp:.2f} >= {target:.2f})"
    elif position == "SHORT":
        if target is not None and ltp <= target:
            return True, f"[INDICATOR] Target reached ({ltp:.2f} <= {target:.2f})"

    # 3. Universal time exit
    if now_time_str >= "15:25":
        return True, "[INDICATOR] Universal Time Exit reached (15:25)"

    # 4. Stop Loss check (5-min candle close basis if possible, else LTP fallback)
    if stop_loss is not None:
        if dhan is not None and index_sec_id is not None:
            from trade_rules import check_stop_loss_on_5m_close
            is_sl_crossed, close_val = check_stop_loss_on_5m_close(dhan, index_sec_id, stop_loss, position)
            if is_sl_crossed:
                direction = "less than" if position == "LONG" else "greater than"
                return True, (
                    f"[INDICATOR] Stop Loss crossed on 5-min candle close "
                    f"({close_val:.2f} is {direction} SL {stop_loss:.2f})"
                )
        else:
            # Fallback: real-time LTP
            if position == "LONG" and ltp < stop_loss:
                return True, f"[INDICATOR] Stop Loss crossed on LTP ({ltp:.2f} < SL {stop_loss:.2f})"
            elif position == "SHORT" and ltp > stop_loss:
                return True, f"[INDICATOR] Stop Loss crossed on LTP ({ltp:.2f} > SL {stop_loss:.2f})"

    return False, ""
