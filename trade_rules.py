import re
from datetime import datetime, timedelta
from utils import safe_print

def is_within_trading_hours(dt_ist):
    """Checks if the given datetime in IST is within active trading hours (09:15 - 15:30)."""
    trading_start = dt_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    trading_end = dt_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    return trading_start <= dt_ist <= trading_end

def is_signal_valid_for_today(bar2_time_str, now_date_str):
    """
    Safety check: Scraper signal date must match today's date (e.g. '03 Aug').
    Returns True if valid, False otherwise.
    """
    if not bar2_time_str:
        return False
    sig_date_match = re.search(r'\b\d{1,2}\s+[A-Za-z]{3}\b', bar2_time_str)
    sig_date_str = sig_date_match.group(0) if sig_date_match else ""
    return sig_date_str == now_date_str

def extract_target_times(bar2_time_str, bar3_time_str):
    """
    Extracts target times (HH:MM) and computes range close times (+5 mins).
    Returns (b2_t, b2_close_t, b3_t, b3_close_t) or (None, None, None, None) on error.
    """
    try:
        b2_match = re.search(r'\b\d{2}:\d{2}\b', bar2_time_str)
        b3_match = re.search(r'\b\d{2}:\d{2}\b', bar3_time_str)
        if not b2_match or not b3_match:
            return None, None, None, None
            
        b2_t = b2_match.group(0)
        b3_t = b3_match.group(0)
        
        b2_dt = datetime.strptime(b2_t, "%H:%M")
        b2_close_t = (b2_dt + timedelta(minutes=5)).strftime("%H:%M")
        
        b3_dt = datetime.strptime(b3_t, "%H:%M")
        b3_close_t = (b3_dt + timedelta(minutes=5)).strftime("%H:%M")
        
        return b2_t, b2_close_t, b3_t, b3_close_t
    except Exception:
        return None, None, None, None

def check_stop_loss_on_5m_close(dhan, index_sec_id, stop_loss_price, position):
    """
    Queries historical candles, resamples to 5-minute intervals,
    and checks if the latest completed 5-minute candle closed beyond the stop loss level.
    Returns (is_sl_crossed, close_val).
    """
    if stop_loss_price is None or dhan is None or index_sec_id is None:
        return False, None
        
    from datetime import datetime
    from utils import IST
    from broker import get_intraday_candles
    
    query_date = datetime.now(IST).strftime("%Y-%m-%d")
    df = get_intraday_candles(dhan, index_sec_id, "IDX_I", "INDEX", query_date, query_date)
    if df is not None and not df.empty:
        try:
            df_copy = df.copy()
            df_copy.set_index('time', inplace=True)
            df_5m = df_copy.resample('5Min', closed='left', label='left').agg({
                'close': 'last'
            }).dropna()
            
            if not df_5m.empty:
                latest_close = float(df_5m.iloc[-1]['close'])
                if position == "LONG" and latest_close < stop_loss_price:
                    return True, latest_close
                elif position == "SHORT" and latest_close > stop_loss_price:
                    return True, latest_close
        except Exception as e:
            safe_print(f"Error evaluating 5m candles for Stop Loss: {e}")
            
    return False, None

def evaluate_exit_conditions(state, ltp, now_time_str, b3_t, dhan=None, index_sec_id=None, option_ltp=None):
    """
    Checks exit conditions in priority order:
      1. Max Capital Risk (live option P&L vs MAX_LOSS_PER_TRADE) — real-time premium basis.
      2. Target hit — real-time index LTP basis.
      3. Time exits — Cycle Reset (b3_t) and Universal (15:25).
      4. Index Stop Loss — 5-min candle close basis (or LTP fallback).
    Returns (should_exit, exit_reason)
    """
    from sizing import get_max_loss_per_trade, MAX_LOSS_BREACH_THRESHOLD

    position = state.get("Position")
    stop_loss = state.get("StopLossPrice")
    target = state.get("TargetPrice")

    # 1. Max Capital Risk check (highest priority)
    #    Uses live option LTP to compute real P&L including theta decay.
    #    Requires MAX_LOSS_BREACH_THRESHOLD consecutive evaluations below the
    #    threshold before triggering an exit — single tick spikes are ignored.
    if option_ltp is not None:
        entry_price = state.get("EntryPrice", 0.0) or 0.0
        quantity = state.get("Quantity", 0) or 0
        if entry_price > 0 and quantity > 0:
            current_pnl = (option_ltp - entry_price) * quantity
            max_loss = get_max_loss_per_trade()
            if current_pnl <= -max_loss:
                breach_count = state.get("MaxLossBreachCount", 0) + 1
                state["MaxLossBreachCount"] = breach_count
                if breach_count >= MAX_LOSS_BREACH_THRESHOLD:
                    return True, (
                        f"Max Capital Risk breached: live P&L ₹{current_pnl:.2f} "
                        f"<= -₹{max_loss:.2f} "
                        f"(entry ₹{entry_price:.2f}, LTP ₹{option_ltp:.2f}, qty {quantity})"
                    )
                # Not yet confirmed — log but don't exit
                safe_print(
                    f"  [MAX LOSS GUARD] Breach {breach_count}/{MAX_LOSS_BREACH_THRESHOLD}: "
                    f"P&L ₹{current_pnl:.2f} below threshold. Awaiting confirmation..."
                )
            else:
                # P&L has recovered — reset breach counter
                if state.get("MaxLossBreachCount", 0) > 0:
                    state["MaxLossBreachCount"] = 0

    # 2. Target check (real-time index LTP basis)
    if position == "LONG":
        if target is not None and ltp >= target:
            return True, f"Index Target crossed ({ltp:.2f} >= {target:.2f})"
    elif position == "SHORT":
        if target is not None and ltp <= target:
            return True, f"Index Target crossed ({ltp:.2f} <= {target:.2f})"

    # 3. Time exits
    # Only close due to cycle reset (b3_t) if the position was entered during the morning cycle (before b3_t)
    if b3_t and now_time_str >= b3_t:
        trigger_time = state.get("TriggerCandleStamp")
        is_morning_position = True
        if trigger_time:
            try:
                if trigger_time >= b3_t:
                    is_morning_position = False
            except Exception:
                pass
        if is_morning_position:
            return True, f"Cycle Reset exit reached ({b3_t})"

    if now_time_str >= "15:25":
        return True, "Universal Time Exit reached (15:25)"

    # 4. Index Stop Loss check (5-min candle close basis if possible)
    if stop_loss is not None:
        if dhan is not None and index_sec_id is not None:
            is_sl_crossed, close_val = check_stop_loss_on_5m_close(dhan, index_sec_id, stop_loss, position)
            if is_sl_crossed:
                direction = "less than" if position == "LONG" else "greater than"
                return True, f"Index Stop Loss crossed on 5-min candle close ({close_val:.2f} is {direction} SL {stop_loss:.2f})"
        else:
            # Fallback to real-time LTP if Dhan context is missing
            if position == "LONG" and ltp < stop_loss:
                return True, f"Index Stop Loss crossed on LTP fallback ({ltp:.2f} < {stop_loss:.2f})"
            elif position == "SHORT" and ltp > stop_loss:
                return True, f"Index Stop Loss crossed on LTP fallback ({ltp:.2f} > {stop_loss:.2f})"

    return False, ""

def get_sleep_interval(position, now_ist):
    """
    Calculates sleep duration based on active position status.
    - If active position exists: sleep to start of next minute.
    - If no active position: sleep to boundary of next 5-minute candle.
    Returns (sleep_seconds, next_time)
    """
    if position != "NONE":
        sleep_sec = 60 - now_ist.second
        # Avoid 0 seconds sleep if we hit exactly the boundary
        if sleep_sec <= 0:
            sleep_sec = 60
        next_time = now_ist + timedelta(seconds=sleep_sec)
        return float(sleep_sec), next_time
    else:
        next_minute = ((now_ist.minute // 5) + 1) * 5
        target_hour = now_ist.hour
        if next_minute >= 60:
            next_minute = 0
            target_hour += 1
            
        next_time = now_ist.replace(hour=target_hour, minute=next_minute, second=0, microsecond=0)
        sleep_sec = (next_time - now_ist).total_seconds()
        if sleep_sec <= 0:
            sleep_sec = 300
            next_time = now_ist + timedelta(minutes=5)
        return float(sleep_sec), next_time

def calculate_trade_levels(operator, trigger_candle_level, time_candle_opposite_level):
    """
    Determines Stop Loss and 5 Target levels dynamically.
    - For Buy on Dip (LONG / CE side):
      - Entry = TriggerCandleHigh
      - Stop Loss = TimeCandleLow (active_low)
      - Target = Entry + (i * risk) for i from 1 to 5
    - For Sell on Rise (SHORT / PE side):
      - Entry = TriggerCandleLow
      - Stop Loss = TimeCandleHigh (active_high)
      - Target = Entry - (i * risk) for i from 1 to 5
    Returns (stop_loss, [T1, T2, T3, T4, T5])
    """
    if operator == "Buy on Dip":
        entry = trigger_candle_level
        stop_loss = time_candle_opposite_level
        risk = entry - stop_loss
        targets = [entry + (1.0 * i) * risk for i in range(1, 6)]
        return stop_loss, targets
    else:
        entry = trigger_candle_level
        stop_loss = time_candle_opposite_level
        risk = stop_loss - entry
        targets = [entry - (1.0 * i) * risk for i in range(1, 6)]
        return stop_loss, targets

def check_and_update_trailing_sl(state, dhan, index_sec_id, position):
    """
    Checks the last completed 5-minute candle close.
    If it closed beyond any target level (T1 to T4), trails the Stop Loss:
    - If T1 is hit: trails SL to Entry.
    - If T2 is hit: trails SL to T1, and so on.
    Returns True if the state was updated.
    """
    targets = state.get("Targets")
    if not targets or len(targets) < 5 or dhan is None or index_sec_id is None:
        return False
        
    active_idx = state.get("ActiveTargetIndex", 0)
    if active_idx >= 4: # Already on T5, cannot trail further
        return False
        
    entry_idx_price = state.get("EntryIndexPrice", 0.0) or state.get("EntryPrice", 0.0)
    if entry_idx_price == 0.0:
        return False
        
    from datetime import datetime
    from utils import IST, safe_print
    from broker import get_intraday_candles
    
    query_date = datetime.now(IST).strftime("%Y-%m-%d")
    df = get_intraday_candles(dhan, index_sec_id, "IDX_I", "INDEX", query_date, query_date)
    if df is not None and not df.empty:
        try:
            df_copy = df.copy()
            df_copy.set_index('time', inplace=True)
            df_5m = df_copy.resample('5Min', closed='left', label='left').agg({
                'close': 'last'
            }).dropna()
            
            if not df_5m.empty:
                latest_close = float(df_5m.iloc[-1]['close'])
                
                new_active_idx = active_idx
                for i in range(active_idx, 4): # Check T1 to T4
                    target_val = float(targets[i])
                    if position == "LONG":
                        if latest_close >= target_val:
                            new_active_idx = i + 1
                    elif position == "SHORT":
                        if latest_close <= target_val:
                            new_active_idx = i + 1
                            
                if new_active_idx > active_idx:
                    # Compute the original risk from the first target relative to entry.
                    # This is used to apply a buffer so a tiny retracement does not
                    # stop out the position right after a target is hit.
                    from sizing import TM_TRAIL_BUFFER_RATIO
                    original_risk = abs(entry_idx_price - float(targets[0]))
                    buffer = original_risk * TM_TRAIL_BUFFER_RATIO

                    # Determine the base SL level (entry or previous target)
                    if new_active_idx == 1:
                        base_sl = entry_idx_price
                    else:
                        base_sl = float(targets[new_active_idx - 2])

                    # Apply buffer in the direction that gives extra room before stopping out
                    if position == "SHORT":
                        # SHORT: price must close ABOVE new_sl to stop out.
                        # Push new_sl higher (further into loss zone) to absorb noise.
                        new_sl = base_sl + buffer
                    else:  # LONG
                        # LONG: price must close BELOW new_sl to stop out.
                        # Push new_sl lower (further into loss zone) to absorb noise.
                        new_sl = base_sl - buffer
                    
                    # Ensure the new stop loss is not already violated by the current candle close
                    can_trail = False
                    if position == "LONG" and latest_close > new_sl:
                        can_trail = True
                    elif position == "SHORT" and latest_close < new_sl:
                        can_trail = True
                        
                    if can_trail:
                        state["ActiveTargetIndex"] = new_active_idx
                        state["StopLossPrice"] = new_sl
                        
                        safe_print(f"\n[TRAILING SL ACTION] 5-min candle closed at {latest_close:.2f} beyond T{new_active_idx}.")
                        safe_print(f"  • SL shifted to:     {new_sl:.2f}  (base {base_sl:.2f} + buffer {buffer:.2f})")
                        return True
        except Exception as e:
            safe_print(f"Error evaluating 5m candles for trailing SL: {e}")
            
    return False
