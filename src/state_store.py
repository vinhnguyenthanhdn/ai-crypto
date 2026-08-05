"""SQLite state store: state machine, run lock, và signal log.

Mỗi lần cron gọi là một process mới nên toàn bộ state phải nằm ngoài process —
không giữ gì trong memory giữa các lần chạy.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS position_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL DEFAULT 'WAIT',
    symbol TEXT,
    entry_price REAL,
    entry_time TEXT,
    entry_score REAL,
    stop_price REAL,
    take_profit_price REAL,
    size_usd REAL
);

CREATE TABLE IF NOT EXISTS run_lock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    pid INTEGER,
    started_at TEXT
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    trade_date TEXT PRIMARY KEY,
    realized_pnl_pct REAL NOT NULL DEFAULT 0,
    trading_halted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS signal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT,
    price REAL,
    decision TEXT,
    total_score REAL,
    layer_scores TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS run_health (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_run_at TEXT,
    last_run_ok INTEGER
);

CREATE TABLE IF NOT EXISTS kv_store (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- Feature Store: raw feature mỗi lần chạy, tách khỏi signal_log (chỉ có score)
-- — dùng để train Entry Model và truy vết Feature Lineage.
CREATE TABLE IF NOT EXISTS feature_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT,
    price REAL,
    features TEXT NOT NULL
);

-- Raw Event: Event Sourcing, không overwrite, nguồn sự thật cho lifecycle 1 trade
-- (Signal -> Risk Check -> Entry -> Monitoring -> Exit). payload JSON đóng luôn
-- vai trò Snapshot cho type ENTRY/EXIT.
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    trade_id TEXT,
    type TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_conn():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), timeout=10)
    try:
        conn.executescript(_SCHEMA)
        try:
            conn.execute("ALTER TABLE position_state ADD COLUMN entry_score REAL")
        except sqlite3.OperationalError:
            pass  # cột đã tồn tại (DB tạo trước khi thêm entry_score)
        yield conn
        conn.commit()
    finally:
        conn.close()


class RunAlreadyInProgress(Exception):
    pass


@contextmanager
def run_lock(stale_after_seconds=180):
    """Chặn cron chạy chồng.

    Nếu lock cũ hơn stale_after_seconds thì coi như process trước đã chết
    (crash/kill) và tự giải phóng, tránh khoá vĩnh viễn.
    """
    with get_conn() as conn:
        row = conn.execute("SELECT pid, started_at FROM run_lock WHERE id = 1").fetchone()
        if row:
            pid, started_at = row
            age = time.time() - datetime.fromisoformat(started_at).timestamp()
            if age < stale_after_seconds:
                raise RunAlreadyInProgress(
                    f"Lần chạy trước (pid={pid}) vẫn đang giữ lock, mới {age:.0f}s trước"
                )
        conn.execute(
            "INSERT INTO run_lock (id, pid, started_at) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET pid=excluded.pid, started_at=excluded.started_at",
            (_pid(), _now_iso()),
        )
    try:
        yield
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM run_lock WHERE id = 1")


def get_run_lock_status():
    """Đang có 1 lần chạy `run.py` giữ `run_lock` (đang trong cửa sổ theo dõi
    liên tục), khác với cron chỉ đang BẬT nhưng đợi tới lần trigger kế tiếp."""
    with get_conn() as conn:
        row = conn.execute("SELECT pid, started_at FROM run_lock WHERE id = 1").fetchone()
    if not row:
        return {"active": False, "pid": None, "started_at": None}
    pid, started_at = row
    return {"active": True, "pid": pid, "started_at": started_at}


def _pid():
    import os

    return os.getpid()


_POSITION_FIELDS = ["status", "symbol", "entry_price", "entry_time", "entry_score", "stop_price", "take_profit_price", "size_usd"]


def get_position_state():
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT {', '.join(_POSITION_FIELDS)} FROM position_state WHERE id = 1"
        ).fetchone()
        if not row:
            return {"status": "WAIT", **{f: None for f in _POSITION_FIELDS if f != "status"}}
        return dict(zip(_POSITION_FIELDS, row))


def set_position_state(status, symbol=None, entry_price=None, entry_time=None, entry_score=None,
                        stop_price=None, take_profit_price=None, size_usd=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO position_state (id, status, symbol, entry_price, entry_time, entry_score, "
            "stop_price, take_profit_price, size_usd) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET status=excluded.status, symbol=excluded.symbol, "
            "entry_price=excluded.entry_price, entry_time=excluded.entry_time, entry_score=excluded.entry_score, "
            "stop_price=excluded.stop_price, take_profit_price=excluded.take_profit_price, "
            "size_usd=excluded.size_usd",
            (status, symbol, entry_price, entry_time, entry_score, stop_price, take_profit_price, size_usd),
        )


def log_signal(symbol, price, decision, total_score, layer_scores: dict, notes=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO signal_log (ts, symbol, price, decision, total_score, layer_scores, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now_iso(), symbol, price, decision, total_score, json.dumps(layer_scores), notes),
        )


def record_run_health(ok: bool):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO run_health (id, last_run_at, last_run_ok) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_run_at=excluded.last_run_at, last_run_ok=excluded.last_run_ok",
            (_now_iso(), int(ok)),
        )


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def add_daily_pnl(pnl_pct: float):
    today = _today()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO daily_pnl (trade_date, realized_pnl_pct) VALUES (?, ?) "
            "ON CONFLICT(trade_date) DO UPDATE SET realized_pnl_pct = realized_pnl_pct + excluded.realized_pnl_pct",
            (today, pnl_pct),
        )
        row = conn.execute(
            "SELECT realized_pnl_pct FROM daily_pnl WHERE trade_date = ?", (today,)
        ).fetchone()
        if row and row[0] <= -abs(config.DAILY_LOSS_LIMIT_PCT):
            conn.execute(
                "UPDATE daily_pnl SET trading_halted = 1 WHERE trade_date = ?", (today,)
            )


def get_kv(key, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default


def set_kv(key, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO kv_store (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def is_trading_halted_today() -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT trading_halted FROM daily_pnl WHERE trade_date = ?", (_today(),)
        ).fetchone()
        return bool(row and row[0])


def get_max_drawdown_pct() -> float:
    """Max Drawdown tính từ lịch sử `daily_pnl` (Risk Engine).

    Cộng dồn PnL% theo ngày, theo dõi đỉnh (peak) chạy được — drawdown là
    khoảng cách lớn nhất từ đỉnh xuống điểm thấp nhất sau đó.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT realized_pnl_pct FROM daily_pnl ORDER BY trade_date"
        ).fetchall()
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for (pnl,) in rows:
        cumulative += pnl
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return round(max_dd, 3)


def is_kill_switch_on() -> bool:
    return get_kv("kill_switch", "0") == "1"


def set_kill_switch(on: bool, reason: str = ""):
    set_kv("kill_switch", "1" if on else "0")
    if on:
        set_kv("kill_switch_reason", reason)


def get_kill_switch_reason() -> str:
    return get_kv("kill_switch_reason", "")


def record_exit_now():
    """Đánh dấu thời điểm SELL gần nhất để tính Cooldown (Risk Engine)."""
    set_kv("last_exit_at", _now_iso())


def cooldown_remaining_seconds(cooldown_minutes: float) -> float:
    last_exit_at = get_kv("last_exit_at")
    if not last_exit_at:
        return 0.0
    elapsed = time.time() - datetime.fromisoformat(last_exit_at).timestamp()
    remaining = cooldown_minutes * 60 - elapsed
    return max(0.0, remaining)


def log_feature_snapshot(symbol, price, features: dict):
    """Feature Store: lưu raw feature mỗi lần chạy."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO feature_snapshot (ts, symbol, price, features) VALUES (?, ?, ?, ?)",
            (_now_iso(), symbol, price, json.dumps(features)),
        )


def get_feature_snapshots(symbol=None, limit=1000):
    """Đọc lại Feature Store để train Entry Model — mới nhất trước."""
    with get_conn() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT ts, symbol, price, features FROM feature_snapshot "
                "WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ts, symbol, price, features FROM feature_snapshot ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [
        {"ts": ts, "symbol": sym, "price": price, "features": json.loads(features)}
        for ts, sym, price, features in rows
    ]


def log_event(event_type: str, payload: dict, trade_id: str | None = None):
    """Raw Event: nguồn sự thật, không overwrite.

    `type` ví dụ: MARKET_TICK, FEATURE_UPDATED, SIGNAL_GENERATED, RISK_REJECTED,
    ENTRY, STOP_MOVED, TAKE_PROFIT, EXIT. payload của ENTRY/EXIT chính là
    Snapshot — không cần bảng snapshot riêng.
    """
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO event_log (ts, trade_id, type, payload) VALUES (?, ?, ?, ?)",
            (_now_iso(), trade_id, event_type, json.dumps(payload)),
        )


def get_events(trade_id: str | None = None, event_type: str | None = None, limit=1000):
    query = "SELECT ts, trade_id, type, payload FROM event_log WHERE 1=1"
    params = []
    if trade_id:
        query += " AND trade_id = ?"
        params.append(trade_id)
    if event_type:
        query += " AND type = ?"
        params.append(event_type)
    query += " ORDER BY id ASC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {"ts": ts, "trade_id": tid, "type": t, "payload": json.loads(payload)}
        for ts, tid, t, payload in rows
    ]


def get_ws_cvd(symbol: str, max_age_seconds: float = 120.0):
    """CVD tính từ trade stream WS thật (`collector_ws.py`, flush mỗi 30s) — ưu
    tiên dùng thay CVD xấp xỉ từ REST snapshot khi có sẵn và đủ mới. None nếu
    collector_ws chưa chạy/dữ liệu quá cũ, caller tự fallback về REST.
    """
    cvd = get_kv(f"cvd_ws_{symbol}")
    ts = get_kv(f"cvd_ws_{symbol}_updated_at")
    if cvd is None or ts is None:
        return None
    age = time.time() - datetime.fromisoformat(ts).timestamp()
    if age > max_age_seconds:
        return None
    return float(cvd)


def get_last_tick(symbol: str, max_age_seconds: float = 30.0):
    """Tick giá thật gần nhất từ collector_ws — None nếu quá cũ/không có
    (collector_ws chưa chạy hoặc mất kết nối), caller tự fallback về giá REST.
    """
    price = get_kv(f"last_tick_price_{symbol}")
    ts = get_kv(f"last_tick_price_{symbol}_at")
    if price is None or ts is None:
        return None
    age = time.time() - datetime.fromisoformat(ts).timestamp()
    if age > max_age_seconds:
        return None
    return float(price)


def make_trade_id(symbol: str, entry_time: str) -> str:
    return f"{symbol}_{entry_time}"


def get_trade_summary(trade_id: str) -> dict | None:
    """Trade Summary: ghép cặp ENTRY/EXIT event của cùng trade_id."""
    events = get_events(trade_id=trade_id)
    entry = next((e for e in events if e["type"] == "ENTRY"), None)
    exit_ = next((e for e in events if e["type"] == "EXIT"), None)
    if not entry:
        return None
    summary = {
        "trade_id": trade_id,
        "entry_time": entry["ts"],
        "entry_price": entry["payload"].get("market", {}).get("price"),
        "model": entry["payload"].get("model"),
        "status": "OPEN",
    }
    if exit_:
        summary.update(
            {
                "exit_time": exit_["ts"],
                "exit_price": exit_["payload"].get("market", {}).get("price"),
                "pnl_pct": exit_["payload"].get("pnl_pct"),
                "exit_reason": exit_["payload"].get("reason"),
                "status": "CLOSED",
            }
        )
    return summary
