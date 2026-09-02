"""SQLite state store: state machine, run lock, và signal log.

Mỗi activation là một process mới nên toàn bộ state phải nằm ngoài process —
không giữ gì trong memory giữa các lần chạy.
"""
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from . import config

_POSITION_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS position_state (
    trade_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'IN_POSITION',
    symbol TEXT,
    entry_price REAL,
    entry_time TEXT,
    entry_score REAL,
    stop_price REAL,
    take_profit_price REAL,
    size_usd REAL,
    tp_reason TEXT,
    scoring_profile TEXT NOT NULL DEFAULT 'champion',
    position_meta TEXT NOT NULL DEFAULT '{}'
);
"""

_SCHEMA = _POSITION_STATE_SCHEMA + """
CREATE TABLE IF NOT EXISTS run_lock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    pid INTEGER,
    started_at TEXT,
    owner_token TEXT,
    heartbeat_at TEXT
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    trade_date TEXT PRIMARY KEY,
    realized_pnl_pct REAL NOT NULL DEFAULT 0,
    realized_pnl_usd REAL NOT NULL DEFAULT 0,
    start_equity_usd REAL,
    end_equity_usd REAL,
    trading_halted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS equity_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    trade_id TEXT NOT NULL UNIQUE,
    realized_pnl_usd REAL NOT NULL,
    equity_before_usd REAL NOT NULL,
    equity_after_usd REAL NOT NULL,
    return_on_equity_pct REAL NOT NULL,
    accounting TEXT NOT NULL
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
    features TEXT NOT NULL,
    lineage TEXT NOT NULL DEFAULT '{}'
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

CREATE INDEX IF NOT EXISTS idx_event_log_type_ts ON event_log(type, ts);
"""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


_BOUND_CONNECTION = ContextVar("state_store_bound_connection", default=None)


def _prepare_connection(conn):
    conn.executescript(_SCHEMA)
    _migrate_position_state_to_multi(conn)
    _migrate_position_state_metadata(conn)
    _migrate_feature_snapshot_lineage(conn)
    _migrate_daily_pnl_ledger(conn)
    _migrate_run_lock_owner(conn)


def _migrate_position_state_to_multi(conn):
    """DB tạo trước khi hỗ trợ đa vị thế có `position_state` với PK `id=1` (1
    row cố định, đóng lệnh = ghi đè status='WAIT'). Đổi PK sang `trade_id` để
    cho phép nhiều row `status='IN_POSITION'` cùng lúc (MAX_CONCURRENT_POSITIONS
    > 1) — SQLite không ALTER được PK trực tiếp nên phải tạo lại bảng, giữ
    nguyên vị thế đang mở (nếu có) sang schema mới. Idempotent: bỏ qua nếu đã
    ở schema mới."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(position_state)").fetchall()}
    if "trade_id" in cols:
        return
    try:
        conn.execute("ALTER TABLE position_state ADD COLUMN entry_score REAL")
    except sqlite3.OperationalError:
        pass  # cột đã tồn tại (DB tạo trước khi thêm entry_score)
    old_row = conn.execute(
        "SELECT status, symbol, entry_price, entry_time, entry_score, stop_price, "
        "take_profit_price, size_usd FROM position_state WHERE id = 1"
    ).fetchone()
    conn.execute("ALTER TABLE position_state RENAME TO position_state_legacy_single")
    conn.execute(_POSITION_STATE_SCHEMA)
    if old_row and old_row[0] == "IN_POSITION":
        status, symbol, entry_price, entry_time, entry_score, stop_price, take_profit_price, size_usd = old_row
        conn.execute(
            "INSERT INTO position_state (trade_id, status, symbol, entry_price, entry_time, "
            "entry_score, stop_price, take_profit_price, size_usd) VALUES (?, 'IN_POSITION', ?, ?, ?, ?, ?, ?, ?)",
            (make_trade_id(symbol, entry_time), symbol, entry_price, entry_time, entry_score,
             stop_price, take_profit_price, size_usd),
        )
    conn.execute("DROP TABLE position_state_legacy_single")


def _migrate_position_state_metadata(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(position_state)").fetchall()}
    additions = {
        "tp_reason": "TEXT",
        "scoring_profile": "TEXT NOT NULL DEFAULT 'champion'",
        "position_meta": "TEXT NOT NULL DEFAULT '{}'",
    }
    for name, ddl in additions.items():
        if name not in cols:
            try:
                conn.execute(f"ALTER TABLE position_state ADD COLUMN {name} {ddl}")
            except sqlite3.OperationalError:
                refreshed = {row[1] for row in conn.execute("PRAGMA table_info(position_state)").fetchall()}
                if name not in refreshed:
                    raise


def _migrate_feature_snapshot_lineage(conn):
    """Bổ sung lineage cho DB cũ mà không làm mất feature snapshot hiện có."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(feature_snapshot)").fetchall()}
    if "lineage" not in cols:
        try:
            conn.execute("ALTER TABLE feature_snapshot ADD COLUMN lineage TEXT NOT NULL DEFAULT '{}'")
        except sqlite3.OperationalError as exc:
            # Hai process có thể cùng migrate DB lần đầu; chỉ bỏ qua nếu process
            # kia vừa thêm đúng cột, còn lỗi khác phải được nổi lên.
            refreshed = {row[1] for row in conn.execute("PRAGMA table_info(feature_snapshot)").fetchall()}
            if "lineage" not in refreshed:
                raise exc


def _migrate_daily_pnl_ledger(conn):
    """Bổ sung cột USD/equity cho DB cũ; dữ liệu pct cũ không suy ngược thành
    USD nên giữ nguyên làm legacy và ledger mới bắt đầu từ equity cấu hình."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(daily_pnl)").fetchall()}
    additions = {
        "realized_pnl_usd": "REAL NOT NULL DEFAULT 0",
        "start_equity_usd": "REAL",
        "end_equity_usd": "REAL",
    }
    for name, ddl in additions.items():
        if name not in cols:
            try:
                conn.execute(f"ALTER TABLE daily_pnl ADD COLUMN {name} {ddl}")
            except sqlite3.OperationalError:
                refreshed = {row[1] for row in conn.execute("PRAGMA table_info(daily_pnl)").fetchall()}
                if name not in refreshed:
                    raise


def _migrate_run_lock_owner(conn):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(run_lock)").fetchall()}
    for name in ("owner_token", "heartbeat_at"):
        if name not in cols:
            try:
                conn.execute(f"ALTER TABLE run_lock ADD COLUMN {name} TEXT")
            except sqlite3.OperationalError:
                refreshed = {row[1] for row in conn.execute("PRAGMA table_info(run_lock)").fetchall()}
                if name not in refreshed:
                    raise


@contextmanager
def get_conn():
    bound = _BOUND_CONNECTION.get()
    if bound is not None:
        yield bound
        return
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), timeout=10)
    try:
        _prepare_connection(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def session():
    """Dùng một SQLite connection cho chuỗi lifecycle lớn.

    Các hàm public vẫn đi qua ``get_conn`` và cùng schema/SQL như live; replay
    tăng tốc chỉ thay việc mở lại connection ở mỗi event. Live không bind
    session nên giữ nguyên transaction-per-call hiện hành.
    """
    if _BOUND_CONNECTION.get() is not None:
        raise RuntimeError("state_store.session không hỗ trợ lồng nhau")
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30)
    token = None
    try:
        _prepare_connection(conn)
        token = _BOUND_CONNECTION.set(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if token is not None:
            _BOUND_CONNECTION.reset(token)
        conn.close()


class RunAlreadyInProgress(Exception):
    pass


@contextmanager
def run_lock(stale_after_seconds=180):
    """Atomic lease lock; owner heartbeat quyết định stale, release đúng owner."""
    # Khởi tạo/migrate schema trước transaction acquire ngắn.
    with get_conn():
        pass
    owner_token = uuid.uuid4().hex
    conn = sqlite3.connect(str(config.DB_PATH), timeout=10, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT pid, started_at, heartbeat_at FROM run_lock WHERE id = 1"
        ).fetchone()
        if row:
            pid, started_at, heartbeat_at = row
            lease_at = heartbeat_at or started_at
            age = time.time() - datetime.fromisoformat(lease_at).timestamp()
            if age < stale_after_seconds and _pid_alive(pid):
                conn.execute("ROLLBACK")
                raise RunAlreadyInProgress(
                    f"Lần chạy trước (pid={pid}) vẫn giữ lease, heartbeat {age:.0f}s trước"
                )
            conn.execute("DELETE FROM run_lock WHERE id = 1")
        now = _now_iso()
        conn.execute(
            "INSERT INTO run_lock (id, pid, started_at, owner_token, heartbeat_at) "
            "VALUES (1, ?, ?, ?, ?)",
            (_pid(), now, owner_token, now),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    try:
        yield owner_token
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM run_lock WHERE id = 1 AND owner_token = ?", (owner_token,))


def refresh_run_lock(owner_token: str) -> bool:
    """Gia hạn lease; False nghĩa là process không còn sở hữu lock."""
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE run_lock SET heartbeat_at = ? WHERE id = 1 AND owner_token = ?",
            (_now_iso(), owner_token),
        )
        return cursor.rowcount == 1


def get_run_lock_status():
    """Đang có 1 lần chạy `run.py` giữ `run_lock` (đang trong cửa sổ theo dõi
    liên tục), khác với scheduler đang đợi lần trigger kế tiếp."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT pid, started_at, heartbeat_at, owner_token FROM run_lock WHERE id = 1"
        ).fetchone()
    if not row:
        return {"active": False, "pid": None, "started_at": None, "heartbeat_at": None}
    pid, started_at, heartbeat_at, owner_token = row
    return {
        "active": True, "pid": pid, "started_at": started_at,
        "heartbeat_at": heartbeat_at, "owner_token": owner_token,
    }


def _pid():
    return os.getpid()


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True


_POSITION_FIELDS = ["trade_id", "status", "symbol", "entry_price", "entry_time", "entry_score",
                    "stop_price", "take_profit_price", "size_usd", "tp_reason",
                    "scoring_profile", "position_meta"]


def get_open_positions() -> list:
    """Toàn bộ vị thế đang mở — có thể nhiều hơn 1 nếu `MAX_CONCURRENT_POSITIONS`
    > 1 (nhiều lệnh cùng symbol mở lệch thời điểm). Rỗng nghĩa là không có lệnh
    nào đang mở (tương đương "WAIT" ở schema 1-vị-thế cũ)."""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(_POSITION_FIELDS)} FROM position_state "
            "WHERE status = 'IN_POSITION' ORDER BY entry_time"
        ).fetchall()
    result = []
    for row in rows:
        item = dict(zip(_POSITION_FIELDS, row))
        item["position_meta"] = json.loads(item.get("position_meta") or "{}")
        result.append(item)
    return result


def open_position(symbol, entry_price, entry_time, entry_score, stop_price, take_profit_price,
                  size_usd, tp_reason=None, scoring_profile="champion",
                  position_meta: dict | None = None) -> str:
    """Mở 1 vị thế mới, trả về `trade_id` (khoá cho `close_position`/event_log)."""
    trade_id = make_trade_id(symbol, entry_time)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO position_state (trade_id, status, symbol, entry_price, entry_time, "
            "entry_score, stop_price, take_profit_price, size_usd, tp_reason, scoring_profile, position_meta) "
            "VALUES (?, 'IN_POSITION', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (trade_id, symbol, entry_price, entry_time, entry_score, stop_price,
             take_profit_price, size_usd, tp_reason, scoring_profile,
             json.dumps(position_meta or {})),
        )
    return trade_id


def close_position(trade_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM position_state WHERE trade_id = ?", (trade_id,))


def resize_position(trade_id: str, size_usd: float, position_meta: dict | None = None):
    """Cập nhật target notional của một vị thế đang mở, giữ nguyên trade ID."""
    with get_conn() as conn:
        if position_meta is None:
            cursor = conn.execute(
                "UPDATE position_state SET size_usd = ? WHERE trade_id = ? AND status = 'IN_POSITION'",
                (float(size_usd), trade_id),
            )
        else:
            cursor = conn.execute(
                "UPDATE position_state SET size_usd = ?, position_meta = ? "
                "WHERE trade_id = ? AND status = 'IN_POSITION'",
                (float(size_usd), json.dumps(position_meta), trade_id),
            )
        if cursor.rowcount != 1:
            raise KeyError(f"không tìm thấy vị thế đang mở: {trade_id}")


def log_signal(symbol, price, decision, total_score, layer_scores: dict, notes="", ts: str | None = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO signal_log (ts, symbol, price, decision, total_score, layer_scores, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts or _now_iso(), symbol, price, decision, total_score, json.dumps(layer_scores), notes),
        )


def record_run_health(ok: bool):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO run_health (id, last_run_at, last_run_ok) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_run_at=excluded.last_run_at, last_run_ok=excluded.last_run_ok",
            (_now_iso(), int(ok)),
        )


def get_run_health():
    """Lần chạy `run.py` gần nhất (ghi bởi `record_run_health`) — dùng cho
    health-check độc lập (`scripts/health_check.py`) phát hiện scheduler/máy đã
    dừng. None nếu chưa có lần chạy nào."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT last_run_at, last_run_ok FROM run_health WHERE id = 1"
        ).fetchone()
    if not row:
        return None
    return {"last_run_at": row[0], "last_run_ok": bool(row[1])}


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_current_equity_usd() -> float:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT equity_after_usd FROM equity_ledger ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return float(row[0]) if row else float(config.ACCOUNT_EQUITY_USD)


def record_trade_accounting(trade_id: str, accounting: dict, ts: str | None = None) -> dict:
    """Ghi realized PnL/equity atomic và idempotent theo trade_id."""
    ts = ts or _now_iso()
    pnl_usd = float(accounting["net_pnl_usd"])
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT realized_pnl_usd, equity_before_usd, equity_after_usd, "
            "return_on_equity_pct, accounting FROM equity_ledger WHERE trade_id = ?",
            (trade_id,),
        ).fetchone()
        if existing:
            return {
                "realized_pnl_usd": existing[0], "equity_before_usd": existing[1],
                "equity_after_usd": existing[2], "return_on_equity_pct": existing[3],
                "accounting": json.loads(existing[4]), "duplicate": True,
            }

        row = conn.execute(
            "SELECT equity_after_usd FROM equity_ledger ORDER BY id DESC LIMIT 1"
        ).fetchone()
        equity_before = float(row[0]) if row else float(config.ACCOUNT_EQUITY_USD)
        equity_after = equity_before + pnl_usd
        roe_pct = pnl_usd / equity_before * 100 if equity_before else 0.0
        full_accounting = {
            **accounting,
            "equity_before_usd": round(equity_before, 8),
            "equity_after_usd": round(equity_after, 8),
            "return_on_equity_pct": round(roe_pct, 6),
        }
        conn.execute(
            "INSERT INTO equity_ledger (ts, trade_id, realized_pnl_usd, equity_before_usd, "
            "equity_after_usd, return_on_equity_pct, accounting) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, trade_id, pnl_usd, equity_before, equity_after, roe_pct,
             json.dumps(full_accounting)),
        )

        trade_date = datetime.fromisoformat(ts).astimezone(timezone.utc).strftime("%Y-%m-%d")
        daily = conn.execute(
            "SELECT realized_pnl_usd, start_equity_usd FROM daily_pnl WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
        if daily:
            daily_usd = float(daily[0]) + pnl_usd
            start_equity = float(daily[1]) if daily[1] is not None else equity_before
            daily_pct = daily_usd / start_equity * 100 if start_equity else 0.0
            halted = int(daily_pct <= -abs(config.DAILY_LOSS_LIMIT_PCT))
            conn.execute(
                "UPDATE daily_pnl SET realized_pnl_usd=?, realized_pnl_pct=?, "
                "start_equity_usd=?, end_equity_usd=?, trading_halted=MAX(trading_halted, ?) "
                "WHERE trade_date=?",
                (daily_usd, daily_pct, start_equity, equity_after, halted, trade_date),
            )
        else:
            daily_pct = pnl_usd / equity_before * 100 if equity_before else 0.0
            halted = int(daily_pct <= -abs(config.DAILY_LOSS_LIMIT_PCT))
            conn.execute(
                "INSERT INTO daily_pnl (trade_date, realized_pnl_pct, realized_pnl_usd, "
                "start_equity_usd, end_equity_usd, trading_halted) VALUES (?, ?, ?, ?, ?, ?)",
                (trade_date, daily_pct, pnl_usd, equity_before, equity_after, halted),
            )
    return {
        "realized_pnl_usd": round(pnl_usd, 8),
        "equity_before_usd": round(equity_before, 8),
        "equity_after_usd": round(equity_after, 8),
        "return_on_equity_pct": round(roe_pct, 6),
        "accounting": full_accounting,
        "duplicate": False,
    }


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


def is_trading_halted_at(ts: str) -> bool:
    """Daily-loss gate tại clock được truyền vào, dùng cho accelerated replay."""
    trade_date = datetime.fromisoformat(ts).astimezone(timezone.utc).strftime("%Y-%m-%d")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT trading_halted FROM daily_pnl WHERE trade_date = ?", (trade_date,)
        ).fetchone()
        return bool(row and row[0])


def get_max_drawdown_pct() -> float:
    """Max Drawdown từ equity ledger thật; fallback legacy daily_pnl nếu rỗng."""
    with get_conn() as conn:
        ledger = conn.execute(
            "SELECT equity_after_usd FROM equity_ledger ORDER BY id"
        ).fetchall()
        rows = conn.execute("SELECT realized_pnl_pct FROM daily_pnl ORDER BY trade_date").fetchall()
    if ledger:
        values = [float(config.ACCOUNT_EQUITY_USD)] + [float(row[0]) for row in ledger]
        peak = values[0]
        max_dd = 0.0
        for equity in values:
            peak = max(peak, equity)
            if peak > 0:
                max_dd = max(max_dd, (peak - equity) / peak * 100)
        return round(max_dd, 3)
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


def arm_kill_switch_if_drawdown_breached() -> bool:
    """Bật Kill Switch khi Max Drawdown vượt ngưỡng; không bao giờ tự tắt lại.

    README khai luật này là bất đối xứng: hệ tự bật, chỉ người tắt được
    (`scripts/kill_switch.py off`). Luật nằm ở đây thay vì inline trong vòng
    chạy để chính nó gọi được từ test — vòng chạy cần network và dữ liệu thật,
    nên khi luật sống trong đó thì không phép đo nào chạm được vào nó.

    Trả về True nếu lần gọi này vừa bật.
    """
    max_dd = get_max_drawdown_pct()
    if max_dd >= config.MAX_DRAWDOWN_PCT and not is_kill_switch_on():
        set_kill_switch(True, reason=f"Max drawdown {max_dd}% >= ngưỡng {config.MAX_DRAWDOWN_PCT}%")
        return True
    return False


def record_exit_now(ts: str | None = None):
    """Đánh dấu thời điểm SELL gần nhất để tính Cooldown (Risk Engine)."""
    set_kv("last_exit_at", ts or _now_iso())


def cooldown_remaining_seconds(cooldown_minutes: float) -> float:
    last_exit_at = get_kv("last_exit_at")
    if not last_exit_at:
        return 0.0
    elapsed = time.time() - datetime.fromisoformat(last_exit_at).timestamp()
    remaining = cooldown_minutes * 60 - elapsed
    return max(0.0, remaining)


def log_feature_snapshot(symbol, price, features: dict, lineage: dict | None = None,
                         ts: str | None = None):
    """Feature Store: lưu raw feature cùng nguồn/phiên bản/strategy sử dụng."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO feature_snapshot (ts, symbol, price, features, lineage) VALUES (?, ?, ?, ?, ?)",
            (ts or _now_iso(), symbol, price, json.dumps(features), json.dumps(lineage or {})),
        )


def get_feature_snapshots(symbol=None, limit=1000):
    """Đọc lại Feature Store để train Entry Model — mới nhất trước."""
    with get_conn() as conn:
        if symbol:
            rows = conn.execute(
                "SELECT ts, symbol, price, features, lineage FROM feature_snapshot "
                "WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ts, symbol, price, features, lineage FROM feature_snapshot ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [
        {"ts": ts, "symbol": sym, "price": price, "features": json.loads(features),
         "lineage": json.loads(lineage or "{}")}
        for ts, sym, price, features, lineage in rows
    ]


def log_event(event_type: str, payload: dict, trade_id: str | None = None,
              ts: str | None = None):
    """Raw Event: nguồn sự thật, không overwrite.

    `type` ví dụ: MARKET_TICK, FEATURE_UPDATED, SIGNAL_GENERATED, RISK_REJECTED,
    ENTRY, STOP_MOVED, TAKE_PROFIT, EXIT. payload của ENTRY/EXIT chính là
    Snapshot — không cần bảng snapshot riêng.
    """
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO event_log (ts, trade_id, type, payload) VALUES (?, ?, ?, ?)",
            (ts or _now_iso(), trade_id, event_type, json.dumps(payload)),
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


def get_recent_events(event_type: str, limit: int = 100) -> list:
    """Event mới nhất trước; không bị kẹt ở 10k event đầu như get_events ASC."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, trade_id, type, payload FROM event_log WHERE type = ? "
            "ORDER BY id DESC LIMIT ?",
            (event_type, limit),
        ).fetchall()
    return [
        {"ts": ts, "trade_id": tid, "type": kind, "payload": json.loads(payload)}
        for ts, tid, kind, payload in rows
    ]


def get_events_in_range(event_type: str, start_ts: str, end_ts: str) -> list:
    """Event theo khoảng UTC, cũ đến mới; dùng cho timeline/dashboard.

    Index ``(type, ts)`` giữ truy vấn ổn định khi MARKET_TICK tăng theo thời
    gian. Caller chịu trách nhiệm giới hạn range và giảm mẫu trước khi trả UI.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, trade_id, type, payload FROM event_log "
            "WHERE type = ? AND ts >= ? AND ts <= ? ORDER BY ts ASC",
            (event_type, start_ts, end_ts),
        ).fetchall()
    return [
        {"ts": ts, "trade_id": tid, "type": kind, "payload": json.loads(payload)}
        for ts, tid, kind, payload in rows
    ]


def get_latest_event(event_type: str) -> dict | None:
    rows = get_recent_events(event_type, limit=1)
    return rows[0] if rows else None


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
    snapshot = get_last_tick_snapshot(symbol, max_age_seconds=max_age_seconds)
    return snapshot["price"] if snapshot else None


def get_last_tick_snapshot(symbol: str, max_age_seconds: float = 30.0):
    price = get_kv(f"last_tick_price_{symbol}")
    ts = get_kv(f"last_tick_price_{symbol}_at")
    if price is None or ts is None:
        return None
    age = max(0.0, time.time() - datetime.fromisoformat(ts).timestamp())
    if age > max_age_seconds:
        return None
    return {"price": float(price), "timestamp": ts, "age_seconds": age, "source": "collector_ws"}


def get_cached_report(cache_key: str, max_age_seconds: float):
    """AI Report/AI Review đã sinh gần đây cho cùng `cache_key` (thường là
    `decision:trade_id`). Đây là best-effort cache, chưa phải distributed lock;
    xem `TODO-AI-CACHE-ATOMIC`. None nếu chưa có/đã quá cũ.
    """
    text = get_kv(f"ai_report_cache_{cache_key}")
    ts = get_kv(f"ai_report_cache_{cache_key}_at")
    if text is None or ts is None:
        return None
    age = time.time() - datetime.fromisoformat(ts).timestamp()
    if age > max_age_seconds:
        return None
    return text


def set_cached_report(cache_key: str, text: str):
    set_kv(f"ai_report_cache_{cache_key}", text)
    set_kv(f"ai_report_cache_{cache_key}_at", _now_iso())


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
                "pnl_usd": exit_["payload"].get("pnl_usd"),
                "exit_reason": exit_["payload"].get("reason"),
                "status": "CLOSED",
            }
        )
    return summary
