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
import os
import math
import plistlib
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src import config as bot_config  # noqa: E402
from src import state_store  # noqa: E402
from src.backtest.engine import compute_stats  # noqa: E402

CONFIG_PATH = BASE_DIR / "config" / "paper.env"
SECRET_PATH = BASE_DIR / "config" / "dashboard_secret.json"
RUNTIME_LABEL = "com.ai-crypto.paper"
RUNTIME_LOG_PATH = BASE_DIR / "logs" / "run_paper_launchd.log"
RUNTIME_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{RUNTIME_LABEL}.plist"
DEFAULT_DB_PATH = str(BASE_DIR / "data" / "state_paper.db")
BACKTEST_30D_PATH = BASE_DIR / "data" / "backtests" / "sr_30d_latest.json"
_BACKTEST_CACHE = {"mtime_ns": None, "data": None}

# Field cho phép sửa qua dashboard — DB_PATH/LOG_PATH cố tình không cho sửa qua UI
# (đổi sai có thể trỏ nhầm DB, làm mất khả năng phân biệt với live 5m).
EDITABLE_FIELDS = {
    "EXCHANGE_ID": str,
    "SCORING_PROFILE": str,
    "TIMEFRAME": str,
    "MTF_TIMEFRAMES": str,
    "BUY_SCORE_THRESHOLD": float,
    "WATCH_SCORE_THRESHOLD": float,
    "PULLBACK_ATR_BUFFER": float,
    "MONITOR_WINDOW_MINUTES": float,
    "MONITOR_POLL_SECONDS": float,
    "ACTIVATION_INTERVAL_MINUTES": float,
    "STRATEGY_LABEL": str,
}

# Field chỉ nhận 1 trong tập giá trị cố định (dropdown ở UI) — sai giá trị sẽ làm
# market.get_exchange() crash (getattr(ccxt, EXCHANGE_ID) không tồn tại), nên chặn
# ở đây thay vì chỉ ép kiểu str chung chung như các field khác.
FIELD_CHOICES = {
    "EXCHANGE_ID": {"binance", "okx"},
    "SCORING_PROFILE": {"champion", "support_resistance_only"},
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
        "effective_source": str(CONFIG_PATH),
        "restart_required_after_change": True,
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
    return jsonify({
        "ok": True,
        "config": {k: cfg.get(k, "") for k in EDITABLE_FIELDS},
        "effective_source": str(CONFIG_PATH),
        "restart_required": True,
    })


# -------------------------------------------------------------------- runtime

def _runtime_status() -> dict:
    """Trạng thái launchd thật; scheduler Python neo start-to-start, không cron."""
    cfg = _read_config()
    continuous = cfg.get("RUN_CONTINUOUS", "false").lower() in ("1", "true", "yes", "on")
    scheduled = cfg.get("RUN_SCHEDULED", "false").lower() in ("1", "true", "yes", "on")
    try:
        poll_seconds = float(cfg.get("MONITOR_POLL_SECONDS", "5"))
        refresh_minutes = float(cfg.get("MONITOR_WINDOW_MINUTES", "5"))
        activation_minutes = float(cfg.get("ACTIVATION_INTERVAL_MINUTES", "60"))
    except ValueError:
        poll_seconds, refresh_minutes, activation_minutes = None, None, None
    target = f"gui/{os.getuid()}/{RUNTIME_LABEL}"
    result = subprocess.run(
        ["launchctl", "print", target], capture_output=True, text=True,
    )
    output = result.stdout if result.returncode == 0 else ""
    state_match = re.search(r"^\s*state = (\S+)", output, re.MULTILINE)
    pid_match = re.search(r"^\s*pid = (\d+)", output, re.MULTILINE)
    state = state_match.group(1) if state_match else "not_loaded"
    return {
        "label": RUNTIME_LABEL,
        "mode": "continuous_daemon" if continuous else ("scheduled_window" if scheduled else "single_cycle"),
        "loaded": result.returncode == 0,
        "running": state == "running",
        "state": state,
        "pid": int(pid_match.group(1)) if pid_match else None,
        "poll_seconds": poll_seconds,
        "refresh_minutes": refresh_minutes,
        "activation_minutes": activation_minutes,
    }


def _apply_runtime_schedule(activation_minutes: float) -> None:
    """Để scheduler nhẹ sống bằng KeepAlive; cadence nằm trong Python runtime."""
    if not RUNTIME_PLIST_PATH.exists():
        raise RuntimeError(f"Không tìm thấy plist: {RUNTIME_PLIST_PATH}")
    old_bytes = RUNTIME_PLIST_PATH.read_bytes()
    plist = plistlib.loads(old_bytes)
    plist.pop("StartInterval", None)
    plist["KeepAlive"] = True
    plist["RunAtLoad"] = True
    target = f"gui/{os.getuid()}/{RUNTIME_LABEL}"
    domain = f"gui/{os.getuid()}"
    try:
        RUNTIME_PLIST_PATH.write_bytes(plistlib.dumps(plist, fmt=plistlib.FMT_XML))
        subprocess.run(["launchctl", "bootout", target], capture_output=True, text=True)
        time.sleep(1)
        result = subprocess.run(
            ["launchctl", "bootstrap", domain, str(RUNTIME_PLIST_PATH)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "launchctl bootstrap thất bại")
    except Exception:
        RUNTIME_PLIST_PATH.write_bytes(old_bytes)
        subprocess.run(["launchctl", "bootout", target], capture_output=True, text=True)
        time.sleep(1)
        subprocess.run(
            ["launchctl", "bootstrap", domain, str(RUNTIME_PLIST_PATH)],
            capture_output=True, text=True,
        )
        raise


@app.route("/api/runtime/config", methods=["POST"])
@login_required
def api_runtime_config():
    """Lưu activation/window/poll và áp dụng scheduler launchd ngay."""
    _use_paper_db()
    if state_store.get_open_positions():
        return jsonify({"ok": False, "error": "Không đổi scheduler khi đang có vị thế mở"}), 409
    data = request.get_json(force=True) or {}
    try:
        activation = float(data.get("activation_minutes"))
        window = float(data.get("window_minutes"))
        poll = float(data.get("poll_seconds"))
        if not all(math.isfinite(v) for v in (activation, window, poll)):
            raise ValueError
        if not (1 <= activation <= 10080):
            raise ValueError("Kích hoạt phải trong 1–10080 phút")
        if not (0.5 <= window <= 1440):
            raise ValueError("Monitor window phải trong 0.5–1440 phút")
        if window > activation:
            raise ValueError("Monitor window không được lớn hơn tần suất kích hoạt")
        if not (1 <= poll <= 300):
            raise ValueError("Poll phải trong 1–300 giây")
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc) or "Cấu hình runtime không hợp lệ"}), 400

    cfg = _read_config()
    old_config = CONFIG_PATH.read_text()
    cfg["ACTIVATION_INTERVAL_MINUTES"] = str(activation)
    cfg["MONITOR_WINDOW_MINUTES"] = str(window)
    cfg["MONITOR_POLL_SECONDS"] = str(poll)
    cfg["RUN_SCHEDULED"] = "true"
    cfg["RUN_CONTINUOUS"] = "false"
    try:
        _write_config(cfg)
        _apply_runtime_schedule(activation)
    except Exception as exc:  # rollback config; plist rollback nằm trong helper
        CONFIG_PATH.write_text(old_config)
        return jsonify({"ok": False, "error": f"Không áp dụng được scheduler: {exc}"}), 500
    return jsonify({"ok": True, "runtime": _runtime_status()})


# ---------------------------------------------------------------------- status

@app.route("/api/status")
@login_required
def api_status():
    _use_paper_db()
    positions = state_store.get_open_positions()
    # Dashboard hiện chỉ hiển thị 1 card vị thế (MAX_CONCURRENT_POSITIONS mặc định
    # 1) — giữ "position" trả về vị thế đầu tiên (hoặc dict rỗng status WAIT) để
    # không phá UI hiện tại; "positions" là danh sách đầy đủ cho khi UI cần hiển
    # thị nhiều vị thế cùng lúc.
    position = positions[0] if positions else {
        "status": "WAIT", "trade_id": None, "symbol": None, "entry_price": None,
        "entry_time": None, "entry_score": None, "stop_price": None,
        "take_profit_price": None, "size_usd": None,
    }
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
    if positions:
        tick = state_store.get_latest_event("MARKET_TICK")
        if tick:
            payload = tick["payload"]
            latest_tick = {"ts": tick["ts"], "price": payload.get("price"), "total_score": payload.get("total_score")}

    return jsonify({
        "position": position,
        "positions": positions,
        "max_concurrent_positions": bot_config.MAX_CONCURRENT_POSITIONS,
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
        "runtime": _runtime_status(),
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
    if not RUNTIME_LOG_PATH.exists():
        return jsonify({"lines": [], "path": str(RUNTIME_LOG_PATH)})
    text = RUNTIME_LOG_PATH.read_text(errors="replace")
    lines = text.splitlines()[-n:]
    return jsonify({"lines": lines, "path": str(RUNTIME_LOG_PATH)})


@app.route("/api/ticks")
@login_required
def api_ticks():
    _use_paper_db()
    limit = min(int(request.args.get("limit", 50)), 500)
    return jsonify({"ticks": state_store.get_recent_events("MARKET_TICK", limit=limit)})


def _parse_history_time(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Thời gian phải theo định dạng ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("Thời gian phải có timezone")
    return parsed.astimezone(timezone.utc)


def _downsample_score_points(points: list[dict], max_points: int) -> list[dict]:
    """Giảm mẫu nhưng giữ min/max score của từng bucket để không mất spike."""
    if len(points) <= max_points:
        return points
    bucket_count = max(1, max_points // 2)
    bucket_size = math.ceil(len(points) / bucket_count)
    sampled = []
    for start in range(0, len(points), bucket_size):
        bucket = points[start:start + bucket_size]
        score_min = min(range(len(bucket)), key=lambda i: bucket[i]["score"])
        score_max = max(range(len(bucket)), key=lambda i: bucket[i]["score"])
        for index in sorted({score_min, score_max}):
            sampled.append(bucket[index])
    return sampled


def _load_backtest_timeline() -> tuple[list[dict], dict]:
    if not BACKTEST_30D_PATH.exists():
        raise FileNotFoundError("Chưa có artifact backtest 30 ngày")
    mtime_ns = BACKTEST_30D_PATH.stat().st_mtime_ns
    if _BACKTEST_CACHE["mtime_ns"] != mtime_ns:
        result = json.loads(BACKTEST_30D_PATH.read_text(encoding="utf-8"))
        _BACKTEST_CACHE["mtime_ns"] = mtime_ns
        _BACKTEST_CACHE["data"] = result
    result = _BACKTEST_CACHE["data"]
    return result.get("score_timeline") or [], result


def _timeline_point_time(point: dict) -> datetime:
    parsed = datetime.fromisoformat(point["ts"].replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@app.route("/api/score-history")
@login_required
def api_score_history():
    """Timeline MARKET_TICK cho biểu đồ Score/Giá theo ngày và giờ."""
    now = datetime.now(timezone.utc)
    try:
        start = _parse_history_time(request.args.get("from"), now - timedelta(hours=6))
        end = _parse_history_time(request.args.get("to"), now)
        max_points = min(max(int(request.args.get("max_points", 1600)), 250), 4000)
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    if end <= start:
        return jsonify({"error": "Mốc Đến phải sau mốc Từ"}), 400
    if end - start > timedelta(days=31):
        return jsonify({"error": "Khoảng xem tối đa là 31 ngày"}), 400

    source = request.args.get("source", "paper")
    backtest_result = None
    if source == "backtest_30d":
        try:
            timeline, backtest_result = _load_backtest_timeline()
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            return jsonify({"error": str(exc)}), 404
        points = [point for point in timeline if start <= _timeline_point_time(point) <= end]
    elif source == "paper":
        _use_paper_db()
        events = state_store.get_events_in_range(
            "MARKET_TICK", start.isoformat(), end.isoformat()
        )
        points = []
        for event in events:
            payload = event["payload"]
            monitor = payload.get("sr_monitor") or {}
            try:
                price = float(payload["price"])
                score = float(payload["total_score"])
            except (KeyError, TypeError, ValueError):
                continue
            points.append({
                "ts": event["ts"],
                "price": price,
                "score": score,
                "action": payload.get("action"),
                "reason": payload.get("reason"),
                "score_side": payload.get("score_side"),
                "support_status": monitor.get("support_status"),
                "resistance_status": monitor.get("resistance_status"),
                "buy_eligible": monitor.get("buy_eligible"),
            })
    else:
        return jsonify({"error": "Nguồn dữ liệu không hợp lệ"}), 400

    scores = [point["score"] for point in points]
    sampled = _downsample_score_points(points, max_points)
    cfg = _read_config()
    sr_only = cfg.get("SCORING_PROFILE") == "support_resistance_only"
    buy_key = "SR_DECISION_THRESHOLD" if sr_only else "BUY_SCORE_THRESHOLD"
    backtest_threshold = ((backtest_result or {}).get("manifest") or {}).get("sr", {}).get("decision_threshold")
    return jsonify({
        "source": source,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "points": sampled,
        "thresholds": {
            "buy": float(backtest_threshold or cfg.get(buy_key, 70)),
            "watch": float(cfg.get("WATCH_SCORE_THRESHOLD", 55)),
        },
        "summary": {
            "raw_points": len(points),
            "returned_points": len(sampled),
            "min_score": min(scores) if scores else None,
            "max_score": max(scores) if scores else None,
            "avg_score": (sum(scores) / len(scores)) if scores else None,
        },
    })


@app.route("/api/score-detail")
@login_required
def api_score_detail():
    """Chi tiết 'tính score như nào' của cycle gần nhất (`SCORE_COMPUTED`,
    xem run.py) — mỗi thành phần góp vào total_score + breakdown Technical."""
    _use_paper_db()
    latest = state_store.get_latest_event("SCORE_COMPUTED")
    if not latest:
        return jsonify({"available": False})
    return jsonify({"available": True, "ts": latest["ts"], **latest["payload"]})


@app.route("/api/support-resistance")
@login_required
def api_support_resistance():
    _use_paper_db()
    latest = state_store.get_latest_event("MARKET_TICK")
    if not latest:
        return jsonify({"available": False})
    payload = latest["payload"]
    score_event = state_store.get_latest_event("SCORE_COMPUTED")
    return jsonify({
        "available": True,
        "ts": latest["ts"],
        "price": payload.get("price"),
        "action": payload.get("action"),
        "total_score": payload.get("total_score"),
        "score_side": payload.get("score_side"),
        "monitor": payload.get("sr_monitor"),
        "diagnostics": (
            score_event["payload"].get("support_resistance_diagnostics")
            if score_event else None
        ),
    })


# --------------------------------------------------------------------- trades

@app.route("/api/trades")
@login_required
def api_trades():
    source = request.args.get("source", "paper")
    if source == "backtest_30d":
        try:
            _timeline, result = _load_backtest_timeline()
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            return jsonify({"error": str(exc)}), 404
        trades = [{
            "status": "CLOSED",
            "entry_time": trade.get("entry_time"),
            "entry_price": trade.get("entry_price"),
            "entry_score": trade.get("entry_score"),
            "exit_time": trade.get("exit_time"),
            "exit_price": trade.get("exit_price"),
            "pnl_pct": trade.get("net_pnl_pct"),
            "pnl_usd": (trade.get("accounting") or {}).get("net_pnl_usd"),
            "equity_before_usd": (trade.get("accounting") or {}).get("equity_before_usd"),
            "equity_after_usd": (trade.get("accounting") or {}).get("equity_after_usd"),
            "exit_reason": trade.get("reason"),
            "stop_price": trade.get("stop_price"),
            "take_profit_price": trade.get("take_profit_price"),
            "tp_reason": trade.get("tp_reason"),
        } for trade in reversed(result.get("trades") or [])]
        return jsonify({
            "source": source,
            "trades": trades,
            "n_total": len(trades),
            "n_open": 0,
            "n_closed": len(trades),
            "stats": result.get("net") or {},
        })
    if source != "paper":
        return jsonify({"error": "Nguồn dữ liệu không hợp lệ"}), 400

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
        "source": source,
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
    # The default stays loopback-only: this server is reachable from outside the
    # machine through a tunnel that dials out, never by binding a public
    # interface. A container is the one case where 127.0.0.1 is wrong — it means
    # the loopback *inside* the container, so a published port reaches nothing.
    # compose.yaml sets DASHBOARD_HOST=0.0.0.0 and publishes the port on the
    # host's loopback instead, which keeps the same reachability with the bind
    # moved one layer out.
    host = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("DASHBOARD_PORT", "8787"))
    print(f"[dashboard] http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
