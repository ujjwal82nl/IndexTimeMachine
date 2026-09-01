import re
from datetime import datetime
from utils import safe_print, get_index_metadata, IST
from broker import (
    get_5min_candle_range, get_atm_itm_strike, find_option_security_id,
    get_option_price, execute_dhan_order
)
from sizing import calculate_order_qty

def update_range_candles(dhan, index_sec_id, state, b2_t, b2_close_t, b3_t, b3_close_t, now_time_str, target_expiry_date, underlying):
    """
    Checks target times and updates morning / afternoon high-low ranges using historical Dhan candle logs.
    Returns True if state was modified.
    """
    state_changed = False
    
    # 1. Update Morning Range
    if state.get("MorningHigh") is None and now_time_str >= b2_close_t:
        bar2_time_str = state.get("Bar2Time")
        if bar2_time_str:
            morning_high, morning_low = get_5min_candle_range(dhan, index_sec_id, bar2_time_str)
            if morning_high is not None:
                state["MorningHigh"] = morning_high
                state["MorningLow"] = morning_low
                state_changed = True
                
                ce_strike = get_atm_itm_strike(underlying, morning_high, "CE")
                pe_strike = get_atm_itm_strike(underlying, morning_low, "PE")
                safe_print(f"\n[RANGE SET] Morning Candle ({b2_t}) Range set:")
                safe_print(f"  • High: {morning_high} -> ATM/ITM CE Strike: {underlying} {target_expiry_date.strftime('%d-%b-%Y')} {ce_strike} CALL")
                safe_print(f"  • Low:  {morning_low} -> ATM/ITM PE Strike: {underlying} {target_expiry_date.strftime('%d-%b-%Y')} {pe_strike} PUT")

    # 2. Update Afternoon Range
    if state.get("AfternoonHigh") is None and now_time_str >= b3_close_t:
        bar3_time_str = state.get("Bar3Time")
        if bar3_time_str:
            afternoon_high, afternoon_low = get_5min_candle_range(dhan, index_sec_id, bar3_time_str)
            if afternoon_high is not None:
                state["AfternoonHigh"] = afternoon_high
                state["AfternoonLow"] = afternoon_low
                state_changed = True
                
                ce_strike = get_atm_itm_strike(underlying, afternoon_high, "CE")
                pe_strike = get_atm_itm_strike(underlying, afternoon_low, "PE")
                safe_print(f"\n[RANGE SET] Afternoon Candle ({b3_t}) Range set:")
                safe_print(f"  • High: {afternoon_high} -> ATM/ITM CE Strike: {underlying} {target_expiry_date.strftime('%d-%b-%Y')} {ce_strike} CALL")
                safe_print(f"  • Low:  {afternoon_low} -> ATM/ITM PE Strike: {underlying} {target_expiry_date.strftime('%d-%b-%Y')} {pe_strike} PUT")

    return state_changed

def execute_entry_trade(dhan, underlying, state, option_type, index_price, client_code, feed, target_expiry_date, active_range_val, opposite_range_val, current_option_lt, strategy_tag="TIME_MACHINE"):
    """
    Finds correct strike option contract, checks margin budgeting, places live Buy order,
    and updates/saves trade position state. Subscribes feed to option tick updates.
    strategy_tag is written to trades.json and Telegram for strategy comparison.
    Returns True if successfully executed.
    """
    strike = get_atm_itm_strike(underlying, active_range_val, option_type)
    opt_id, opt_sym = find_option_security_id(underlying, target_expiry_date, strike, option_type)
    
    if not opt_id:
        return False
        
    # Get current option premium to size the trade
    opt_price = current_option_lt if current_option_lt is not None else get_option_price(dhan, opt_id, underlying)
    
    if not opt_price:
        safe_print(f"[ERROR] Could not fetch premium price for option contract: {opt_sym}")
        return False
        
    qty = calculate_order_qty(dhan, underlying, opt_price, active_range_val, opposite_range_val)
    if not qty:
        return False
        
    extra_details = {
        "Operator": state.get("Operator"),
        "StopLossPrice": state.get("StopLossPrice") or state.get("SignalSL"),
        "TargetPrice": state.get("TargetPrice") or (state.get("Targets")[-1] if state.get("Targets") else None),
        "Targets": state.get("Targets")
    }
    if "MorningHigh" in state:
        extra_details.update({
            "MorningRange": [state.get("MorningHigh"), state.get("MorningLow")],
            "AfternoonRange": [state.get("AfternoonHigh"), state.get("AfternoonLow")],
            "TriggerCandle": {
                "Stamp": state.get("TriggerCandleStamp"),
                "High": state.get("TriggerCandleHigh"),
                "Low": state.get("TriggerCandleLow")
            }
        })
    else:
        extra_details.update({
            "SignalEntry": state.get("SignalEntry"),
            "SignalSL": state.get("SignalSL")
        })

    success, sym, sec_id = execute_dhan_order(
        dhan, underlying, target_expiry_date, strike, option_type, "BUY", index_price, client_code, qty,
        strategy_tag=strategy_tag, extra_details=extra_details
    )
    
    if success:
        # Fallback Stop Loss is the opposite side of the active range (Range High/Low)
        stop_loss_idx = state.get("StopLossPrice")
        if stop_loss_idx is None:
            stop_loss_idx = opposite_range_val
            safe_print(f"  [STOPLOSS FALLBACK SET] Stop Loss index trigger: {stop_loss_idx}")
            
        entry_time = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        
        # Update local state dictionary
        state.update({
            "Position": "LONG" if option_type == "CE" else "SHORT",
            "OptionSymbol": sym,
            "SecurityId": sec_id,
            "Quantity": qty,
            "EntryPrice": opt_price,
            "EntryIndexPrice": index_price,
            "StopLossPrice": stop_loss_idx,
            "Strike": strike,
            "OptionType": option_type,
            "EntryTime": entry_time
        })
        
        # Log open trade to daily ledger
        try:
            from utils import log_open_trade_to_ledger, _to_index_name, is_paper_trading
            idx_name = _to_index_name(underlying)
            
            trade_record = {
                "Index": idx_name,
                "Strategy": strategy_tag,
                "Position": "LONG" if option_type == "CE" else "SHORT",
                "OptionSymbol": sym,
                "SecurityId": sec_id,
                "Strike": strike,
                "OptionType": option_type,
                "EntryTime": entry_time,
                "EntryIndexPrice": round(index_price, 2),
                "EntryPremium": round(opt_price, 2),
                "Quantity": qty,
                "ExitTime": None,
                "ExitIndexPrice": None,
                "ExitPremium": None,
                "ExitReason": None,
                "PremiumPnL": None,
                "NetPnL": None,
                "IndexPnL": None,
                "TradeType": "PAPER" if is_paper_trading() else "LIVE"
            }
            # Add metadata context
            trade_record.update(extra_details)
            log_open_trade_to_ledger(trade_record)
        except Exception as ledger_err:
            safe_print(f"[ERROR] Failed to write open trade to ledger: {ledger_err}")
        
        # Subscribe to option tick stream
        if feed is not None:
            try:
                seg_code = 8 if "Sensex" in underlying or "BSE" in underlying else 2
                feed.subscribe_symbols([(seg_code, str(sec_id))])
            except Exception as e:
                safe_print(f"Error subscribing to option tick stream: {e}")
            
        # Get lots description
        meta = get_index_metadata(underlying)
        lot_size = meta.get("lotSize", 1)
        lots = qty // lot_size
        qty_desc = f"{lots} Lots ({qty} shares)"

        # Send Telegram entry notification
        msg = (
            f"🚀 <b>[TRADE ENTERED]</b> {underlying}\n"
            f"• <b>Strategy:</b> {strategy_tag}\n"
            f"• <b>Contract:</b> {sym}\n"
            f"• <b>Type:</b> {'CALL (CE)' if option_type == 'CE' else 'PUT (PE)'}\n"
            f"• <b>Quantity:</b> {qty_desc}\n"
            f"• <b>Entry Premium:</b> ₹{opt_price:.2f}\n"
            f"• <b>Index Price:</b> {index_price:.2f}\n"
            f"• <b>Stop Loss (Index):</b> {stop_loss_idx:.2f}\n"
            f"• <b>Target (Index):</b> {state.get('TargetPrice') or 0.0:.2f}"
        )
        try:
            from utils import send_telegram_notification
            send_telegram_notification(msg)
        except Exception as telegram_err:
            safe_print(f"[WARNING] Telegram notification failed: {telegram_err}")

        # Log entry details to stdout/logs
        safe_print(
            f"\n🚀 [TRADE ENTERED] {underlying}\n"
            f"  • Contract: {sym}\n"
            f"  • Quantity: {qty_desc}\n"
            f"  • Entry Premium: ₹{opt_price:.2f}\n"
            f"  • Index Price: {index_price:.2f}\n"
            f"  • Stop Loss (Index): {stop_loss_idx:.2f}\n"
        )

        return True
        
    return False

def execute_exit_trade(dhan, underlying, state, index_price, client_code, feed, target_expiry_date, active_high, active_low, strategy_tag="TIME_MACHINE", exit_reason=None):
    """
    Places live Sell order to square off position, unsubscribes feed from tick updates,
    and resets position fields in state dict.
    strategy_tag is written to trades.json and Telegram for strategy comparison.
    Returns True if successfully executed.
    """
    position = state.get("Position")
    sec_id = state.get("SecurityId")
    qty = state.get("Quantity", 0)
    
    if position == "NONE" or not sec_id:
        return False
        
    # Retrieve details from state with fallback parsing of symbol
    strike = state.get("Strike")
    option_type = state.get("OptionType")
    
    if not strike or not option_type:
        option_type = "CE" if position == "LONG" else "PE"
        opt_sym = state.get("OptionSymbol", "")
        # Extract strike from symbol e.g., "SENSEX-Aug2026-78000-CE" -> strike=78000
        match = re.search(r'-(\d+)-(CE|PE)$', opt_sym)
        if match:
            strike = int(match.group(1))
            option_type = match.group(2)
        else:
            # Last resort dynamic calculation
            range_val = active_high if position == "LONG" else active_low
            strike = get_atm_itm_strike(underlying, range_val, option_type)
            
    # Store trade parameters for notification before resetting state
    opt_sym = state.get("OptionSymbol")
    entry_price = state.get("EntryPrice", 0.0)
    
    # Fetch current option price for PnL report
    exit_price = get_option_price(dhan, sec_id, underlying) or 0.0
    pnl = (exit_price - entry_price) * qty
    
    extra_details = {
        "ExitReason": exit_reason or "Time Exit / Signal",
        "EntryPrice": entry_price,
        "ExitPrice": exit_price,
        "NetPnL": round(pnl, 2)
    }
    
    success, _, _ = execute_dhan_order(
        dhan, underlying, target_expiry_date, strike, option_type, "SELL", index_price, client_code, qty,
        strategy_tag=strategy_tag, extra_details=extra_details
    )
    
    if success:
        if pnl >= 0:
            pnl_indicator = f"🟢 <b>P&L:</b> +₹{pnl:.2f} (Profit)"
        else:
            pnl_indicator = f"🔴 <b>P&L:</b> -₹{abs(pnl):.2f} (Loss)"

        # Log completed trade to daily ledger in real-time
        try:
            from utils import log_completed_trade_to_ledger, is_paper_trading, _to_index_name
            idx_name = _to_index_name(underlying)
            entry_time = state.get("EntryTime") or datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
            exit_time = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
            
            entry_idx = state.get("EntryIndexPrice") or 0.0
            idx_pnl = (index_price - entry_idx) if position == "LONG" else (entry_idx - index_price)
            
            exit_details = {
                "ExitTime": exit_time,
                "ExitIndexPrice": round(index_price, 2),
                "ExitPremium": round(exit_price, 2),
                "ExitReason": exit_reason or "Time Exit / Signal",
                "PremiumPnL": round(exit_price - entry_price, 2),
                "NetPnL": round(pnl, 2),
                "IndexPnL": round(idx_pnl, 2)
            }
            
            fallback_record = {
                "Index": idx_name,
                "Strategy": strategy_tag,
                "Position": position,
                "OptionSymbol": opt_sym,
                "SecurityId": sec_id,
                "Strike": strike,
                "OptionType": option_type,
                "EntryTime": entry_time,
                "EntryIndexPrice": round(entry_idx, 2),
                "EntryPremium": round(entry_price, 2),
                "Quantity": qty,
                "TradeType": "PAPER" if is_paper_trading() else "LIVE",
                "LogSnippet": ""
            }
            log_completed_trade_to_ledger(opt_sym, qty, exit_details, fallback_record=fallback_record)
        except Exception as ledger_err:
            safe_print(f"[ERROR] Failed to write trade to ledger: {ledger_err}")

        # Unsubscribe from dynamic F&O option ticks
        if feed is not None:
            try:
                seg_code = 8 if "Sensex" in underlying or "BSE" in underlying else 2
                feed.unsubscribe_symbols([(seg_code, str(sec_id))])
            except Exception as e:
                safe_print(f"Error unsubscribing option tick stream: {e}")
            
        # Reset position fields in state dict
        state.update({
            "Position": "NONE",
            "OptionSymbol": None,
            "SecurityId": None,
            "Quantity": 0,
            "EntryPrice": 0.0,
            "EntryIndexPrice": 0.0,
            "Strike": None,
            "OptionType": None,
            "StopLossPrice": None,
            "TargetPrice": None
        })
        
        # Get lots description
        meta = get_index_metadata(underlying)
        lot_size = meta.get("lotSize", 1)
        lots = qty // lot_size
        qty_desc = f"{lots} Lots ({qty} shares)"

        # Send Telegram exit notification
        msg = (
            f"🎯 <b>[TRADE EXITED]</b> {underlying}\n"
            f"• <b>Strategy:</b> {strategy_tag}\n"
            f"• <b>Contract:</b> {opt_sym}\n"
            f"• <b>Quantity:</b> {qty_desc}\n"
            f"• <b>Entry Premium:</b> ₹{entry_price:.2f}\n"
            f"• <b>Exit Premium:</b> ₹{exit_price:.2f}\n"
            f"• {pnl_indicator}\n"
            f"• <b>Exit Index Price:</b> {index_price:.2f}"
        )
        try:
            from utils import send_telegram_notification
            send_telegram_notification(msg)
        except Exception as telegram_err:
            safe_print(f"[WARNING] Telegram notification failed: {telegram_err}")

        # Log exit details to stdout/logs
        clean_pnl_indicator = pnl_indicator.replace("<b>", "").replace("</b>", "").replace("🟢 ", "").replace("🔴 ", "")
        safe_print(
            f"\n🎯 [TRADE EXITED] {underlying}\n"
            f"  • Contract: {opt_sym}\n"
            f"  • Quantity: {qty_desc}\n"
            f"  • Entry Premium: ₹{entry_price:.2f}\n"
            f"  • Exit Premium: ₹{exit_price:.2f}\n"
            f"  • {clean_pnl_indicator}\n"
            f"  • Exit Index Price: {index_price:.2f}\n"
        )

        return True
        
    return False
