# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import datetime, date, timedelta
import logging, os, re, csv

from airflow import DAG
from airflow.operators.python import PythonOperator, get_current_context
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.utils.dates import days_ago

# ===== Config =====
DAG_ID = "load_songkhla_csv_to_pg"
PG_CONN_ID = "postgres_default"
TARGET_TABLE = "songkhla_weather_half_hourly_prev"

# ดีฟอลต์ path ของไฟล์ (สามารถ override ตอน Trigger)
DEFAULT_CSV_PATH = "/opt/airflow/dags/songkhla_weather_2024.csv"

# เวลาไทย (สำหรับ parse ถ้าเป็นคู่ date+time)
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Bangkok")
except Exception:
    from pytz import timezone as tz
    TZ = tz("Asia/Bangkok")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===== Utils =====
def _to_float(s: str):
    if s is None:
        return None
    s = str(s).strip()
    if s == "" or s.lower() in {"na", "null", "none"}:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s.replace(",", ""))
    return float(m.group(0)) if m else None

def _create_table_if_not_exists(hook: PostgresHook):
    hook.run(f"""
        CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (
            datetime TIMESTAMP PRIMARY KEY,
            temperatureF NUMERIC,
            humidity_pct NUMERIC,
            wind_speed_kmh NUMERIC,
            pressure_in NUMERIC,
            condition TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    # VIEW (ถ้ายังไม่มีหรืออยากอัปเดตหัวตาราง)
    hook.run(f"""
        CREATE OR REPLACE VIEW vw_{TARGET_TABLE}_csv AS
        SELECT
          to_char(datetime, 'DD/MM/YYYY') AS "date",
          to_char(datetime, 'HH12:MI AM')  AS "time",
          temperatureF                     AS "temperature_F",
          humidity_pct                     AS "humidity_%",
          wind_speed_kmh,
          pressure_in,
          condition
        FROM {TARGET_TABLE}
        ORDER BY datetime;
    """)

def _upsert_batch(hook: PostgresHook, rows: list[tuple]) -> int:
    """UPSERT ตามคีย์ datetime (กันซ้ำ)"""
    if not rows:
        return 0
    sql = f"""
        INSERT INTO {TARGET_TABLE}
        (datetime, temperatureF, humidity_pct, wind_speed_kmh, pressure_in, condition)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (datetime) DO UPDATE SET
          temperatureF   = COALESCE(EXCLUDED.temperatureF,   {TARGET_TABLE}.temperatureF),
          humidity_pct   = COALESCE(EXCLUDED.humidity_pct,   {TARGET_TABLE}.humidity_pct),
          wind_speed_kmh = COALESCE(EXCLUDED.wind_speed_kmh, {TARGET_TABLE}.wind_speed_kmh),
          pressure_in    = COALESCE(EXCLUDED.pressure_in,    {TARGET_TABLE}.pressure_in),
          condition      = COALESCE(EXCLUDED.condition,      {TARGET_TABLE}.condition);
    """
    conn = hook.get_conn()
    with conn:
        with conn.cursor() as cur:
            # แบ่ง batch กัน memory/lock
            for i in range(0, len(rows), 1000):
                cur.executemany(sql, rows[i:i+1000])
    return len(rows)

def _parse_datetime(row: dict) -> datetime | None:
    """
    รองรับสองกรณี:
    1) มีคอลัมน์ 'datetime' โดยตรง (ISO, 'YYYY-MM-DD HH:MM:SS' ฯลฯ)
    2) มี 'date' (DD/MM/YYYY หรือ YYYY-MM-DD) + 'time' ('HH:MM AM/PM')
    """
    # กรณี 1: มี datetime
    for key in ("datetime", "date_time", "ts"):
        if key in row and str(row[key]).strip():
            s = str(row[key]).strip()
            # ลอง parse แบบทั่วไป ๆ
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
                        "%d/%m/%Y %I:%M %p", "%d/%m/%Y %H:%M",
                        "%m/%d/%Y %I:%M %p"):
                try:
                    return datetime.strptime(s, fmt)
                except Exception:
                    pass
            # สุดท้ายลอง pandas แบบไม่พึ่ง lib — ข้าม ถ้าแปลไม่ได้
            try:
                # fallback หยาบ ๆ: แยกวัน/เวลา ถ้ามี 'T'
                if "T" in s:
                    s = s.replace("T", " ").split("+")[0]
                    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None

    # กรณี 2: date + time
    date_keys = ("date",)
    time_keys = ("time",)
    dtxt, ttxt = None, None

    for k in date_keys:
        if k in row and str(row[k]).strip():
            dtxt = str(row[k]).strip()
            break
    for k in time_keys:
        if k in row and str(row[k]).strip():
            ttxt = str(row[k]).strip()
            break

    if not dtxt or not ttxt:
        return None

    # parse date
    dd = None
    for dfmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            dd = datetime.strptime(dtxt, dfmt).date()
            break
        except Exception:
            continue
    if dd is None:
        return None

    # parse time (ส่วนมากจาก view จะเป็น 'HH:MM AM/PM')
    tt = None
    for tfmt in ("%I:%M %p", "%H:%M"):
        try:
            tt = datetime.strptime(ttxt, tfmt).time()
            break
        except Exception:
            continue
    if tt is None:
        return None

    return datetime.combine(dd, tt)

def _read_csv_to_rows(csv_path: str) -> tuple[list[tuple], int]:
    """
    อ่าน CSV แล้ว map คอลัมน์เป็น schema เป้าหมาย
    return: (rows, total_input_rows)
    """
    rows = []
    total = 0
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        # ทำ header ให้เป็น lower ทั้งหมดสำหรับการหา key
        fieldnames = [h.strip() for h in reader.fieldnames] if reader.fieldnames else []
        lower_map = {h.lower(): h for h in fieldnames}

        def get(row: dict, *names):
            # หา key แบบ case-insensitive/alias
            for n in names:
                ln = n.lower()
                if ln in lower_map:
                    return row.get(lower_map[ln])
            return None

        for row in reader:
            total += 1
            dt = _parse_datetime({k.lower(): v for k, v in row.items()})
            if not dt:
                continue

            temp_f = _to_float(get(row, "temperatureF", "temperature_F", "temp_f"))
            hum    = _to_float(get(row, "humidity_pct", "humidity_%", "humidity"))
            wind   = _to_float(get(row, "wind_speed_kmh", "wind_kmh", "wind"))
            press  = _to_float(get(row, "pressure_in", "pressure"))
            cond   = get(row, "condition", "conditions", "weather", "description")
            cond   = cond.strip() if isinstance(cond, str) and cond.strip() != "" else None

            rows.append((dt, temp_f, hum, wind, press, cond))
    return rows, total

def load_csv_into_postgres():
    ctx = get_current_context()
    conf = (ctx.get("dag_run").conf or {}) if ctx.get("dag_run") else {}

    csv_path = conf.get("file_path", DEFAULT_CSV_PATH)
    truncate_year = conf.get("truncate_year")       # e.g., 2024 (int) หรือ string "2024"

    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV not found at: {csv_path}")

    hook = PostgresHook(PG_CONN_ID)
    _create_table_if_not_exists(hook)

    # ถ้าระบุ truncate_year → ลบข้อมูลปีนั้นทิ้งก่อน (ออปชัน)
    if truncate_year:
        y = int(truncate_year)
        logger.info(f"[{DAG_ID}] Truncate year={y} in table {TARGET_TABLE}")
        hook.run(f"DELETE FROM {TARGET_TABLE} WHERE EXTRACT(YEAR FROM datetime) = %s", parameters=(y,))

    # อ่านไฟล์
    rows, total = _read_csv_to_rows(csv_path)
    logger.info(f"[{DAG_ID}] Read {total} rows from CSV, valid={len(rows)}")

    # อัปเซิร์ต
    inserted = _upsert_batch(hook, rows)
    logger.info(f"[{DAG_ID}] Upserted rows: {inserted}")

# ===== Airflow DAG =====
default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 0,
}

with DAG(
    dag_id=DAG_ID,
    description=f"Load Songkhla weather CSV into Postgres (table={TARGET_TABLE})",
    start_date=days_ago(1),
    schedule=None,          # Trigger เอง
    catchup=False,
    max_active_runs=1,
    concurrency=1,
    default_args=default_args,
    tags=["weather","songkhla","csv","import","postgres"],
) as dag:
    PythonOperator(
        task_id="load_csv_into_postgres",
        python_callable=load_csv_into_postgres,
    )
