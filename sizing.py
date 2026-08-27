import os
import json
from utils import safe_print, get_index_metadata, UTILS_DIR, TRADES_DIR

# ==========================================================
# RISK AND CAPITAL ALLOCATION CONFIGURATIONS
# ==========================================================
MAX_MARGIN_PER_INDEX = 50000.0  # Max margin budget allowed to be allocated per index trade
MAX_LOSS_PER_TRADE = 5000.0      # Max capital risk/loss allowed per trade

# ----------------------------------------------------------
# TIME MACHINE STRATEGY TUNING CONSTANTS
# ----------------------------------------------------------
# After T1 is hit and the SL is trailed toward entry,
# this ratio adds a buffer so a minor retracement does not
# immediately stop out the position.
# E.g., 0.5 means SL trails to entry + 50% of original risk
# (for SHORT) rather than to exact entry price.
TM_TRAIL_BUFFER_RATIO = 0.5

# Number of consecutive ticks the live option P&L must be
# below the Max Capital Risk threshold before an emergency
# exit is triggered. Prevents a single noisy tick from
# prematurely closing a position.
MAX_LOSS_BREACH_THRESHOLD = 2
# ----------------------------------------------------------

def get_max_loss_per_trade():
    """Gets dynamic max loss per trade: Rs. 2,000 for live trading, or 10% of MAX_MARGIN_PER_INDEX for normal execution."""
    from utils import is_paper_trading
    if not is_paper_trading():
        return 2000.0
    return MAX_MARGIN_PER_INDEX * 0.10
# ==========================================================

def get_active_positions_cost():
    """Reads all active state files to calculate the premium spent on open positions."""
    total_cost = 0.0
    for filename in os.listdir(TRADES_DIR):
        if filename.startswith("state_") and filename.endswith(".json"):
            try:
                with open(os.path.join(TRADES_DIR, filename), "r") as f:
                    state = json.load(f)
                    if state.get("Position") != "NONE":
                        qty = int(state.get("Quantity", 0))
                        price = float(state.get("EntryPrice", 0.0))
                        total_cost += qty * price
            except Exception:
                pass
    return total_cost

def calculate_order_qty(dhan, underlying, option_price, entry, stop_loss):
    """
    Calculates order quantity based on:
    - Live trading precaution: force exactly 1 lot.
    - Spreadsheet Risk Model (paper trading):
      - Capital Risk = MAX_LOSS_PER_TRADE (e.g. Rs. 5,000)
      - Strike Risk = |entry - stop_loss| * 0.5
      - Risk per Lot = Strike Risk * Lot Size
      - Target Lots = round(MAX_LOSS_PER_TRADE / Risk per Lot)
    - Option Premium Cost Limit:
      - Option premium cost must not exceed MAX_MARGIN_PER_INDEX (e.g. Rs. 100,000)
    - Available balance from Dhan (get_fund_limits)
    - 80% allocation budget capped across all running instances (total_cost)
    - Scale down to fit budget if needed, down to 1 lot. If not even 1 lot fits, return None.
    """
    meta = get_index_metadata(underlying)
    lot_size = meta["lotSize"]
    
    from utils import is_paper_trading
    if not is_paper_trading():
        safe_print(f"  [LIVE MODE] Forcing order size to exactly 1 lot: {lot_size} shares")
        return lot_size
    
    # 1. Calculate Target Lots based on Spreadsheet Risk Model
    index_risk = abs(entry - stop_loss)
    strike_risk = index_risk * 0.5
    
    # Avoid Division by Zero if entry and stop_loss are identical
    if strike_risk <= 0:
        safe_print(f"  [WARNING] Strike risk is 0 (entry={entry}, stop_loss={stop_loss}). Skipping sizing.")
        return None
        
    risk_per_lot = strike_risk * lot_size
    target_lots = round(get_max_loss_per_trade() / risk_per_lot)
    
    # Enforce minimum of 1 lot for trading
    if target_lots < 1:
        target_lots = 1
        
    # 2. Fetch available balance
    available_margin = 0.0
    try:
        response = dhan.get_fund_limits()
        if response.get("status") == "success":
            data = response.get("data", {})
            available_margin = float(data.get("availabelBalance") or data.get("availableBalance") or data.get("availableLimit") or 0.0)
    except Exception as e:
        safe_print(f"Error fetching fund limits: {e}")
        return None
        
    # 3. Get cost of other active positions (for multi-index coordination)
    active_cost = get_active_positions_cost()
    
    # 4. Calculate allocation
    total_margin = available_margin + active_cost
    max_allocation = total_margin * 0.8
    remaining_budget = max_allocation - active_cost
    
    # Calculate required cost for the target lots
    target_cost = target_lots * lot_size * option_price
    
    safe_print(f"\n" + "-"*50)
    safe_print(f"             MARGIN BUDGET SIZING")
    safe_print("-"*50)
    safe_print(f"  • Entry Index:        {entry:.2f}")
    safe_print(f"  • Stop Loss Index:    {stop_loss:.2f}")
    safe_print(f"  • Index Risk Points:  {index_risk:.2f}")
    safe_print(f"  • Strike Risk Points: {strike_risk:.2f}")
    safe_print(f"  • Risk per Lot:       {risk_per_lot:.2f}")
    safe_print(f"  • Target Lots (Risk): {target_lots} lots ({target_lots * lot_size} shares)")
    safe_print(f"  • Available Margin:   {available_margin:.2f}")
    safe_print(f"  • Active Positions:   {active_cost:.2f}")
    safe_print(f"  • Max Allocation (80%): {max_allocation:.2f}")
    safe_print(f"  • Remaining Budget:     {remaining_budget:.2f}")
    safe_print(f"  • Max Index Margin:   {MAX_MARGIN_PER_INDEX:.2f}")
    safe_print(f"  • Max Capital Risk:   {get_max_loss_per_trade():.2f}")
    safe_print(f"  • Cost for Target:    {target_cost:.2f} ({target_lots * lot_size} shares @ {option_price:.2f})")
    safe_print("-"*50 + "\n")
    
    # Scale down lots if it exceeds remaining budget, available margin, or max margin per index limit
    while target_lots > 1 and (target_cost > remaining_budget or target_cost > available_margin or target_cost > MAX_MARGIN_PER_INDEX):
        target_lots -= 1
        target_cost = target_lots * lot_size * option_price
        
    if target_cost <= remaining_budget and target_cost <= available_margin and target_cost <= MAX_MARGIN_PER_INDEX:
        safe_print(f"  -> Allocation Sized: {target_lots} Lots ({target_lots * lot_size} shares)")
        return target_lots * lot_size
    else:
        safe_print(f"  [ALERT] Insufficient balance or exceeding risk/margin limits. Skipped.")
        return None
