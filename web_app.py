import os
import time
from datetime import datetime
from threading import Thread
from functools import wraps

from flask import (
    Flask,
    render_template_string,
    jsonify,
    request,
    Response,
    redirect,
    url_for,
    session,
)

from config_manager import load_config, save_config, load_positions, save_positions
from data_providers import fetch_candles_binance
from strategy_engine import run_live_cycle
from journal import load_journal_records


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("BOT_SECRET_KEY")
AUTH_USERNAME = os.environ.get("BOT_DASH_USER")
AUTH_PASSWORD = os.environ.get("BOT_DASH_PASS")


def requires_login(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return wrapped


@app.after_request
def set_secure_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


dashboard_data = {
    "last_update": "",
    "open_positions": {},
    "closed_trades": [],
    "stats": {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_pnl": 0.0,
        "win_rate": 0.0,
        "avg_pnl": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "max_drawdown": 0.0,
        "profit_factor": 0.0,
        "expectancy": 0.0,
    },
    "current_signals": [],
    "live_trading_mode": False,
    "equity": 0.0,
    "equity_unrealized": 0.0,
    "risk": {
        "daily_risk_limit_enabled": False,
        "daily_risk_limit_usd": -50.0,
        "pnl_today": 0.0,
        "trading_paused_for_today": False,
        "trades_today": 0,
    },

}

force_closed_until = {}
TIMEFRAME = "1h"

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    next_url = request.args.get("next") or url_for("dashboard")
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == AUTH_USERNAME and password == AUTH_PASSWORD:
            session["logged_in"] = True
            return redirect(next_url)
        else:
            error = "Nieprawidłowy login lub hasło"

    html = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Bot Login</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    :root {
      --bg-main: #020617;
      --bg-card: #020617;
      --border-soft: #1f2937;
      --text-main: #e5e7eb;
      --text-muted: #9ca3af;
      --accent: #38bdf8;
      --accent-soft: rgba(56,189,248,0.12);
      --danger: #f97373;
    }
    * {
      box-sizing: border-box;
    }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top, rgba(56,189,248,0.18), transparent 55%),
        radial-gradient(circle at bottom, #020617 0, #020617 60%, #020617 100%);
      color: var(--text-main);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .login-shell {
      width: 100%;
      max-width: 420px;
      padding: 1.5rem;
    }
    .brand-badge {
      display: inline-flex;
      align-items: center;
      gap: .4rem;
      padding: .25rem .8rem;
      border-radius: 999px;
      border: 1px solid rgba(148,163,184,.4);
      font-size: .75rem;
      text-transform: uppercase;
      letter-spacing: .1em;
      color: var(--text-muted);
      background: rgba(15,23,42,0.75);
      backdrop-filter: blur(12px);
    }
    .login-card {
      position: relative;
      margin-top: 1.2rem;
      border-radius: 1rem;
      border: 1px solid var(--border-soft);
      background: radial-gradient(circle at top, rgba(56,189,248,.12), transparent 55%), #020617;
      box-shadow: 0 24px 60px rgba(15,23,42,0.85);
      padding: 1.5rem 1.6rem 1.4rem;
      overflow: hidden;
    }
    .login-card::before {
      content: "";
      position: absolute;
      inset: -40%;
      background:
        radial-gradient(circle at top right, rgba(56,189,248,0.12), transparent 55%),
        radial-gradient(circle at bottom left, rgba(14,165,233,0.09), transparent 55%);
      opacity: .9;
      pointer-events: none;
    }
    .login-inner {
      position: relative;
      z-index: 1;
    }
    .login-title {
      font-size: .95rem;
      text-transform: uppercase;
      letter-spacing: .12em;
      color: var(--text-muted);
      margin-bottom: .35rem;
    }
    .login-heading {
      font-size: 1.25rem;
      font-weight: 600;
      margin-bottom: .25rem;
    }
    .login-sub {
      font-size: .8rem;
      color: var(--text-muted);
      margin-bottom: 1rem;
    }
    .form-label {
      font-size: .78rem;
      text-transform: uppercase;
      letter-spacing: .08em;
      color: var(--text-muted);
      margin-bottom: .25rem;
    }
    .form-control {
      background-color: rgba(15,23,42,0.9);
      border-radius: .6rem;
      border: 1px solid #1f2937;
      color: var(--text-main);
      font-size: .85rem;
      padding: .45rem .75rem;
    }
    .form-control:focus {
      background-color: #020617;
      border-color: var(--accent);
      box-shadow: 0 0 0 1px rgba(56,189,248,0.5);
      color: var(--text-main);
    }
    .login-btn {
      width: 100%;
      border-radius: 999px;
      border: 1px solid rgba(56,189,248,.7);
      background: linear-gradient(90deg, #0ea5e9, #22c55e);
      color: #0b1120;
      font-size: .85rem;
      font-weight: 600;
      letter-spacing: .08em;
      text-transform: uppercase;
      padding: .5rem .75rem;
      margin-top: .5rem;
      box-shadow: 0 0 24px rgba(56,189,248,.6);
    }
    .login-btn:hover {
      filter: brightness(1.05);
    }
    .error-box {
      border-radius: .6rem;
      border: 1px solid rgba(248,113,113,.6);
      background-color: rgba(127,29,29,0.3);
      color: var(--danger);
      font-size: .78rem;
      padding: .4rem .6rem;
      margin-bottom: .7rem;
    }
    .footer-hint {
      margin-top: .75rem;
      font-size: .7rem;
      color: var(--text-muted);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .footer-hint span {
      opacity: .8;
    }
    .dot {
      width: .4rem;
      height: .4rem;
      border-radius: 999px;
      background: #22c55e;
      box-shadow: 0 0 8px rgba(34,197,94,.7);
      display: inline-block;
      margin-right: .3rem;
    }
  </style>
</head>
<body>
<div class="login-shell">
  <div class="d-flex justify-content-between align-items-center mb-2">
    <div class="brand-badge">
      <span>MEAN‑REVERSION BOT</span>
    </div>
    <small style="font-size:.75rem; color:#6b7280;">1H · BINANCE</small>
  </div>

  <div class="login-card">
    <div class="login-inner">
      <div class="login-title">Secure access</div>
      <div class="login-heading">Sign in to dashboard</div>
      <div class="login-sub">
        Dostęp tylko dla właściciela bota.
      </div>

      {% if error %}
      <div class="error-box">
        {{ error }}
      </div>
      {% endif %}

      <form method="post">
        <div class="mb-3">
          <label class="form-label">Username</label>
          <input name="username" type="text" class="form-control" autocomplete="username">
        </div>
        <div class="mb-2">
          <label class="form-label">Password</label>
          <input name="password" type="password" class="form-control" autocomplete="current-password">
        </div>
        <input type="hidden" name="next" value="{{ next_url }}">
        <button type="submit" class="login-btn">Enter dashboard</button>
      </form>

      <div class="footer-hint">
        <span><span class="dot"></span>Session protected</span>
        <span>Auto‑logout po zamknięciu przeglądarki</span>
      </div>
    </div>
  </div>
</div>
</body>
</html>
"""
    return render_template_string(html, error=error, next_url=next_url)



@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@requires_login
def dashboard():
    cfg = load_config()
    html = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Mean-Reversion 1H Live Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="10">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    :root {
      --bg-main: #020617;
      --bg-card: #020617;
      --bg-page: #020617;
      --border-soft: #1f2937;
      --text-main: #e5e7eb;
      --text-muted: #9ca3af;
      --accent: #38bdf8;
      --accent-soft: rgba(56,189,248,0.1);
      --danger: #f87171;
      --success: #4ade80;
      --warning: #facc15;
    }
    body {
      background: radial-gradient(circle at top, #020617 0, #020617 35%, #020617 100%);
      color: var(--text-main);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .navbar {
      background: linear-gradient(90deg, #020617, #0f172a);
      border-bottom: 1px solid #1f2937;
    }
    .navbar-brand {
      font-weight: 600;
      letter-spacing: .06em;
      text-transform: uppercase;
      font-size: .9rem;
      color: var(--text-main);
    }
    .brand-pill {
      border-radius: 999px;
      padding: .2rem .8rem;
      border: 1px solid rgba(148,163,184,.5);
      font-size: .75rem;
      color: var(--text-muted);
    }
    .last-update {
      font-size: .75rem;
      color: var(--text-muted);
    }
    .mode-pill {
      border-radius: 999px;
      padding: .2rem .8rem;
      font-size: .75rem;
      border: 1px solid rgba(52,211,153,.6);
      color: #22c55e;
    }
    .mode-pill.dry {
      border-color: rgba(248,250,252,.6);
      color: #e5e7eb;
    }
    .card {
      background-color: var(--bg-card);
      border-radius: .9rem;
      border: 1px solid var(--border-soft);
      box-shadow: 0 18px 40px rgba(15,23,42,0.5);
    }
    .card-header {
      border-bottom: 1px solid rgba(31,41,55,.8);
      padding: .6rem 1rem;
    }
    .card-header h5 {
      font-size: .9rem;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: var(--text-muted);
      margin: 0;
    }
    .metric-card {
      position: relative;
      overflow: hidden;
    }
    .metric-card::before {
      content: "";
      position: absolute;
      inset: 0;
      background: radial-gradient(circle at top, rgba(56,189,248,.13), transparent 55%);
      opacity: .8;
      pointer-events: none;
    }
    .metric-body {
      position: relative;
      z-index: 1;
      padding: .85rem 1rem;
    }
    .metric-label {
      font-size: .75rem;
      text-transform: uppercase;
      letter-spacing: .08em;
      color: var(--text-muted);
      margin-bottom: .4rem;
    }
    .metric-value {
      font-size: 1.5rem;
      font-weight: 600;
    }
    .metric-value.equity {
      color: var(--accent);
      text-shadow: 0 0 18px rgba(56,189,248,.55);
    }
    .metric-sub {
      font-size: .75rem;
      color: var(--text-muted);
    }
    .metric-value.pos {
      color: var(--success);
    }
    .metric-value.neg {
      color: var(--danger);
    }
    .table-container {
      max-height: 340px;
      overflow-y: auto;
    }
    .table-dark {
      --bs-table-bg: transparent;
      --bs-table-border-color: rgba(55,65,81,.7);
      font-size: .8rem;
    }
    .table-dark thead th {
      border-bottom-color: rgba(75,85,99,.9);
      text-transform: uppercase;
      font-size: .7rem;
      letter-spacing: .06em;
      color: var(--text-muted);
    }
    .table-dark tbody tr:nth-child(even) {
      background-color: rgba(15,23,42,.7);
    }
    .table-dark tbody tr:hover {
      background-color: rgba(30,64,175,.35);
    }
    .badge-buy {
      background: rgba(34,197,94,.15);
      border: 1px solid rgba(34,197,94,.7);
      color: var(--success);
      font-weight: 500;
      border-radius: 999px;
    }
    .badge-sell {
      background: rgba(248,113,113,.15);
      border: 1px solid rgba(248,113,113,.7);
      color: var(--danger);
      font-weight: 500;
      border-radius: 999px;
    }
    .badge-hold {
      background: rgba(148,163,184,.08);
      border: 1px solid rgba(148,163,184,.6);
      color: var(--text-muted);
      font-weight: 500;
      border-radius: 999px;
    }
    .status-pill {
      border-radius: 999px;
      padding: .1rem .55rem;
      font-size: .65rem;
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .status-ready {
      background: rgba(52,211,153,.12);
      color: var(--success);
      border: 1px solid rgba(52,211,153,.7);
    }
    .status-hold {
      background: rgba(148,163,184,.1);
      color: var(--text-muted);
      border: 1px solid rgba(148,163,184,.5);
    }
    .status-position {
      background: rgba(56,189,248,.1);
      color: var(--accent);
      border: 1px solid rgba(56,189,248,.6);
    }
    .status-cooldown {
      background: rgba(250,204,21,.1);
      color: var(--warning);
      border: 1px solid rgba(250,204,21,.7);
    }
    .btn-outline-warning {
      border-radius: 999px;
      padding: .18rem .7rem;
      font-size: .7rem;
      border-width: 1px;
    }
    .btn-outline-warning:hover {
      background-color: rgba(250,204,21,.1);
      border-color: var(--warning);
      color: var(--warning);
    }
    .pnl-pos {
      color: var(--success);
      font-weight: 500;
    }
    .pnl-neg {
      color: var(--danger);
      font-weight: 500;
    }
    .pnl-small {
      font-size: .72rem;
      color: var(--text-muted);
    }
    ::-webkit-scrollbar {
      width: 6px;
    }
    ::-webkit-scrollbar-track {
      background: transparent;
    }
    ::-webkit-scrollbar-thumb {
      background: #1f2937;
      border-radius: 999px;
    }
    @media (max-width: 992px) {
      .metric-value {
        font-size: 1.2rem;
      }
      .nav-link {
        font-size: .8rem;
        text-transform: uppercase;
        letter-spacing: .08em;
      }
    }
    .nav-link {
      font-size: .8rem;
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .status-bar {
      font-size: .8rem;
      color: var(--text-muted);
    }
    .status-bar span {
      margin-right: 1rem;
    }
    .status-badge-paused {
      color: #f97316;
    }
    .status-badge-active {
      color: #22c55e;
    }
  </style>
</head>
<body>
<nav class="navbar navbar-dark mb-3">
  <div class="container-fluid d-flex justify-content-between align-items-center">
    <div class="d-flex align-items-center gap-3">
      <span class="navbar-brand mb-0 h1">1H LIVE DASHBOARD</span>
      <span class="brand-pill">BINANCE · MEAN REVERSION</span>
    </div>
    <div class="d-flex align-items-center gap-3">
      {% if data.live_trading_mode %}
        <span class="mode-pill">LIVE MODE</span>
      {% else %}
        <span class="mode-pill dry">PAPER MODE</span>
      {% endif %}
      <span class="last-update">Last update: {{ data.last_update }}</span>
    </div>
  </div>
</nav>

<div class="container-fluid mb-3">
  <ul class="nav nav-tabs">
    <li class="nav-item">
      <a class="nav-link active" href="#">Dashboard</a>
    </li>
    <li class="nav-item">
      <a class="nav-link" href="/settings">Settings</a>
    </li>
    <li class="nav-item">
      <a class="nav-link" href="/journal">Journal</a>
    </li>
  </ul>
</div>

<!-- STATUS BOTA / RYZYKO -->
<div class="container-fluid mb-3">
  <div class="status-bar">
    {% if data.risk.trading_paused_for_today %}
      <span class="status-badge-paused">
        Trading status: PAUSED (daily loss limit reached)
      </span>
    {% else %}
      <span class="status-badge-active">
        Trading status: ACTIVE
      </span>
    {% endif %}
    <span>
      Today P&L:
      {% if data.risk.pnl_today >= 0 %}
        <span class="pnl-pos">{{ data.risk.pnl_today }}</span>
      {% else %}
        <span class="pnl-neg">{{ data.risk.pnl_today }}</span>
      {% endif %}
    </span>

    <span>
      Limit: {{ data.risk.daily_risk_limit_usd }}
    </span>
    <span>
      Trades today: {{ data.risk.trades_today }}
    </span>
  </div>

  </div>
</div>

<!-- METRYKI GŁÓWNE -->
<div class="container-fluid mb-4">
  <div class="row g-3">
    <div class="col-6 col-md-2">
      <div class="card metric-card">
        <div class="metric-body">
          <div class="metric-label">Total trades</div>
          <div class="metric-value">{{ data.stats.total_trades }}</div>
          <div class="metric-sub">Closed since start</div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card metric-card">
        <div class="metric-body">
          <div class="metric-label">Win rate</div>
          <div class="metric-value">{{ data.stats.win_rate }}%</div>
          <div class="metric-sub">All closed trades</div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="card metric-card">
        <div class="metric-body">
          <div class="metric-label">Total P&L</div>
          <div class="metric-value {% if data.stats.total_pnl >= 0 %}pos{% else %}neg{% endif %}">
            {{ data.stats.total_pnl }}
          </div>
          <div class="metric-sub">
            Max DD: {{ data.stats.max_drawdown }}
            &nbsp;|&nbsp;
            PF: {{ data.stats.profit_factor }}
            &nbsp;|&nbsp;
            Exp: {{ data.stats.expectancy }}
          </div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md-3">
      <div class="card metric-card">
        <div class="metric-body">
          <div class="metric-label">Account equity</div>
          <div class="metric-value equity">
            {{ data.equity }}
          </div>
          <div class="metric-sub">
            Start: {{ config.account_equity_usd }} · Unrealized: {{ data.equity_unrealized }}
          </div>
        </div>
      </div>
    </div>
    <div class="col-6 col-md-2">
      <div class="card metric-card">
        <div class="metric-body">
          <div class="metric-label">Best / worst</div>
          <div class="metric-value">
            <span class="pnl-pos">{{ data.stats.best_trade }}</span>
            <span class="pnl-small"> / </span>
            <span class="pnl-neg">{{ data.stats.worst_trade }}</span>
          </div>
          <div class="metric-sub">Best / worst trade (USD)</div>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- CURRENT SIGNALS / OPEN POSITIONS / CLOSED TRADES -->
<div class="container-fluid pb-4">
  <div class="row g-4">
    <!-- CURRENT SIGNALS -->
    <div class="col-lg-4">
      <div class="card h-100">
        <div class="card-header">
          <h5 class="mb-0">Current Signals</h5>
        </div>
        <div class="card-body p-0 table-container">
          {% if data.current_signals %}
          <table class="table table-dark table-sm mb-0 align-middle">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Signal</th>
                <th>Price</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {% for s in data.current_signals %}
              <tr>
                <td>{{ s.symbol }}</td>
                <td>
                  {% if s.signal == 'BUY' %}
                    <span class="badge badge-buy">BUY</span>
                  {% elif s.signal == 'SELL' %}
                    <span class="badge badge-sell">SELL</span>
                  {% else %}
                    <span class="badge badge-hold">HOLD</span>
                  {% endif %}
                </td>
                <td>{{ s.price }}</td>
                <td>
                  {% if s.status == 'READY' %}
                    <span class="status-pill status-ready">Ready</span>
                  {% elif s.status == 'IN POSITION' %}
                    <span class="status-pill status-position">In position</span>
                  {% elif s.status == 'COOLDOWN' %}
                    <span class="status-pill status-cooldown">Cooldown</span>
                  {% elif s.status == 'PAUSED' %}
                    <span class="status-pill status-cooldown">Paused</span>
                  {% else %}
                    <span class="status-pill status-hold">{{ s.status }}</span>
                  {% endif %}
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
          {% else %}
          <div class="p-3 text-muted">No signals</div>
          {% endif %}
        </div>
      </div>
    </div>

    <!-- OPEN POSITIONS -->
    <div class="col-lg-4">
      <div class="card h-100">
        <div class="card-header d-flex justify-content-between align-items-center">
          <h5 class="mb-0">Open Positions</h5>
        </div>
        <div class="card-body p-0 table-container">
          {% if data.open_positions %}
          <table class="table table-dark table-sm mb-0 align-middle">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th>Entry</th>
                <th>SL</th>
                <th>TP</th>
                <th>Price</th>
                <th>P&L</th>
                <th>MFE / MAE</th>
                <th>Age</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {% for sym, pos in data.open_positions.items() %}
              <tr>
                <td>{{ sym }}</td>
                <td>
                  {% if pos.action == 'BUY' %}
                    <span class="badge badge-buy">LONG</span>
                  {% else %}
                    <span class="badge badge-sell">SHORT</span>
                  {% endif %}
                </td>
                <td>{{ pos.entry }}</td>
                <td class="text-danger">{{ pos.sl }}</td>
                <td class="text-success">{{ pos.tp }}</td>
                <td>{{ pos.current_price }}</td>
                <td>
                  {% if pos.pnl_usd >= 0 %}
                    <span class="pnl-pos">{{ pos.pnl_usd }} ({{ pos.pnl_pct }}%)</span>
                  {% else %}
                    <span class="pnl-neg">{{ pos.pnl_usd }} ({{ pos.pnl_pct }}%)</span>
                  {% endif %}
                </td>
                <td>
                  <span class="pnl-pos">{{ pos.mfe_pct }}%</span>
                  <span class="pnl-small"> / </span>
                  <span class="pnl-neg">{{ pos.mae_pct }}%</span>
                </td>
                <td>{{ pos.age }}</td>
                <td>
                  <button class="btn btn-sm btn-outline-warning" onclick="forceClose('{{ sym }}')">
                    Close
                  </button>
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
          {% else %}
          <div class="p-3 text-muted">No open positions</div>
          {% endif %}
        </div>
      </div>
    </div>

    <!-- CLOSED TRADES -->
    <div class="col-lg-4">
      <div class="card h-100">
        <div class="card-header">
          <h5 class="mb-0">Recent Closed Trades</h5>
        </div>
        <div class="card-body p-0 table-container">
          {% if data.closed_trades %}
          <table class="table table-dark table-sm mb-0 align-middle">
            <thead>
              <tr>
                <th>Time</th>
                <th>Symbol</th>
                <th>Type</th>
                <th>P&L</th>
              </tr>
            </thead>
            <tbody>
              {% for t in data.closed_trades %}
              <tr>
                <td>{{ t.timestamp }}</td>
                <td>{{ t.symbol }}</td>
                <td>{{ t.exit_type }}</td>
                <td>
                  {% if t.pnl >= 0 %}
                    <span class="pnl-pos">{{ t.pnl }} ({{ t.pnl_pct }}%)</span>
                  {% else %}
                    <span class="pnl-neg">{{ t.pnl }} ({{ t.pnl_pct }}%)</span>
                  {% endif %}
                </td>
              </tr>
              {% endfor %}
            </tbody>
          </table>
          {% else %}
          <div class="p-3 text-muted">No closed trades yet</div>
          {% endif %}
        </div>
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
<script>
async function forceClose(symbol) {
  if (!confirm('Force close ' + symbol + '?')) return;
  try {
    const res = await fetch('/closeposition', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({symbol: symbol})
    });
    const data = await res.json();
    if (!res.ok) {
      alert('Error: ' + (data.error || res.status));
      return;
    }
    alert('Closed ' + symbol + '\\nP&L: ' + data.pnlusd + ' USD (' + data.pnlpct + '%)');
    location.reload();
  } catch (e) {
    alert('Request failed');
  }
}
</script>
</body>
</html>
"""
    return render_template_string(html, data=dashboard_data, config=cfg)


@app.route("/settings", methods=["GET"])
@requires_login
def settings_page():
    cfg = load_config()
    html = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Bot Settings</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {
      background-color: #020617;
      color: #e5e7eb;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .navbar {
      background: linear-gradient(90deg, #020617, #0f172a);
      border-bottom: 1px solid #1f2937;
    }
    .navbar-brand {
      font-weight: 600;
      letter-spacing: .06em;
      text-transform: uppercase;
      font-size: .9rem;
    }
    .card {
      background-color: #020617;
      border-radius: .9rem;
      border: 1px solid #1f2937;
      box-shadow: 0 18px 40px rgba(15,23,42,0.5);
    }
    .card-header {
      border-bottom: 1px solid #1f2937;
      padding: .6rem 1rem;
    }
    .card-header h5 {
      font-size: .9rem;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: #9ca3af;
      margin: 0;
    }
    .nav-link {
      font-size: .8rem;
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .settings-label {
      font-size: .75rem;
      color: #9ca3af;
    }
    .form-control, .form-select {
      background-color: #020617;
      border-color: #1f2937;
      color: #e5e7eb;
      font-size: .8rem;
    }
    .form-control:focus, .form-select:focus {
      box-shadow: 0 0 0 1px #38bdf8;
      border-color: #38bdf8;
      background-color: #020617;
      color: #e5e7eb;
    }
    .form-check-label {
      font-size: .8rem;
    }
    .switch-compact .form-check-input {
      width: 2.4rem;
      height: 1.3rem;
      background-color: #020617;
      border-radius: 1rem;
      border: 1px solid #4b5563;
      cursor: pointer;
      box-shadow: inset 0 0 4px rgba(15,23,42,0.8);
    }
    .switch-compact .form-check-input:focus {
      box-shadow: 0 0 0 1px #38bdf8;
      border-color: #38bdf8;
    }
    .switch-compact .form-check-input:checked {
      background-color: #38bdf8;
      border-color: #38bdf8;
      box-shadow: 0 0 10px rgba(56,189,248,.6);
    }
    .switch-compact .form-check-label {
      font-size: .78rem;
      color: #9ca3af;
      margin-left: 0.35rem;
      user-select: none;
    }
  </style>
</head>
<body>
<nav class="navbar navbar-dark mb-3">
  <div class="container-fluid d-flex justify-content-between align-items-center">
    <div class="d-flex align-items-center gap-3">
      <span class="navbar-brand mb-0 h1">BOT SETTINGS</span>
    </div>
    <div>
      <a href="/" class="btn btn-sm btn-outline-light">Back to dashboard</a>
    </div>
  </div>
</nav>

<div class="container-fluid mb-4">
  <div class="card">
    <div class="card-header d-flex justify-content-between align-items-center">
      <h5 class="mb-0">Core settings</h5>
      <small class="text-muted">Changes apply from next cycles</small>
    </div>
    <div class="card-body">
      <form id="settings-form" class="row g-3 align-items-end">
        <div class="col-6 col-md-2">
          <label class="settings-label mb-1">Max positions</label>
          <input type="number" min="1" max="20" step="1" class="form-control" id="max_open_positions">
        </div>
        <div class="col-6 col-md-2">
          <label class="settings-label mb-1">Default size USD</label>
          <input type="number" min="1" step="1" class="form-control" id="default_position_size_usd">
        </div>
        <div class="col-6 col-md-2">
          <label class="settings-label mb-1">Account equity USD</label>
          <input type="number" min="1" step="1" class="form-control" id="account_equity_usd">
        </div>
        <div class="col-6 col-md-2">
          <label class="settings-label mb-1">SL long %</label>
          <input type="number" min="0.1" step="0.1" class="form-control" id="sl_pct_long">
        </div>
        <div class="col-6 col-md-2">
          <label class="settings-label mb-1">TP long %</label>
          <input type="number" min="0.1" step="0.1" class="form-control" id="tp_pct_long">
        </div>
        <div class="col-6 col-md-2">
          <label class="settings-label mb-1">SL short %</label>
          <input type="number" min="0.1" step="0.1" class="form-control" id="sl_pct_short">
        </div>
        <div class="col-6 col-md-2">
          <label class="settings-label mb-1">TP short %</label>
          <input type="number" min="0.1" step="0.1" class="form-control" id="tp_pct_short">
        </div>
        <div class="col-6 col-md-2">
          <label class="settings-label mb-1">Cooldown (h)</label>
          <input type="number" min="0" step="0.5" class="form-control" id="cooldown_hours">
        </div>
        <div class="col-6 col-md-2">
          <label class="settings-label mb-1">Daily risk limit USD</label>
          <input type="number" step="1" class="form-control" id="daily_risk_limit_usd">
        </div>
        <div class="col-6 col-md-3">
          <div class="form-check form-switch mt-4 switch-compact">
            <input class="form-check-input" type="checkbox" id="daily_risk_limit_enabled" role="switch">
            <label class="form-check-label" for="daily_risk_limit_enabled">
              Daily risk limit
            </label>
          </div>
        </div>
        <div class="col-6 col-md-3">
          <div class="form-check form-switch mt-4 switch-compact">
            <input class="form-check-input" type="checkbox" id="live_trading_mode" role="switch">
            <label class="form-check-label" for="live_trading_mode">
              Live trading
            </label>
          </div>
        </div>
        <div class="col-12 col-md-2 text-md-end">
          <button type="button" class="btn btn-sm btn-primary" onclick="saveSettings()">
            Save settings
          </button>
        </div>
        <div class="col-12">
          <small id="settings-status" class="text-muted"></small>
        </div>
      </form>
    </div>
  </div>
</div>

<script>
async function loadSettings() {
  const statusEl = document.getElementById('settings-status');
  statusEl.textContent = '';
  try {
    const res = await fetch('/config');
    const cfg = await res.json();

    document.getElementById('max_open_positions').value = cfg.max_open_positions ?? 3;
    document.getElementById('default_position_size_usd').value = cfg.default_position_size_usd ?? 100;
    document.getElementById('account_equity_usd').value = cfg.account_equity_usd ?? 1000;
    document.getElementById('sl_pct_long').value = cfg.sl_pct_long ?? 3.0;
    document.getElementById('tp_pct_long').value = cfg.tp_pct_long ?? 3.5;
    document.getElementById('sl_pct_short').value = cfg.sl_pct_short ?? 3.0;
    document.getElementById('tp_pct_short').value = cfg.tp_pct_short ?? 3.5;
    document.getElementById('cooldown_hours').value = cfg.cooldown_hours ?? 1.0;
    document.getElementById('daily_risk_limit_usd').value = cfg.daily_risk_limit_usd ?? -50;
    document.getElementById('daily_risk_limit_enabled').checked = cfg.daily_risk_limit_enabled ?? false;
    document.getElementById('live_trading_mode').checked = cfg.live_trading_mode ?? false;
  } catch (e) {
    statusEl.textContent = 'Failed to load settings';
  }
}

async function saveSettings() {
  const statusEl = document.getElementById('settings-status');
  statusEl.textContent = 'Saving...';
  const payload = {
    max_open_positions: Number(document.getElementById('max_open_positions').value),
    default_position_size_usd: Number(document.getElementById('default_position_size_usd').value),
    sl_pct_long: Number(document.getElementById('sl_pct_long').value),
    tp_pct_long: Number(document.getElementById('tp_pct_long').value),
    sl_pct_short: Number(document.getElementById('sl_pct_short').value),
    tp_pct_short: Number(document.getElementById('tp_pct_short').value),
    account_equity_usd: Number(document.getElementById('account_equity_usd').value),
    cooldown_hours: Number(document.getElementById('cooldown_hours').value),
    live_trading_mode: document.getElementById('live_trading_mode').checked,
    daily_risk_limit_usd: Number(document.getElementById('daily_risk_limit_usd').value),
    daily_risk_limit_enabled: document.getElementById('daily_risk_limit_enabled').checked,
  };
  try {
    const res = await fetch('/config', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      statusEl.textContent = 'Error saving settings';
      return;
    }
    statusEl.textContent = 'Settings saved (apply from next cycle).';
  } catch (e) {
    statusEl.textContent = 'Error saving settings';
  }
}

document.addEventListener('DOMContentLoaded', loadSettings);
</script>
</body>
</html>
"""
    return render_template_string(html)


@app.route("/journal")
@requires_login
def journal():
    cfg = load_config()
    page_size = int(cfg.get("journal_page_size", 50))
    records = load_journal_records(page_size=page_size)
    html = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Trade Journal</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  <style>
    body {
      background-color: #020617;
      color: #e5e7eb;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    .navbar {
      background: linear-gradient(90deg, #020617, #0f172a);
      border-bottom: 1px solid #1f2937;
    }
    .navbar-brand {
      font-weight: 600;
      letter-spacing: .06em;
      text-transform: uppercase;
      font-size: .9rem;
    }
    .card {
      background-color: #020617;
      border-radius: .9rem;
      border: 1px solid #1f2937;
      box-shadow: 0 18px 40px rgba(15,23,42,0.5);
    }
    .card-header {
      border-bottom: 1px solid #1f2937;
      padding: .6rem 1rem;
    }
    .card-header h5 {
      font-size: .9rem;
      letter-spacing: .08em;
      text-transform: uppercase;
      color: #9ca3af;
      margin: 0;
    }
    .table-dark {
      --bs-table-bg: transparent;
      --bs-table-border-color: rgba(55,65,81,.7);
      font-size: .8rem;
    }
    .table-dark thead th {
      border-bottom-color: rgba(75,85,99,.9);
      text-transform: uppercase;
      font-size: .7rem;
      letter-spacing: .06em;
      color: #9ca3af;
    }
    .table-dark tbody tr:nth-child(even) {
      background-color: rgba(15,23,42,.7);
    }
    .table-dark tbody tr:hover {
      background-color: rgba(30,64,175,.35);
    }
    .pnl-pos {
      color: #4ade80;
      font-weight: 500;
    }
    .pnl-neg {
      color: #f87171;
      font-weight: 500;
    }
    .nav-link {
      font-size: .8rem;
      text-transform: uppercase;
      letter-spacing: .08em;
    }
  </style>
</head>
<body>
<nav class="navbar navbar-dark mb-3">
  <div class="container-fluid d-flex justify-content-between align-items-center">
    <div class="d-flex align-items-center gap-3">
      <span class="navbar-brand mb-0 h1">TRADE JOURNAL</span>
    </div>
    <div>
      <a href="/" class="btn btn-sm btn-outline-light">Back to dashboard</a>
    </div>
  </div>
</nav>

<div class="container-fluid mb-3">
  <div class="card">
    <div class="card-header">
      <h5 class="mb-0">Recent trades ({{ records|length }})</h5>
    </div>
    <div class="card-body p-0">
      {% if records %}
      <div class="table-responsive">
        <table class="table table-dark table-sm mb-0 align-middle">
          <thead>
            <tr>
              <th>Opened</th>
              <th>Closed</th>
              <th>Symbol</th>
              <th>Side</th>
              <th>Entry</th>
              <th>SL</th>
              <th>TP</th>
              <th>Exit type</th>
              <th>P&L</th>
              <th>R:R</th>
              <th>MFE%</th>
              <th>MAE%</th>
              <th>Duration</th>
            </tr>
          </thead>
          <tbody>
            {% for r in records %}
            <tr>
              <td>{{ r.opened_at }}</td>
              <td>{{ r.closed_at or "-" }}</td>
              <td>{{ r.symbol }}</td>
              <td>{{ r.side }}</td>
              <td>{{ r.entry }}</td>
              <td>{{ r.sl }}</td>
              <td>{{ r.tp }}</td>
              <td>{{ r.exit_type or "-" }}</td>
              <td>
                {% if r.pnl is not none %}
                  {% if r.pnl >= 0 %}
                    <span class="pnl-pos">{{ r.pnl }} ({{ r.pnl_pct }}%)</span>
                  {% else %}
                    <span class="pnl-neg">{{ r.pnl }} ({{ r.pnl_pct }}%)</span>
                  {% endif %}
                {% else %}
                  -
                {% endif %}
              </td>
              <td>{{ r.rr }}</td>
              <td>
                {% if r.mfe_pct is not none %}
                  {{ r.mfe_pct }}%
                {% else %}
                  -
                {% endif %}
              </td>
              <td>
                {% if r.mae_pct is not none %}
                  {{ r.mae_pct }}%
                {% else %}
                  -
                {% endif %}
              </td>
              <td>{{ r.duration or "-" }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      {% else %}
      <div class="p-3 text-muted">No trades in journal yet</div>
      {% endif %}
    </div>
  </div>
</div>
</body>
</html>
"""
    return render_template_string(html, records=records)


@app.route("/api/status")
def api_status():
    return jsonify(dashboard_data)


@app.route("/config", methods=["GET", "POST"])
@requires_login
def api_config():
    if request.method == "GET":
        return jsonify(load_config())
    cfg = load_config()
    data = request.json or {}

    for key in (
        "confidence_threshold",
        "max_open_positions",
        "default_position_size_usd",
        "sl_pct_long",
        "tp_pct_long",
        "sl_pct_short",
        "tp_pct_short",
        "cooldown_hours",
        "live_trading_mode",
        "journal_page_size",
        "account_equity_usd",
        "risk_per_trade_pct",
        "daily_risk_limit_usd",
        "daily_risk_limit_enabled",
    ):
        if key in data:
            cfg[key] = data[key]

    if "symbol_position_size" in data and isinstance(
        data["symbol_position_size"], dict
    ):
        cfg.setdefault("symbol_position_size", {}).update(data["symbol_position_size"])

    save_config(cfg)
    return jsonify(cfg)


@app.route("/closeposition", methods=["POST"])
@requires_login
def close_position():
    data = request.json or {}
    symbol = data.get("symbol")
    if not symbol:
        return jsonify({"error": "symbol required"}), 400

    positions = load_positions()
    if symbol not in positions:
        return jsonify({"error": "no such open position"}), 404

    position = positions.pop(symbol)
    save_positions(positions)

    cfg = load_config()
    cooldown_hours = cfg.get("cooldown_hours", 1.0)

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        df_last = fetch_candles_binance(symbol=symbol, interval=TIMEFRAME, limit=1)
        if df_last is not None and not df_last.empty:
            current_price = float(df_last.iloc[-1]["close"])
        else:
            current_price = float(position["entry"])
    except Exception:
        current_price = float(position["entry"])

    entry = float(position["entry"])
    side = position["action"]
    size_usd = float(position.get("position_size_usd", 0.0))

    if side == "BUY":
        pnl_pct = (current_price - entry) / entry * 100.0
    else:
        pnl_pct = (entry - current_price) / entry * 100.0

    pnl_usd = size_usd * (pnl_pct / 100.0)
    mfe_pct = float(position.get("mfe_pct", 0.0))
    mae_pct = float(position.get("mae_pct", 0.0))

    try:
        with open("trade_results.log", "a", encoding="utf-8") as f:
            log_msg = (
                f"[{timestamp}] {symbol}: CLOSED @ FORCE | "
                f"P&L: ${pnl_usd:.2f} ({pnl_pct:.2f}%) | "
                f"MFE: {mfe_pct:.2f}% | MAE: {mae_pct:.2f}%"
            )
            f.write(log_msg + "\n")
    except Exception:
        pass

    from datetime import timedelta

    unblock_time = datetime.now() + timedelta(hours=float(cooldown_hours))
    force_closed_until[symbol] = unblock_time.strftime("%Y-%m-%d %H:%M:%S")

    dashboard_data["closed_trades"].insert(
        0,
        {
            "symbol": symbol,
            "exit_type": "FORCE",
            "pnl": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 2),
            "timestamp": timestamp,
        },
    )
    dashboard_data["closed_trades"] = dashboard_data["closed_trades"][:10]

    return jsonify(
        {
            "symbol": symbol,
            "closed_at": timestamp,
            "pnlusd": round(pnl_usd, 2),
            "pnlpct": round(pnl_pct, 2),
        }
    )


def live_worker():
    while True:
        try:
            run_live_cycle(dashboard_data, force_closed_until)
        except Exception as e:
            print("Error in live cycle:", e)
        time.sleep(10)


def main():
    t = Thread(target=live_worker, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    main()
