"""Dashboard quản lý Paper Trading.

Flask app local — có auth (session + password) vì sẽ expose ra ngoài qua tunnel
(Cloudflare Tunnel/Tailscale) để truy cập từ xa, không chỉ localhost. Bind mặc định
127.0.0.1 — tunnel kết nối vào cổng này qua outbound connection, không cần mở port
router hay bind 0.0.0.0.

Chạy: python3 scripts/dashboard_server.py
Lần đầu chạy sẽ tự sinh mật khẩu ngẫu nhiên, in ra console DUY NHẤT 1 lần — lưu lại,
không hiện lại được (chỉ có hash trong config/dashboard_secret.json). Đổi mật khẩu:
xoá file đó rồi chạy lại để sinh mật khẩu mới.
"""
import json
import re
import secrets
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src import config as bot_config  # noqa: E402
from src import state_store  # noqa: E402
from src.backtest.engine import compute_stats  # noqa: E402

CONFIG_PATH = BASE_DIR / "config" / "paper.env"
DASHBOARD_STATE_PATH = BASE_DIR / "config" / "dashboard_state.json"
SECRET_PATH = BASE_DIR / "config" / "dashboard_secret.json"
SCRIPT_PATH = str(BASE_DIR / "scripts" / "run_paper.sh")
CRON_LOG_PATH = BASE_DIR / "logs" / "run_paper_cron.log"
DEFAULT_DB_PATH = str(BASE_DIR / "data" / "state_paper.db")

# Field cho phép sửa qua dashboard — DB_PATH/LOG_PATH cố tình không cho sửa qua UI
# (đổi sai có thể trỏ nhầm DB, làm mất khả năng phân biệt với live 5m).
EDITABLE_FIELDS = {
    "EXCHANGE_ID": str,
    "TIMEFRAME": str,
    "MTF_TIMEFRAMES": str,
    "BUY_SCORE_THRESHOLD": float,
    "WATCH_SCORE_THRESHOLD": float,
    "PULLBACK_ATR_BUFFER": float,
    "MONITOR_WINDOW_MINUTES": float,
    "MONITOR_POLL_SECONDS": float,
    "STRATEGY_LABEL": str,
}

# Field chỉ nhận 1 trong tập giá trị cố định (dropdown ở UI) — sai giá trị sẽ làm
# market.get_exchange() crash (getattr(ccxt, EXCHANGE_ID) không tồn tại), nên chặn
# ở đây thay vì chỉ ép kiểu str chung chung như các field khác.
FIELD_CHOICES = {
    "EXCHANGE_ID": {"binance", "okx"},
}


def _load_secret():
    if SECRET_PATH.exists():
        return json.loads(SECRET_PATH.read_text())
    password = secrets.token_urlsafe(12)
    data = {"password_hash": generate_password_hash(password), "flask_secret": secrets.token_hex(32)}
    SECRET_PATH.write_text(json.dumps(data, indent=2))
    print("=" * 60)
    print(f"[dashboard] Mật khẩu đăng nhập (chỉ hiện 1 lần): {password}")
    print(f"[dashboard] Lưu lại ngay — xoá {SECRET_PATH} để sinh mật khẩu mới.")
    print("=" * 60)
    return data


SECRET = _load_secret()

app = Flask(__name__, static_folder=str(BASE_DIR / "scripts" / "dashboard_static"))
app.secret_key = SECRET["flask_secret"]


# ------------------------------------------------------------------------- auth

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("authed"):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


@app.route("/api/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True) or {}
    password = data.get("password", "")
    if check_password_hash(SECRET["password_hash"], password):
        session["authed"] = True
        session.permanent = True
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Sai mật khẩu"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/session")
def api_session():
    return jsonify({"authed": bool(session.get("authed"))})


# --------------------------------------------------------------------- config

def _read_config() -> dict:
    """Đọc `KEY="value"` — bắt buộc phải có quote (xem `_write_config`), giá trị
    có khoảng trắng (vd STRATEGY_LABEL) mà không quote sẽ làm bash `source` vỡ
    (phát hiện 2026-08-05: "2h: command not found" — bash coi phần sau dấu cách
    là 1 command riêng, không phải 1 phần của giá trị)."""
    cfg = {}
    if not CONFIG_PATH.exists():
        return cfg
    for line in CONFIG_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
            v = v[1:-1]
        cfg[k.strip()] = v
    return cfg


def _write_config(cfg: dict):
    """Luôn quote giá trị — an toàn cho `source` trong bash kể cả khi giá trị có
    khoảng trắng (vd STRATEGY_LABEL="PAPER 2h")."""
    lines = [f'{k}="{v}"' for k, v in cfg.items()]
    CONFIG_PATH.write_text("\n".join(lines) + "\n")


def _db_path() -> str:
    return _read_config().get("DB_PATH", DEFAULT_DB_PATH)


def _use_paper_db():
    """`state_store` đọc `config.DB_PATH` tại thời điểm gọi (không cache) — trỏ
    lại đúng DB paper trading hiện tại trước mỗi thao tác, phòng trường hợp user
    đổi DB_PATH thủ công trong config/paper.env."""
    bot_config.DB_PATH = Path(_db_path())


@app.route("/api/config")
@login_required
def api_get_config():
    cfg = _read_config()
    values = {k: cfg.get(k, "") for k in EDITABLE_FIELDS}
    if "EXCHANGE_ID" not in cfg:
        values["EXCHANGE_ID"] = bot_config.EXCHANGE_ID  # chưa set riêng thì đang thừa kế từ .env chung
    return jsonify({
        "values": values,
        "choices": {k: sorted(v) for k, v in FIELD_CHOICES.items()},
    })


@app.route("/api/config", methods=["POST"])
@login_required
def api_set_config():
    data = request.get_json(force=True) or {}
    cfg = _read_config()
    errors = {}
    for key, value in data.items():
        if key not in EDITABLE_FIELDS:
            errors[key] = "Trường không cho phép sửa"
            continue
        caster = EDITABLE_FIELDS[key]
        try:
            cast_value = str(caster(value)) if caster is not str else str(value).strip()
        except (TypeError, ValueError):
            errors[key] = "Giá trị không hợp lệ"
            continue
        choices = FIELD_CHOICES.get(key)
        if choices and cast_value not in choices:
            errors[key] = f"Chỉ nhận 1 trong: {', '.join(sorted(choices))}"
            continue
        cfg[key] = cast_value
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400
    _write_config(cfg)
    return jsonify({"ok": True, "config": {k: cfg.get(k, "") for k in EDITABLE_FIELDS}})


# ----------------------------------------------------------------------- cron

def _load_dashboard_state() -> dict:
    if DASHBOARD_STATE_PATH.exists():
        return json.loads(DASHBOARD_STATE_PATH.read_text())
    return {"cron_enabled": False, "cron_minutes": 30}


def _save_dashboard_state(state: dict):
    DASHBOARD_STATE_PATH.write_text(json.dumps(state, indent=2))


def _read_crontab() -> list[str]:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return [l for l in result.stdout.splitlines() if l.strip()]


def _write_crontab(lines: list[str]):
    content = ("\n".join(lines) + "\n") if lines else ""
    subprocess.run(["crontab", "-"], input=content, text=True, check=True)


def _cron_line(minutes: int) -> str:
    return f"*/{minutes} * * * * {SCRIPT_PATH} >> {CRON_LOG_PATH} 2>&1"


def _next_cron_run_iso(minutes: int) -> str:
    """Cron `*/N * * * *` kích hoạt khi phút chia hết cho N — tính đúng mốc kế
    tiếp theo lịch, không suy từ last_run_at (lần chạy tay/lỗi sẽ làm sai lệch)."""
    now = datetime.now(timezone.utc)
    base = now.replace(second=0, microsecond=0)
    remainder = base.minute % minutes
    if remainder == 0 and now.second == 0:
        next_time = base
    else:
        next_time = base + timedelta(minutes=(minutes - remainder if remainder else minutes))
    return next_time.isoformat()


def _cron_status() -> dict:
    lines = _read_crontab()
    ours = [l for l in lines if SCRIPT_PATH in l]
    state = _load_dashboard_state()
    minutes = state.get("cron_minutes", 30)
    if ours:
        m = re.match(r"\*/(\d+)", ours[0])
        if m:
            minutes = int(m.group(1))
    enabled = bool(ours)
    return {
        "enabled": enabled, "minutes": minutes, "line": ours[0] if ours else None,
        "next_run_at": _next_cron_run_iso(minutes) if enabled else None,
    }


@app.route("/api/cron")
@login_required
def api_cron_status():
    return jsonify(_cron_status())


@app.route("/api/run-now", methods=["POST"])
@login_required
def api_run_now():
    """Trigger tay ngay lập tức — không đợi lịch cron. `run.py` tự dùng
    `run_lock()` nên gọi tay lúc cron đang chạy chỉ bị bỏ qua an toàn, không
    xung đột. Chạy nền (Popen, không đợi) vì 1 lần chạy có thể mất tới
    MONITOR_WINDOW_MINUTES phút (cửa sổ theo dõi) — request không nên treo lâu
    vậy."""
    CRON_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CRON_LOG_PATH, "a") as log_f:
        subprocess.Popen([SCRIPT_PATH], stdout=log_f, stderr=log_f, start_new_session=True)
    return jsonify({"ok": True, "message": "Đã trigger — theo dõi qua log/tick feed."})


@app.route("/api/cron/toggle", methods=["POST"])
@login_required
def api_cron_toggle():
    data = request.get_json(force=True) or {}
    enabled = bool(data.get("enabled"))
    lines = [l for l in _read_crontab() if SCRIPT_PATH not in l]
    state = _load_dashboard_state()
    state["cron_enabled"] = enabled
    if enabled:
        lines.append(_cron_line(state.get("cron_minutes", 30)))
    _write_crontab(lines)
    _save_dashboard_state(state)
    return jsonify(_cron_status())


@app.route("/api/cron/frequency", methods=["POST"])
@login_required
def api_cron_frequency():
    data = request.get_json(force=True) or {}
    try:
        minutes = int(data.get("minutes"))
        if not (1 <= minutes <= 1440):
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Tần suất phải là số nguyên 1-1440 phút"}), 400
    state = _load_dashboard_state()
    state["cron_minutes"] = minutes
    lines = [l for l in _read_crontab() if SCRIPT_PATH not in l]
    if state.get("cron_enabled", True):
        lines.append(_cron_line(minutes))
    _write_crontab(lines)
    _save_dashboard_state(state)
    return jsonify(_cron_status())


# ---------------------------------------------------------------------- status

@app.route("/api/status")
@login_required
def api_status():
    _use_paper_db()
    position = state_store.get_position_state()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with state_store.get_conn() as conn:
        row = conn.execute(
            "SELECT realized_pnl_pct FROM daily_pnl WHERE trade_date = ?", (today,)
        ).fetchone()
        daily_pnl_pct = row[0] if row else 0.0
        health_row = conn.execute(
            "SELECT last_run_at, last_run_ok FROM run_health WHERE id = 1"
        ).fetchone()

    latest_tick = None
    if position["status"] == "IN_POSITION":
        ticks = state_store.get_events(event_type="MARKET_TICK", limit=10000)
        if ticks:
            payload = ticks[-1]["payload"]
            latest_tick = {"ts": ticks[-1]["ts"], "price": payload.get("price"), "total_score": payload.get("total_score")}

    return jsonify({
        "position": position,
        "latest_tick": latest_tick,
        "monitoring": state_store.get_run_lock_status(),
        "kill_switch_on": state_store.is_kill_switch_on(),
        "kill_switch_reason": state_store.get_kill_switch_reason(),
        "trading_halted_today": state_store.is_trading_halted_today(),
        "daily_pnl_pct": daily_pnl_pct,
        "max_drawdown_pct": state_store.get_max_drawdown_pct(),
        "run_health": {
            "last_run_at": health_row[0] if health_row else None,
            "last_run_ok": bool(health_row[1]) if health_row else None,
        },
        "cron": _cron_status(),
    })


@app.route("/api/kill-switch", methods=["POST"])
@login_required
def api_kill_switch():
    _use_paper_db()
    data = request.get_json(force=True) or {}
    on = bool(data.get("on"))
    reason = (data.get("reason") or "").strip() or "Bật/tắt thủ công từ dashboard"
    state_store.set_kill_switch(on, reason=reason if on else "")
    return jsonify({"ok": True, "kill_switch_on": state_store.is_kill_switch_on()})


# ----------------------------------------------------------------------- logs

@app.route("/api/logs")
@login_required
def api_logs():
    n = min(int(request.args.get("lines", 200)), 2000)
    if not CRON_LOG_PATH.exists():
        return jsonify({"lines": [], "path": str(CRON_LOG_PATH)})
    text = CRON_LOG_PATH.read_text(errors="replace")
    lines = text.splitlines()[-n:]
    return jsonify({"lines": lines, "path": str(CRON_LOG_PATH)})


@app.route("/api/ticks")
@login_required
def api_ticks():
    _use_paper_db()
    limit = min(int(request.args.get("limit", 50)), 500)
    events = state_store.get_events(event_type="MARKET_TICK", limit=10000)
    return jsonify({"ticks": events[-limit:][::-1]})


@app.route("/api/score-detail")
@login_required
def api_score_detail():
    """Chi tiết 'tính score như nào' của lần cron gần nhất (`SCORE_COMPUTED`,
    xem run.py) — mỗi thành phần góp vào total_score + breakdown Technical."""
    _use_paper_db()
    events = state_store.get_events(event_type="SCORE_COMPUTED", limit=10000)
    if not events:
        return jsonify({"available": False})
    latest = events[-1]
    return jsonify({"available": True, "ts": latest["ts"], **latest["payload"]})


# --------------------------------------------------------------------- trades

@app.route("/api/trades")
@login_required
def api_trades():
    _use_paper_db()
    entry_events = state_store.get_events(event_type="ENTRY", limit=10000)
    trade_ids = [e["trade_id"] for e in entry_events if e["trade_id"]]
    summaries = [state_store.get_trade_summary(tid) for tid in trade_ids]
    summaries = [s for s in summaries if s]
    summaries.sort(key=lambda s: s.get("entry_time", ""), reverse=True)

    closed = [s for s in summaries if s["status"] == "CLOSED"]
    pnl_pcts = [s["pnl_pct"] for s in closed if s.get("pnl_pct") is not None]
    stats = compute_stats(pnl_pcts, init_cash=100.0)

    return jsonify({
        "trades": summaries,
        "n_total": len(summaries),
        "n_open": len(summaries) - len(closed),
        "n_closed": len(closed),
        "stats": stats,
    })


# --------------------------------------------------------------------- static

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    port = 8787
    print(f"[dashboard] http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
