"""generate_report.py
------------------
Pure renderer: reads and merges daily trades/trade_ledger_YYYY-MM-DD.json
ledger files, and writes reports/StrategyReport-DDMonYY.html.

Usage:
    python reports/generate_report.py
"""
import os
import sys
import json
from datetime import datetime

# Resolve paths relative to this file so it is CWD-independent
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(SCRIPT_DIR)  # kept for generate_dashboard compatibility

LEDGER_FILE = os.path.join(SCRIPT_DIR, "trade_ledger.json")
OUTPUT_HTML = os.path.join(SCRIPT_DIR, "StrategyReport-" + datetime.now().strftime("%d%b%y") + ".html")

def migrate_consolidated_ledger():
    """
    One-time migration: reads reports/trade_ledger.json, splits historical trades
    into daily trades/trade_ledger_YYYY-MM-DD.json files, and renames reports/trade_ledger.json to .bak.
    """
    if os.path.exists(LEDGER_FILE):
        try:
            print("[MIGRATION] Found legacy consolidated reports/trade_ledger.json. Migrating to daily files...")
            with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            trades = data.get("trades", [])
            
            sys.path.append(parent_dir)
            from utils import _append_trade_to_ledger_file
            
            migrated_count = 0
            for t in trades:
                entry_time = t.get("EntryTime")
                if entry_time:
                    date_str = entry_time.split()[0] # YYYY-MM-DD
                    daily_file = os.path.join(parent_dir, "trades", f"trade_ledger_{date_str}.json")
                    os.makedirs(os.path.dirname(daily_file), exist_ok=True)
                    if _append_trade_to_ledger_file(daily_file, t):
                        migrated_count += 1
                        
            print(f"[MIGRATION] Successfully migrated {migrated_count} trade(s) to daily trades/trade_ledger_*.json files.")
            bak_file = LEDGER_FILE + ".bak"
            if os.path.exists(bak_file):
                os.remove(bak_file)
            os.rename(LEDGER_FILE, bak_file)
            print(f"[MIGRATION] Renamed legacy consolidated ledger to: {os.path.basename(bak_file)}")
        except Exception as e:
            print(f"[MIGRATION ERROR] Failed to migrate consolidated ledger: {e}")

def load_trade_ledger() -> list:
    """Load and merge completed trade records from all trades/trade_ledger_*.json daily files, deduplicating them."""
    # 1. Run migration first
    migrate_consolidated_ledger()
    
    trades = []
    dedup_keys = set()
    
    def get_dedup_key(t):
        return (
            t.get("Index", ""),
            t.get("Strategy", ""),
            t.get("EntryTime", ""),
            t.get("OptionSymbol", ""),
            str(t.get("Quantity", ""))
        )

    # 2. Load daily ledger files from trades/
    import glob
    trades_dir = os.path.join(parent_dir, "trades")
    daily_pattern = os.path.join(trades_dir, "trade_ledger_*.json")
    daily_files = glob.glob(daily_pattern)
    daily_count = 0
    
    for filepath in daily_files:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            daily_trades = data.get("trades", [])
            for t in daily_trades:
                # SKIP OPEN TRADES: we only render completed trades on the dashboard
                if t.get("ExitTime") is None:
                    continue
                key = get_dedup_key(t)
                if key not in dedup_keys:
                    trades.append(t)
                    dedup_keys.add(key)
                    daily_count += 1
        except Exception as e:
            print(f"[WARN] Could not read daily ledger {os.path.basename(filepath)}: {e}")

    print(f"Loaded {len(trades)} unique completed trade(s) from daily ledger files.")

    # Sort trades chronologically descending
    trades.sort(key=lambda x: x.get("EntryTime", ""), reverse=True)
    return trades

def generate_dashboard(trades):
    trades.sort(key=lambda x: x["EntryTime"], reverse=True)
    
    # Calculate stats
    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if t.get("NetPnL") and t["NetPnL"] > 0)
    losing_trades = sum(1 for t in trades if t.get("NetPnL") and t["NetPnL"] <= 0)
    win_rate = round((winning_trades / total_trades) * 100, 1) if total_trades > 0 else 0.0
    
    total_profit = sum(t["NetPnL"] for t in trades if t.get("NetPnL") and t["NetPnL"] > 0)
    total_loss = abs(sum(t["NetPnL"] for t in trades if t.get("NetPnL") and t["NetPnL"] < 0))
    net_pnl = sum(t["NetPnL"] for t in trades if t.get("NetPnL"))
    profit_factor = round(total_profit / total_loss, 2) if total_loss > 0 else (round(total_profit, 2) if total_profit > 0 else 1.0)
    
    # Group by index
    index_performance = {}
    for t in trades:
        idx_name = t["Index"]
        if idx_name not in index_performance:
            index_performance[idx_name] = {"Trades": 0, "PnL": 0.0}
        index_performance[idx_name]["Trades"] += 1
        if t.get("NetPnL"):
            index_performance[idx_name]["PnL"] += t["NetPnL"]
            
    for k in index_performance:
        index_performance[k]["PnL"] = round(index_performance[k]["PnL"], 2)

    # Group by strategy
    strategy_performance = {}
    for t in trades:
        strat = t.get("Strategy", "TIME_MACHINE")
        if strat not in strategy_performance:
            strategy_performance[strat] = {"Trades": 0, "PnL": 0.0}
        strategy_performance[strat]["Trades"] += 1
        if t.get("NetPnL"):
            strategy_performance[strat]["PnL"] += t["NetPnL"]

    for k in strategy_performance:
        strategy_performance[k]["PnL"] = round(strategy_performance[k]["PnL"], 2)

    # HTML template with placeholder strings
    html_template = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Visual Trading Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-color: #0b0f19;
            --panel-bg: rgba(17, 24, 39, 0.6);
            --border-color: rgba(255, 255, 255, 0.08);
            --text-color: #f3f4f6;
            --text-muted: #9ca3af;
            --accent-green: #10b981;
            --accent-red: #ef4444;
            --accent-blue: #3b82f6;
            --glass-blur: blur(16px);
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Outfit', sans-serif;
            -webkit-font-smoothing: antialiased;
        }

        body {
            background: radial-gradient(circle at 50% 0%, #1e293b 0%, var(--bg-color) 70%);
            color: var(--text-color);
            min-height: 100vh;
            padding: 2rem;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2.5rem;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 1.5rem;
        }

        .brand h1 {
            font-size: 2.2rem;
            font-weight: 700;
            background: linear-gradient(to right, #3b82f6, #10b981);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .brand p {
            color: var(--text-muted);
            font-size: 0.95rem;
            margin-top: 0.2rem;
        }

        /* Grid System */
        .grid-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2.5rem;
        }

        .card-stat {
            background: var(--panel-bg);
            backdrop-filter: var(--glass-blur);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 1.5rem;
            position: relative;
            overflow: hidden;
            transition: transform 0.3s ease, border-color 0.3s ease;
        }

        .card-stat:hover {
            transform: translateY(-4px);
            border-color: rgba(255, 255, 255, 0.15);
        }

        .card-stat::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
            background: var(--accent-blue);
        }

        .card-stat.profit::before { background: var(--accent-green); }
        .card-stat.loss::before { background: var(--accent-red); }

        .card-stat .title {
            font-size: 0.85rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.5rem;
        }

        .card-stat .value {
            font-size: 1.8rem;
            font-weight: 700;
        }

        .card-stat .sub {
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-top: 0.3rem;
        }

        /* Dashboard Body Layout */
        .dashboard-body {
            display: grid;
            grid-template-columns: 2.2fr 1fr;
            gap: 2rem;
        }

        @media (max-width: 1024px) {
            .dashboard-body {
                grid-template-columns: 1fr;
            }
        }

        .panel {
            background: var(--panel-bg);
            backdrop-filter: var(--glass-blur);
            border: 1px solid var(--border-color);
            border-radius: 20px;
            padding: 1.8rem;
            margin-bottom: 2rem;
        }

        .panel-title {
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 1.5rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        /* Interactive Filter Tabs */
        .filters-group {
            display: flex;
            flex-direction: column;
            gap: 0.8rem;
            margin-bottom: 1.5rem;
        }

        .filters {
            display: flex;
            gap: 0.8rem;
            flex-wrap: wrap;
            align-items: center;
        }

        .filter-label {
            font-size: 0.8rem;
            font-weight: 600;
            text-transform: uppercase;
            color: var(--text-muted);
            min-width: 90px;
        }

        .filter-btn {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 0.4rem 0.9rem;
            color: var(--text-color);
            cursor: pointer;
            font-size: 0.8rem;
            transition: all 0.2s ease;
        }

        .filter-btn:hover, .filter-btn.active {
            background: var(--accent-blue);
            border-color: var(--accent-blue);
            color: white;
            box-shadow: 0 0 10px rgba(59, 130, 246, 0.3);
        }

        /* Date filter inputs */
        .date-input {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 0.4rem 0.75rem;
            color: var(--text-color);
            font-size: 0.8rem;
            font-family: 'Outfit', sans-serif;
            cursor: pointer;
            transition: all 0.2s ease;
            color-scheme: dark;
        }
        .date-input:hover, .date-input:focus {
            border-color: var(--accent-blue);
            outline: none;
            box-shadow: 0 0 8px rgba(59, 130, 246, 0.25);
        }
        .date-clear-btn {
            background: rgba(239, 68, 68, 0.12);
            border: 1px solid rgba(239, 68, 68, 0.3);
            border-radius: 8px;
            padding: 0.4rem 0.75rem;
            color: var(--accent-red);
            cursor: pointer;
            font-size: 0.8rem;
            font-family: 'Outfit', sans-serif;
            transition: all 0.2s ease;
        }
        .date-clear-btn:hover {
            background: rgba(239, 68, 68, 0.25);
            border-color: var(--accent-red);
        }
        .date-sep {
            color: var(--text-muted);
            font-size: 0.8rem;
        }
        /* Quick date preset buttons */
        .quick-date-btn {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 0.4rem 0.85rem;
            color: var(--text-muted);
            cursor: pointer;
            font-size: 0.78rem;
            font-family: 'Outfit', sans-serif;
            font-weight: 500;
            letter-spacing: 0.02em;
            transition: all 0.2s ease;
        }
        .quick-date-btn:hover {
            background: rgba(99, 102, 241, 0.12);
            border-color: rgba(99, 102, 241, 0.4);
            color: #a5b4fc;
        }
        .quick-date-btn.active {
            background: rgba(99, 102, 241, 0.2);
            border-color: #6366f1;
            color: #a5b4fc;
            box-shadow: 0 0 10px rgba(99, 102, 241, 0.25);
        }

        /* Table ledger */
        .table-container {
            overflow-x: auto;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
        }

        th {
            padding: 1rem;
            font-size: 0.85rem;
            text-transform: uppercase;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border-color);
        }

        td {
            padding: 1.1rem 1rem;
            font-size: 0.95rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            cursor: pointer;
        }

        tr.trade-row:hover td {
            background: rgba(255, 255, 255, 0.02);
        }

        .badge {
            padding: 0.25rem 0.6rem;
            border-radius: 6px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
        }

        .badge.buy { background: rgba(16, 185, 129, 0.15); color: var(--accent-green); }
        .badge.sell { background: rgba(239, 68, 68, 0.15); color: var(--accent-red); }
        
        .badge.strategy-tm { background: rgba(59, 130, 246, 0.15); color: var(--accent-blue); }
        .badge.strategy-ind { background: rgba(139, 92, 246, 0.15); color: #a78bfa; }

        .val-profit { color: var(--accent-green); font-weight: 600; }
        .val-loss { color: var(--accent-red); font-weight: 600; }

        /* Sidebar stats */
        .index-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 1rem 0;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
        }

        .index-row:last-child {
            border-bottom: none;
            padding-bottom: 0;
        }

        .index-name {
            font-weight: 500;
        }

        .index-meta {
            color: var(--text-muted);
            font-size: 0.8rem;
            margin-top: 0.2rem;
        }

        /* Detail Drawer */
        .drawer {
            position: fixed;
            top: 0;
            right: -550px;
            width: 500px;
            height: 100%;
            background: #111827;
            box-shadow: -10px 0 30px rgba(0,0,0,0.5);
            transition: right 0.3s ease;
            z-index: 1000;
            padding: 2rem;
            overflow-y: auto;
            border-left: 1px solid var(--border-color);
        }

        .drawer.active {
            right: 0;
        }

        .drawer-overlay {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.5);
            display: none;
            z-index: 999;
            backdrop-filter: blur(4px);
        }

        .drawer-overlay.active {
            display: block;
        }

        .drawer-close {
            background: rgba(255,255,255,0.05);
            border: none;
            color: white;
            padding: 0.5rem;
            border-radius: 8px;
            cursor: pointer;
            float: right;
        }

        pre.log-container {
            background: #030712;
            padding: 1rem;
            border-radius: 8px;
            overflow-x: auto;
            font-size: 0.8rem;
            color: #d1d5db;
            line-height: 1.4;
            max-height: 400px;
            overflow-y: auto;
            border: 1px solid var(--border-color);
            margin-top: 1rem;
        }

        /* Animations */
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .card-stat, .panel {
            animation: fadeIn 0.5s cubic-bezier(0.16, 1, 0.3, 1) both;
        }

        /* Trade-type toggle */
        .trade-type-toggle {
            display: flex;
            align-items: center;
            gap: 0.4rem;
            background: rgba(255,255,255,0.05);
            border: 1px solid var(--border-color);
            border-radius: 50px;
            padding: 0.3rem;
        }

        .tt-btn {
            padding: 0.4rem 0.85rem;
            border-radius: 50px;
            border: none;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            background: transparent;
            color: var(--text-muted);
            letter-spacing: 0.04em;
        }

        .tt-btn.active-paper {
            background: rgba(59,130,246,0.2);
            color: var(--accent-blue);
            box-shadow: 0 0 12px rgba(59,130,246,0.25);
        }

        .tt-btn.active-live {
            background: rgba(245,158,11,0.2);
            color: #f59e0b;
            box-shadow: 0 0 12px rgba(245,158,11,0.25);
        }

        .tt-label {
            font-size: 0.72rem;
            font-weight: 600;
            letter-spacing: 0.06em;
            padding: 0.18rem 0.55rem;
            border-radius: 4px;
            text-transform: uppercase;
        }
        .badge.type-paper { background: rgba(59,130,246,0.15); color: var(--accent-blue); }
        .badge.type-live  { background: rgba(245,158,11,0.15);  color: #f59e0b; }
    </style>
</head>
<body>

    <div class="drawer-overlay" id="overlay" onclick="closeDrawer()"></div>
    <div class="drawer" id="drawer">
        <button class="drawer-close" onclick="closeDrawer()">Close</button>
        <h2 id="drawer-title" style="margin-bottom: 1.5rem;">Trade Details</h2>
        <div id="drawer-content"></div>
        <h3 style="margin-top: 2rem; margin-bottom: 0.5rem; font-size: 1.05rem;">Execution Logs</h3>
        <pre class="log-container" id="drawer-logs"></pre>
    </div>

    <header>
        <div class="brand">
            <h1>Algo trading dashboard</h1>
            <p>Interactive Trade Execution, Ledger & Performance Comparison</p>
            <div style="font-size: 0.85rem; color: var(--text-muted); margin-top: 0.4rem;">
                Last updated: <span style="color: var(--text-color); font-weight: 600;">__LAST_UPDATED__</span>
            </div>
        </div>
        <div style="display:flex; align-items:center; gap:1.5rem; flex-wrap:wrap;">
            <div class="trade-type-toggle" id="tradeTypeToggle">
                <button class="tt-btn active-paper" id="tt-PAPER" onclick="setTradeTypeFilter('PAPER')">📄</button>
                <button class="tt-btn" id="tt-LIVE"  onclick="setTradeTypeFilter('LIVE')">⚡</button>
            </div>
        </div>
    </header>

    <div class="grid-stats">
        <div class="card-stat __NET_PNL_CLASS__">
            <div class="title">Net Profit / Loss</div>
            <div class="value __NET_PNL_COLOR__" id="stat-net-pnl">__NET_PNL_DISPLAY__</div>
            <div class="sub">Dynamic premium tracking</div>
        </div>
        <div class="card-stat">
            <div style="display: flex; justify-content: space-between; align-items: center; gap: 1rem; height: 100%;">
                <div>
                    <div class="title">Total Executions</div>
                    <div class="value" id="stat-total-trades">__TOTAL_TRADES__ Trades</div>
                    <div class="sub" id="stat-win-loss">__WINNING_TRADES__ Win / __LOSING_TRADES__ Loss</div>
                </div>
                <div style="position: relative; height: 55px; width: 55px; flex-shrink: 0;">
                    <canvas id="totalExecutionChart"></canvas>
                </div>
            </div>
        </div>
        <div class="card-stat">
            <div class="title">Strategy Win Rate</div>
            <div class="value" id="stat-win-rate">__WIN_RATE__%</div>
            <div class="sub">Target vs Stop Loss hit</div>
        </div>
        <div class="card-stat">
            <div class="title">Profit Factor</div>
            <div class="value" id="stat-profit-factor">__PROFIT_FACTOR__</div>
            <div class="sub">Gross Profit / Gross Loss</div>
        </div>
    </div>

    <div class="dashboard-body">
        <!-- Main Panel: Trade Ledger -->
        <div class="left-col">
            <div class="panel">
                <div class="panel-title">Trade Ledger</div>
                
                <div class="filters-group">
                    <div class="filters">
                        <span class="filter-label">Index:</span>
                        <button class="filter-btn active" id="idx-btn-ALL" onclick="setIndexFilter('ALL')">All Indices</button>
                        __FILTER_BUTTONS__
                    </div>
                    <div class="filters">
                        <span class="filter-label">Strategy:</span>
                        <button class="filter-btn active" id="strat-btn-ALL" onclick="setStrategyFilter('ALL')">All Strategies</button>
                        <button class="filter-btn" id="strat-btn-TIME_MACHINE" onclick="setStrategyFilter('TIME_MACHINE')">Time Machine</button>
                        <button class="filter-btn" id="strat-btn-INDICATOR" onclick="setStrategyFilter('INDICATOR')">Indicator</button>
                    </div>
                    <div class="filters">
                        <span class="filter-label">Date:</span>
                        <button class="quick-date-btn" id="qdf-today" onclick="setQuickDateFilter('today')">Today</button>
                        <button class="quick-date-btn" id="qdf-week" onclick="setQuickDateFilter('week')">Last Week</button>
                        <button class="quick-date-btn" id="qdf-month" onclick="setQuickDateFilter('month')">This Month</button>
                        <button class="quick-date-btn" id="qdf-quarter" onclick="setQuickDateFilter('quarter')">This Quarter</button>
                        <span class="date-sep" style="margin: 0 0.2rem;">|</span>
                        <input type="date" class="date-input" id="date-from" title="From date" onchange="applyDateFilter()">
                        <span class="date-sep">&rarr;</span>
                        <input type="date" class="date-input" id="date-to" title="To date" onchange="applyDateFilter()">
                        <button class="date-clear-btn" onclick="clearDateFilter()">&#10005; Clear</button>
                    </div>
                </div>

                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>Date / Time</th>
                                <th>Index</th>
                                <th>Strategy</th>
                                <th>Instrument</th>
                                <th>Dir</th>
                                <th>Strike</th>
                                <th>Qty</th>
                                <th>Index Levels</th>
                                <th>Premium PnL</th>
                                <th>Net PnL (INR)</th>
                            </tr>
                        </thead>
                        <tbody id="ledger-body">
                            __TABLE_ROWS__
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Sidebar Panel: Statistics & Index breakdown -->
        <div class="right-col">
            <div class="panel">
                <div class="panel-title">Strategy Comparison</div>
                <div id="sidebar-strategy">
                    __STRATEGY_ROWS__
                </div>
            </div>
            
            <div class="panel">
                <div class="panel-title">Asset Breakdown</div>
                <div id="sidebar-assets">
                    __ASSET_ROWS__
                </div>
            </div>
            
            <div class="panel">
                <div class="panel-title">PnL Allocation Chart</div>
                <canvas id="pnlChart" style="max-height: 250px;"></canvas>
            </div>
        </div>
    </div>

    <script>
        // ── Embedded trade data for stats recomputation ──────────────────────
        const ALL_TRADES = __ALL_TRADES_JSON__;

        let currentIndexFilter    = 'ALL';
        let currentStrategyFilter = 'ALL';
        let currentTradeType      = 'PAPER';  // default to PAPER view
        let currentDateFrom       = null;     // 'YYYY-MM-DD' or null
        let currentDateTo         = null;     // 'YYYY-MM-DD' or null
        let activeQuickFilter     = null;

        // ── Populate date inputs with earliest/latest trade dates on load ────
        (function initDateBounds() {
            const dates = ALL_TRADES.map(t => t.EntryTime ? t.EntryTime.split(' ')[0] : null).filter(Boolean).sort();
            if (dates.length === 0) return;
            document.getElementById('date-from').min = dates[0];
            document.getElementById('date-from').max = dates[dates.length - 1];
            document.getElementById('date-to').min   = dates[0];
            document.getElementById('date-to').max   = dates[dates.length - 1];
        })();

        function applyDateFilter() {
            currentDateFrom = document.getElementById('date-from').value || null;
            currentDateTo   = document.getElementById('date-to').value   || null;
            // Clear quick-filter highlight if user manually edits dates
            ['today','week','month','quarter'].forEach(id => {
                const btn = document.getElementById('qdf-' + id);
                if (btn) btn.classList.remove('active');
            });
            activeQuickFilter = null;
            applyCombinedFilters();
            recomputeStats();
        }

        function clearDateFilter() {
            document.getElementById('date-from').value = '';
            document.getElementById('date-to').value   = '';
            currentDateFrom = null;
            currentDateTo   = null;
            ['today','week','month','quarter'].forEach(id => {
                const btn = document.getElementById('qdf-' + id);
                if (btn) btn.classList.remove('active');
            });
            activeQuickFilter = null;
            applyCombinedFilters();
            recomputeStats();
        }

        function _toYMD(d) {
            const yyyy = d.getFullYear();
            const mm = String(d.getMonth() + 1).padStart(2, '0');
            const dd = String(d.getDate()).padStart(2, '0');
            return `${yyyy}-${mm}-${dd}`;
        }

        function setQuickDateFilter(preset) {
            const now = new Date();
            let from, to;

            if (preset === 'today') {
                from = to = _toYMD(now);
            } else if (preset === 'week') {
                // Mon–Sun of the previous calendar week
                const day = now.getDay(); // 0=Sun
                const diffToLastMon = (day === 0 ? 6 : day - 1) + 7;
                const mon = new Date(now);
                mon.setDate(now.getDate() - diffToLastMon);
                const sun = new Date(mon);
                sun.setDate(mon.getDate() + 6);
                from = _toYMD(mon);
                to   = _toYMD(sun);
            } else if (preset === 'month') {
                // 1st to last day of current month
                from = _toYMD(new Date(now.getFullYear(), now.getMonth(), 1));
                to   = _toYMD(new Date(now.getFullYear(), now.getMonth() + 1, 0));
            } else if (preset === 'quarter') {
                // Current calendar quarter
                const q = Math.floor(now.getMonth() / 3);
                from = _toYMD(new Date(now.getFullYear(), q * 3, 1));
                to   = _toYMD(new Date(now.getFullYear(), q * 3 + 3, 0));
            }

            // Highlight active preset
            ['today','week','month','quarter'].forEach(id => {
                const btn = document.getElementById('qdf-' + id);
                if (btn) btn.classList.toggle('active', id === preset);
            });
            activeQuickFilter = preset;

            // Populate the date inputs
            document.getElementById('date-from').value = from;
            document.getElementById('date-to').value   = to;

            // Apply
            currentDateFrom = from;
            currentDateTo   = to;
            applyCombinedFilters();
            recomputeStats();
        }

        function matchesDateFilter(entryTime) {
            if (!currentDateFrom && !currentDateTo) return true;
            const tradeDate = entryTime ? entryTime.split(' ')[0] : null;
            if (!tradeDate) return false;
            if (currentDateFrom && tradeDate < currentDateFrom) return false;
            if (currentDateTo   && tradeDate > currentDateTo)   return false;
            return true;
        }

        function setTradeTypeFilter(tt) {
            currentTradeType = tt;
            // Update toggle button styles
            ['PAPER','LIVE'].forEach(t => {
                const btn = document.getElementById('tt-' + t);
                btn.className = 'tt-btn';
                if (t === tt) {
                    if (t === 'PAPER') btn.classList.add('active-paper');
                    else if (t === 'LIVE') btn.classList.add('active-live');
                } else {
                    btn.style.background = '';
                }
            });
            applyCombinedFilters();
            recomputeStats();
        }

        function recomputeStats() {
            // Apply ALL active filters: trade-type + index + strategy + date
            const filtered = ALL_TRADES.filter(t => {
                const matchesTT    = (t.TradeType || 'PAPER') === currentTradeType;
                const matchesIdx   = (currentIndexFilter   === 'ALL' || t.Index.replace(/ /g,'_') === currentIndexFilter);
                const matchesStrat = (currentStrategyFilter === 'ALL' || (t.Strategy || 'TIME_MACHINE') === currentStrategyFilter);
                const matchesDate  = matchesDateFilter(t.EntryTime);
                return matchesTT && matchesIdx && matchesStrat && matchesDate;
            });

            const total    = filtered.length;
            const winning  = filtered.filter(t => t.NetPnL != null && t.NetPnL > 0).length;
            const losing   = filtered.filter(t => t.NetPnL != null && t.NetPnL <= 0).length;
            const winRate  = total > 0 ? ((winning / total) * 100).toFixed(1) : '0.0';
            const grossP   = filtered.filter(t => t.NetPnL > 0).reduce((s,t) => s + t.NetPnL, 0);
            const grossL   = Math.abs(filtered.filter(t => t.NetPnL < 0).reduce((s,t) => s + t.NetPnL, 0));
            const netPnL   = filtered.filter(t => t.NetPnL != null).reduce((s,t) => s + t.NetPnL, 0);
            const pf       = grossL > 0 ? (grossP / grossL).toFixed(2) : (grossP > 0 ? grossP.toFixed(2) : '1.00');

            const fmtPnL = (v) => (v >= 0 ? '+' : '') + v.toLocaleString('en-IN', {minimumFractionDigits:2, maximumFractionDigits:2}) + ' INR';

            // Update stat cards
            const pnlEl = document.getElementById('stat-net-pnl');
            if (pnlEl) {
                pnlEl.innerText = fmtPnL(netPnL);
                pnlEl.className = 'value ' + (netPnL >= 0 ? 'val-profit' : 'val-loss');
                pnlEl.closest('.card-stat').className = 'card-stat ' + (netPnL >= 0 ? 'profit' : 'loss');
            }
            const totEl = document.getElementById('stat-total-trades');
            if (totEl) totEl.innerText = total + ' Trades';
            const wlEl = document.getElementById('stat-win-loss');
            if (wlEl) wlEl.innerText = winning + ' Win / ' + losing + ' Loss';
            const wrEl = document.getElementById('stat-win-rate');
            if (wrEl) wrEl.innerText = winRate + '%';
            const pfEl = document.getElementById('stat-profit-factor');
            if (pfEl) pfEl.innerText = pf;

            // ── Rebuild Strategy Comparison sidebar ──────────────────────────
            // Base set: same trade-type + same index filter + date, but ignore strategy filter
            // so all strategies for the current index are always shown
            const stratBase = ALL_TRADES.filter(t => {
                const matchesTT   = (t.TradeType || 'PAPER') === currentTradeType;
                const matchesIdx  = (currentIndexFilter === 'ALL' || t.Index.replace(/ /g,'_') === currentIndexFilter);
                const matchesDate = matchesDateFilter(t.EntryTime);
                return matchesTT && matchesIdx && matchesDate;
            });
            const stratMap = {};
            stratBase.forEach(t => {
                const s = t.Strategy || 'TIME_MACHINE';
                if (!stratMap[s]) stratMap[s] = { trades: 0, pnl: 0 };
                // Only count trades that also pass the strategy filter
                const matchesStrat = (currentStrategyFilter === 'ALL' || s === currentStrategyFilter);
                if (matchesStrat) {
                    stratMap[s].trades += 1;
                    if (t.NetPnL != null) stratMap[s].pnl += t.NetPnL;
                }
            });
            // Ensure all strategies for this trade-type+index appear (even with 0)
            const allStrats = [...new Set(stratBase.map(t => t.Strategy || 'TIME_MACHINE'))];
            if (allStrats.length === 0) {
                // fallback: show all known strategies
                [...new Set(ALL_TRADES.map(t => t.Strategy || 'TIME_MACHINE'))].forEach(s => allStrats.push(s));
            }
            const stratEl = document.getElementById('sidebar-strategy');
            if (stratEl) {
                stratEl.innerHTML = allStrats.map(s => {
                    const data = stratMap[s] || { trades: 0, pnl: 0 };
                    const pnlRounded = Math.round(data.pnl * 100) / 100;
                    const cls  = pnlRounded >= 0 ? 'val-profit' : 'val-loss';
                    const sign = pnlRounded >= 0 ? '+' : '';
                    const isActive = (currentStrategyFilter === 'ALL' || currentStrategyFilter === s);
                    const dimStyle = isActive ? '' : 'opacity:0.35;';
                    return `<div class="index-row" style="${dimStyle}">
                        <div>
                            <div class="index-name">${s.replace(/_/g,' ')}</div>
                            <div class="index-meta">${data.trades} Trades executed</div>
                        </div>
                        <div class="${cls}" style="font-size:1.15rem;font-weight:700;">${sign}${pnlRounded.toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2})}</div>
                    </div>`;
                }).join('');
            }

            // ── Rebuild Asset Breakdown sidebar ──────────────────────────────
            // Base set: same trade-type + same strategy filter + date, but ignore index filter
            const assetBase = ALL_TRADES.filter(t => {
                const matchesTT    = (t.TradeType || 'PAPER') === currentTradeType;
                const matchesStrat = (currentStrategyFilter === 'ALL' || (t.Strategy || 'TIME_MACHINE') === currentStrategyFilter);
                const matchesDate  = matchesDateFilter(t.EntryTime);
                return matchesTT && matchesStrat && matchesDate;
            });
            const assetMap = {};
            assetBase.forEach(t => {
                const idx = t.Index;
                if (!assetMap[idx]) assetMap[idx] = { trades: 0, pnl: 0 };
                const matchesIdx = (currentIndexFilter === 'ALL' || idx.replace(/ /g,'_') === currentIndexFilter);
                if (matchesIdx) {
                    assetMap[idx].trades += 1;
                    if (t.NetPnL != null) assetMap[idx].pnl += t.NetPnL;
                }
            });
            const allIndices = [...new Set(assetBase.map(t => t.Index))];
            if (allIndices.length === 0) {
                [...new Set(ALL_TRADES.map(t => t.Index))].forEach(i => allIndices.push(i));
            }
            const assetEl = document.getElementById('sidebar-assets');
            if (assetEl) {
                assetEl.innerHTML = allIndices.map(idx => {
                    const data = assetMap[idx] || { trades: 0, pnl: 0 };
                    const pnlRounded = Math.round(data.pnl * 100) / 100;
                    const cls  = pnlRounded >= 0 ? 'val-profit' : 'val-loss';
                    const sign = pnlRounded >= 0 ? '+' : '';
                    const isActive = (currentIndexFilter === 'ALL' || idx.replace(/ /g,'_') === currentIndexFilter);
                    const dimStyle = isActive ? '' : 'opacity:0.35;';
                    return `<div class="index-row" style="${dimStyle}">
                        <div>
                            <div class="index-name">${idx}</div>
                            <div class="index-meta">${data.trades} Trades executed</div>
                        </div>
                        <div class="${cls}" style="font-size:1.15rem;font-weight:700;">${sign}${pnlRounded.toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2})}</div>
                    </div>`;
                }).join('');
            }

            // ── Update PnL Allocation Chart (mirrors asset breakdown) ────────────
            if (typeof pnlChartInstance !== 'undefined') {
                const chartPnLs = allIndices.map(idx => {
                    const data = assetMap[idx] || { pnl: 0 };
                    return Math.round(data.pnl * 100) / 100;
                });
                pnlChartInstance.data.labels = allIndices;
                pnlChartInstance.data.datasets[0].data = chartPnLs;
                pnlChartInstance.data.datasets[0].backgroundColor = chartPnLs.map(v => v >= 0 ? 'rgba(16, 185, 129, 0.6)' : 'rgba(239, 68, 68, 0.6)');
                pnlChartInstance.data.datasets[0].borderColor = chartPnLs.map(v => v >= 0 ? '#10b981' : '#ef4444');
                pnlChartInstance.update();
            }



            // ── Update Total Execution Chart (Win/Loss) ──────────────────────────
            if (typeof totalExecutionChartInstance !== 'undefined') {
                totalExecutionChartInstance.data.datasets[0].data = [winning, losing];
                totalExecutionChartInstance.update();
            }
        }

        function setIndexFilter(idxFilter) {
            currentIndexFilter = idxFilter;
            
            // Update buttons
            const buttons = document.querySelectorAll('[id^="idx-btn-"]');
            buttons.forEach(btn => {
                if (btn.id === `idx-btn-${idxFilter}`) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });
            if (idxFilter === 'ALL') {
                document.getElementById('idx-btn-ALL').classList.add('active');
            } else {
                document.getElementById('idx-btn-ALL').classList.remove('active');
            }
            
            applyCombinedFilters();
            recomputeStats();
        }

        function setStrategyFilter(stratFilter) {
            currentStrategyFilter = stratFilter;
            
            // Update buttons
            const buttons = document.querySelectorAll('[id^="strat-btn-"]');
            buttons.forEach(btn => {
                if (btn.id === `strat-btn-${stratFilter}`) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });
            
            applyCombinedFilters();
            recomputeStats();
        }

        function applyCombinedFilters() {
            const rows = document.querySelectorAll('.trade-row');
            rows.forEach(row => {
                const rowIdx    = row.getAttribute('data-index');
                const rowStrat  = row.getAttribute('data-strategy');
                const rowTT     = row.getAttribute('data-trade-type') || 'PAPER';
                const rowDate   = row.getAttribute('data-date') || '';

                const matchesIdx   = (currentIndexFilter === 'ALL' || rowIdx === currentIndexFilter);
                const matchesStrat = (currentStrategyFilter === 'ALL' || rowStrat === currentStrategyFilter);
                const matchesTT    = (currentTradeType === 'ALL' || rowTT === currentTradeType);
                const matchesDate  = (!currentDateFrom || rowDate >= currentDateFrom)
                                  && (!currentDateTo   || rowDate <= currentDateTo);

                if (matchesIdx && matchesStrat && matchesTT && matchesDate) {
                    row.style.display = '';
                } else {
                    row.style.display = 'none';
                }
            });
        }

        function openTrade(trade) {
            document.getElementById('drawer-title').innerText = `${trade.Index} (${trade.Strategy}) - ${trade.OptionSymbol || 'Option'}`;
            
            const netPnLFormatted = trade.NetPnL !== null ? `${trade.NetPnL >= 0 ? '+' : ''}${trade.NetPnL.toLocaleString()} INR` : 'Pending/Unavailable';
            const premiumPnLFormatted = trade.PremiumPnL !== null ? `${trade.PremiumPnL >= 0 ? '+' : ''}${trade.PremiumPnL.toFixed(2)}` : '--';
            const indexPnLFormatted = `${trade.IndexPnL >= 0 ? '+' : ''}${trade.IndexPnL.toFixed(2)}`;
            
            let content = `
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1rem;">
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">STRATEGY</div>
                        <div style="font-weight: 600; font-size: 1.1rem;">${trade.Strategy}</div>
                    </div>
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">POSITION</div>
                        <div style="font-weight: 600; font-size: 1.1rem;">${trade.Position}</div>
                    </div>
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">QUANTITY</div>
                        <div style="font-weight: 600; font-size: 1.1rem;">${trade.Quantity}</div>
                    </div>
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">ENTRY INDEX</div>
                        <div style="font-weight: 600; font-size: 1.1rem;">${(trade.EntryIndexPrice || 0).toFixed(2)}</div>
                    </div>
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">EXIT INDEX</div>
                        <div style="font-weight: 600; font-size: 1.1rem;">${(trade.ExitIndexPrice || 0).toFixed(2)}</div>
                    </div>
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">INDEX PNL</div>
                        <div style="font-weight: 600; font-size: 1.1rem;" class="${trade.IndexPnL >= 0 ? 'val-profit' : 'val-loss'}">${indexPnLFormatted} points</div>
                    </div>
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">PREMIUM PNL</div>
                        <div style="font-weight: 600; font-size: 1.1rem;" class="${(trade.PremiumPnL || 0) >= 0 ? 'val-profit' : 'val-loss'}">${premiumPnLFormatted} points</div>
                    </div>
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">NET TRADE PNL</div>
                        <div style="font-weight: 700; font-size: 1.25rem;" class="${(trade.NetPnL || 0) >= 0 ? 'val-profit' : 'val-loss'}">${netPnLFormatted}</div>
                    </div>
                    <div>
                        <div style="color: var(--text-muted); font-size: 0.85rem;">EXIT REASON</div>
                        <div style="font-size: 0.95rem; font-weight: 500;">${trade.ExitReason}</div>
                    </div>
                </div>
            `;
            
            document.getElementById('drawer-content').innerHTML = content;
            document.getElementById('drawer-logs').innerText = trade.LogSnippet || 'No logs recorded for this trade.';
            
            document.getElementById('drawer').classList.add('active');
            document.getElementById('overlay').classList.add('active');
        }

        function closeDrawer() {
            document.getElementById('drawer').classList.remove('active');
            document.getElementById('overlay').classList.remove('active');
        }

        // Initialize Chart
        const ctx = document.getElementById('pnlChart').getContext('2d');
        const indexNames = [__CHART_LABELS__];
        const indexPnLs = [__CHART_DATA__];

        const pnlChartInstance = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: indexNames,
                datasets: [{
                    label: 'Net PnL (INR)',
                    data: indexPnLs,
                    backgroundColor: indexPnLs.map(val => val >= 0 ? 'rgba(16, 185, 129, 0.6)' : 'rgba(239, 68, 68, 0.6)'),
                    borderColor: indexPnLs.map(val => val >= 0 ? '#10b981' : '#ef4444'),
                    borderWidth: 1.5,
                    borderRadius: 8
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: {
                        grid: { color: 'rgba(255, 255, 255, 0.05)' },
                        ticks: { color: '#9ca3af' }
                    },
                    x: {
                        grid: { display: false },
                        ticks: { color: '#9ca3af' }
                    }
                }
            }
        });



        // Initialize Total Execution Chart (Win/Loss)
        const totExecCtx = document.getElementById('totalExecutionChart').getContext('2d');
        const totalExecutionChartInstance = new Chart(totExecCtx, {
            type: 'doughnut',
            data: {
                labels: ['Win', 'Loss'],
                datasets: [{
                    data: [__WINNING_TRADES__, __LOSING_TRADES__],
                    backgroundColor: ['rgba(16, 185, 129, 0.6)', 'rgba(239, 68, 68, 0.6)'],
                    borderColor: ['#10b981', '#ef4444'],
                    borderWidth: 1.5
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                cutout: '70%'
            }
        });
    </script>
</body>
</html>
"""

    # Generate Dynamic Components
    filter_buttons = " ".join(f'<button class="filter-btn" id="idx-btn-{name.replace(" ", "_")}" onclick="setIndexFilter(\'{name.replace(" ", "_")}\')">{name}</button>' for name in index_performance.keys())
    
    table_rows = []
    for t in trades:
        premium_pnl_val = t.get('PremiumPnL')
        premium_pnl_str = f"{premium_pnl_val:+.2f}" if premium_pnl_val is not None else '--'
        entry_prem_str = f"{t.get('EntryPremium'):.2f}" if t.get('EntryPremium') is not None else '--'
        exit_prem_str = f"{t.get('ExitPremium'):.2f}" if t.get('ExitPremium') is not None else '--'
        
        net_pnl_val = t.get('NetPnL')
        net_pnl_str = f"{net_pnl_val:+,.2f}" if net_pnl_val is not None else '--'
        
        pnl_class = 'profit' if (net_pnl_val or 0.0) >= 0 else 'loss'
        badge_class = 'buy' if t['Position'].lower() == 'long' else 'sell'
        
        strategy_display = t.get("Strategy", "TIME_MACHINE")
        strategy_badge = 'strategy-tm' if strategy_display == 'TIME_MACHINE' else 'strategy-ind'
        
        trade_type = t.get('TradeType', 'PAPER')
        trade_type_badge = 'type-paper' if trade_type == 'PAPER' else 'type-live'
        trade_type_icon  = '📄' if trade_type == 'PAPER' else '⚡'
        
        trade_date = t['EntryTime'].split()[0] if t.get('EntryTime') else ''
        row_html = f"""<tr class="trade-row" data-index="{t['Index'].replace(" ", "_")}" data-strategy="{strategy_display}" data-trade-type="{trade_type}" data-date="{trade_date}" onclick="openTrade({json.dumps(t).replace('"', '&quot;')})">
            <td>
                <div style="font-weight: 500;">{t['EntryTime'].split()[0]}</div>
                <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.15rem;">
                    {t['EntryTime'].split()[-1]} &rarr; {t['ExitTime'].split()[-1] if 'ExitTime' in t else '--:--'}
                </div>
            </td>
            <td>
                <span style="font-weight: 600;">{t['Index']}</span>
            </td>
            <td>
                <span class="badge {strategy_badge}">{strategy_display.replace('_', ' ')}</span>
            </td>
            <td>
                <span style="font-size: 0.9rem; color: var(--accent-blue); font-family: monospace;">{t['OptionSymbol'] or '--'}</span>
            </td>
            <td>
                <span class="badge {badge_class}">{t['Position']}</span>
            </td>
            <td style="font-weight: 600;">{t.get('Strike') or '--'}</td>
            <td style="color: var(--text-muted);">{t['Quantity']}</td>
            <td>
                <div style="font-size: 0.85rem;">Entry: <span style="font-family: monospace;">{t['EntryIndexPrice']:.2f}</span></div>
                <div style="font-size: 0.85rem; color: var(--text-muted); margin-top: 0.1rem;">
                    Exit: <span style="font-family: monospace;">{t.get('ExitIndexPrice', 0.0):.2f}</span> ({t.get('IndexPnL', 0.0):+.2f})
                </div>
            </td>
            <td>
                <div class="val-{pnl_class}">
                    {premium_pnl_str}
                </div>
                <div style="font-size: 0.75rem; color: var(--text-muted); margin-top: 0.1rem;">
                    ({entry_prem_str} &rarr; {exit_prem_str})
                </div>
            </td>
            <td>
                <span class="val-{pnl_class}" style="font-size: 1.05rem;">
                    {net_pnl_str}
                </span>
            </td>
        </tr>"""
        table_rows.append(row_html)
        
    table_rows_str = "".join(table_rows)

    asset_rows = []
    for name, perf in index_performance.items():
        pnl_class = 'profit' if perf['PnL'] >= 0 else 'loss'
        row_html = f"""<div class="index-row">
            <div>
                <div class="index-name">{name}</div>
                <div class="index-meta">{perf['Trades']} Trades executed</div>
            </div>
            <div class="val-{pnl_class}" style="font-size: 1.15rem; font-weight: 700;">
                {perf['PnL']:+,.2f}
            </div>
        </div>"""
        asset_rows.append(row_html)
    asset_rows_str = "".join(asset_rows)

    strategy_rows = []
    for name, perf in strategy_performance.items():
        pnl_class = 'profit' if perf['PnL'] >= 0 else 'loss'
        row_html = f"""<div class="index-row">
            <div>
                <div class="index-name">{name.replace('_', ' ')}</div>
                <div class="index-meta">{perf['Trades']} Trades executed</div>
            </div>
            <div class="val-{pnl_class}" style="font-size: 1.15rem; font-weight: 700;">
                {perf['PnL']:+,.2f}
            </div>
        </div>"""
        strategy_rows.append(row_html)
    strategy_rows_str = "".join(strategy_rows)

    chart_labels = ", ".join(f"'{k}'" for k in index_performance.keys())
    chart_data = ", ".join(str(v["PnL"]) for v in index_performance.values())

    # Replacement
    all_trades_json = json.dumps(trades, ensure_ascii=False)
    html = html_template.replace("__LAST_UPDATED__", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    html = html.replace("__NET_PNL_CLASS__", 'profit' if net_pnl >= 0 else 'loss')
    html = html.replace("__NET_PNL_COLOR__", 'val-profit' if net_pnl >= 0 else 'val-loss')
    html = html.replace("__NET_PNL_DISPLAY__", f"{'+' if net_pnl >= 0 else ''}{net_pnl:,.2f} INR")
    html = html.replace("__TOTAL_TRADES__", str(total_trades))
    html = html.replace("__WINNING_TRADES__", str(winning_trades))
    html = html.replace("__LOSING_TRADES__", str(losing_trades))
    html = html.replace("__WIN_RATE__", str(win_rate))
    html = html.replace("__PROFIT_FACTOR__", str(profit_factor))
    html = html.replace("__FILTER_BUTTONS__", filter_buttons)
    html = html.replace("__TABLE_ROWS__", table_rows_str)
    html = html.replace("__ASSET_ROWS__", asset_rows_str)
    html = html.replace("__STRATEGY_ROWS__", strategy_rows_str)
    html = html.replace("__CHART_LABELS__", chart_labels)
    html = html.replace("__CHART_DATA__", chart_data)
    html = html.replace("__ALL_TRADES_JSON__", all_trades_json)

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Generated visual report dashboard at: {OUTPUT_HTML}")


def main():
    print("Loading trade ledger...")
    trades = load_trade_ledger()

    if not trades:
        print("No trades found in ledger. Nothing to render.")
        return

    print(f"Rendering dashboard for {len(trades)} trade(s)...")
    generate_dashboard(trades)


if __name__ == "__main__":
    main()
