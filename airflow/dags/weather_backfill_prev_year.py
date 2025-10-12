# -*- coding: utf-8 -*-
from __future__ import annotations
from datetime import datetime, date, timedelta
import logging, time, re, os
from random import uniform

from airflow import DAG
from airflow.operators.python import PythonOperator, get_current_context
from airflow.providers.postgres.hooks.postgres import PostgresHook

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException

# ===== Config =====
DAG_ID = "songkhla_weather_backfill_prev_year_split"
PG_CONN_ID = "postgres_default"
TARGET_TABLE = "songkhla_weather_half_hourly_prev"

# เวลาไทย
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Asia/Bangkok")
except Exception:
    from pytz import timezone as tz
    TZ = tz("Asia/Bangkok")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALLOWED_STATIONS = {"VTSS", "VTSH"}  # VTSS = Hat Yai Intl, VTSH = Songkhla

# ===== Utils =====
def _now_th() -> datetime:
    return datetime.now(TZ)

def _target_year() -> int:
    """ปกติ = ปีก่อนหน้า; override ได้ตอน Trigger: {"year": 2024}"""
    ctx = get_current_context()
    conf = (ctx.get("dag_run").conf or {}) if ctx.get("dag_run") else {}
    if "year" in conf:
        return int(conf["year"])
    return _now_th().year - 1

def _station() -> str:
    """เลือกสถานีจาก dag_run.conf.station > env[WU_STATION] > 'VTSS'"""
    ctx = get_current_context()
    conf = (ctx.get("dag_run").conf or {}) if ctx.get("dag_run") else {}
    s = (conf.get("station") or os.getenv("WU_STATION", "VTSS")).strip().upper()
    return s if s in ALLOWED_STATIONS else "VTSS"

def _to_float(s: str):
    if not s:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s.replace(",", ""))
    return float(m.group(0)) if m else None

def _open_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1200,1600")
    opts.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")
    opts.page_load_strategy = "eager"
    opts.add_experimental_option("prefs", {
        "profile.managed_default_content_settings.images": 2,
        "intl.accept_languages": "en-US,en"
    })
    opts.binary_location = os.getenv("CHROME_BIN", "/usr/bin/chromium")
    service = Service(os.getenv("CHROMEDRIVER_PATH", "/usr/bin/chromedriver"))
    drv = webdriver.Chrome(service=service, options=opts)
    drv.set_page_load_timeout(60)
    drv.set_script_timeout(30)
    return drv

def _dismiss_consent_if_any(drv):
    try:
        WebDriverWait(drv, 5).until(lambda d: True)
        btns = drv.find_elements(
            By.XPATH,
            "//button//*[contains(translate(., 'ACEPTยอมรับAGREE', 'aceptยอมรับagree'),'accept') "
            "or contains(translate(., 'ACEPTยอมรับAGREE', 'aceptยอมรับagree'),'agree') "
            "or contains(., 'ยอมรับ')]/ancestor::button[1]"
        )
        if not btns:
            btns = drv.find_elements(By.XPATH, "//button[contains(.,'Got it') or contains(.,'Close')]")
        for b in btns:
            try:
                b.click(); break
            except Exception:
                pass
    except Exception:
        pass

def _create_table_if_not_exists():
    hook = PostgresHook(PG_CONN_ID)
    # ตารางหลัก (ให้ตรงกับของเดิม: ไม่มี updated_at)
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
    # VIEW สำหรับ export
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

def _date_range(d0: date, d1: date) -> list[date]:
    days = []
    d = d0
    while d <= d1:
        days.append(d)
        d += timedelta(days=1)
    return days

def _next_day_in_year(hook: PostgresHook, y: int) -> date:
    sql = f"""
      SELECT COALESCE(MAX(datetime::date) + 1, DATE %s) AS next_day
      FROM {TARGET_TABLE}
      WHERE EXTRACT(YEAR FROM datetime) = %s
    """
    start_default = date(y, 1, 1)
    row = hook.get_first(sql, (start_default.isoformat(), y))
    return (row[0] if row and row[0] else start_default)

def _resolve_targets(y: int, hook: PostgresHook) -> list[date]:
    ctx = get_current_context()
    conf = (ctx.get("dag_run").conf or {}) if ctx.get("dag_run") else {}
    if "from" in conf or "to" in conf:
        d0 = date.fromisoformat(conf.get("from", f"{y}-01-01"))
        d1 = date.fromisoformat(conf.get("to",   f"{y}-12-31"))
        return _date_range(d0, d1)
    d0 = _next_day_in_year(hook, y)
    d1 = date(y, 12, 31)
    return _date_range(d0, d1)

def _has_data_for_day(hook: PostgresHook, d: date) -> bool:
    sql = f"SELECT 1 FROM {TARGET_TABLE} WHERE datetime::date = %s LIMIT 1"
    return hook.get_first(sql, (d.isoformat(),)) is not None

def _select_date_and_submit(drv, yyyy, mm, dd):
    w = WebDriverWait(drv, 15)
    try:
        year_sel  = Select(w.until(EC.presence_of_element_located((By.ID, "yearSelection"))))
        month_sel = Select(w.until(EC.presence_of_element_located((By.ID, "monthSelection"))))
        day_sel   = Select(w.until(EC.presence_of_element_located((By.ID, "daySelection"))))
        year_sel.select_by_visible_text(str(yyyy))
        month_sel.select_by_index(mm - 1)
        day_sel.select_by_visible_text(str(dd))
        w.until(EC.element_to_be_clickable((By.ID, "dateSubmit"))).click()
        return
    except Exception:
        pass
    year_sel  = Select(w.until(EC.presence_of_element_located((By.CSS_SELECTOR, "select[name='year']"))))
    month_sel = Select(drv.find_element(By.CSS_SELECTOR, "select[name='month']"))
    day_sel   = Select(drv.find_element(By.CSS_SELECTOR, "select[name='day']"))
    year_sel.select_by_value(str(yyyy))
    month_sel.select_by_value(str(mm))
    day_sel.select_by_value(str(dd))
    drv.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

def _parse_table_rows(drv, base_date: date):
    try:
        WebDriverWait(drv, 25).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table")))
    except TimeoutException:
        return []

    table = drv.find_element(By.CSS_SELECTOR, "table")
    thead = table.find_element(By.TAG_NAME, "thead")
    headers = [th.text.replace("\n"," ").strip().lower() for th in thead.find_elements(By.TAG_NAME, "th")]

    def idx(*cands):
        for c in cands:
            if c in headers: return headers.index(c)
        return None

    i_time  = idx("time","local time")
    i_temp  = idx("temperature","temp")
    i_hum   = idx("humidity")
    i_wind  = idx("wind","wind speed")
    i_press = idx("pressure","barometric pressure","pressure (in)")
    i_cond  = idx("conditions","condition","weather","description")

    if None in (i_time, i_temp, i_hum, i_wind, i_press):
        i_time, i_temp, i_hum, i_wind, i_press, i_cond = 0, 1, 3, 5, 7, -1

    recs = []
    tbody = table.find_element(By.TAG_NAME, "tbody")
    for tr in tbody.find_elements(By.TAG_NAME, "tr"):
        tds = tr.find_elements(By.TAG_NAME, "td")
        if not tds:
            continue
        try:
            ts = tds[i_time].text.strip()
            parts = ts.split()
            hhmm = parts[0]
            ampm = parts[1] if len(parts) > 1 else "AM"
            tm = datetime.strptime(f"{hhmm} {ampm}", "%I:%M %p").time()
            dt_local = datetime.combine(base_date, tm)

            temp_f = _to_float(tds[i_temp].text.strip())
            hum    = _to_float(tds[i_hum].text.strip())

            wind_raw = tds[i_wind].text.strip()
            if wind_raw:
                if "calm" in wind_raw.lower():
                    wind_kmh = 0.0
                else:
                    wn = _to_float(wind_raw)
                    wind_kmh = round(wn*1.60934, 2) if (wn is not None and "mph" in wind_raw.lower()) else wn
            else:
                wind_kmh = None

            press_in = _to_float(tds[i_press].text.strip())

            cond_txt = None
            if i_cond is not None and -len(tds) <= i_cond < len(tds):
                ctext = tds[i_cond].text.strip()
                cond_txt = ctext if ctext else None

            recs.append({
                "datetime": dt_local,
                "temperatureF": temp_f,
                "humidity_pct": hum,
                "wind_speed_kmh": wind_kmh,
                "pressure_in": press_in,
                "condition": cond_txt,
            })
        except Exception:
            continue
    return recs

# -------- schema-aware helpers --------
def _table_has_col(hook: PostgresHook, table: str, col: str) -> bool:
    row = hook.get_first("""
      SELECT 1
      FROM information_schema.columns
      WHERE table_schema = current_schema()
        AND table_name = %s
        AND column_name = %s
      LIMIT 1
    """, (table, col))
    return row is not None

def _bulk_upsert(hook: PostgresHook, values: list[tuple], has_updated_at: bool) -> int:
    """UPSERT: ถ้า datetime ซ้ำ → อัปเดตเฉพาะคอลัมน์ที่ EXCLUDED ไม่เป็น NULL
       และจะอัปเดต updated_at ก็ต่อเมื่อมีคอลัมน์นี้จริง ๆ เท่านั้น"""
    if not values:
        return 0
    set_tail = ", updated_at = NOW()" if has_updated_at else ""
    sql = f"""
        INSERT INTO {TARGET_TABLE}
        (datetime, temperatureF, humidity_pct, wind_speed_kmh, pressure_in, condition)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (datetime) DO UPDATE SET
          temperatureF   = COALESCE(EXCLUDED.temperatureF,   {TARGET_TABLE}.temperatureF),
          humidity_pct   = COALESCE(EXCLUDED.humidity_pct,   {TARGET_TABLE}.humidity_pct),
          wind_speed_kmh = COALESCE(EXCLUDED.wind_speed_kmh, {TARGET_TABLE}.wind_speed_kmh),
          pressure_in    = COALESCE(EXCLUDED.pressure_in,    {TARGET_TABLE}.pressure_in),
          condition      = COALESCE(EXCLUDED.condition,      {TARGET_TABLE}.condition)
          {set_tail};
    """
    conn = hook.get_conn()
    with conn:
        with conn.cursor() as cur:
            for i in range(0, len(values), 500):
                cur.executemany(sql, values[i:i+500])
    return len(values)

RESTART_MARKERS = (
    "HTTPConnectionPool", "chrome not reachable", "invalid session id",
    "Unable to evaluate", "Timed out receiving message from renderer",
    "stacktrace", "disconnected", "closed", "no such window", "cannot determine loading status"
)

def _should_restart_driver(exc: Exception) -> bool:
    msg = f"{exc}"
    return any(key.lower() in msg.lower() for key in RESTART_MARKERS)

def _scrape_one_day(drv, url_base: str, d: date) -> list[dict]:
    drv.get(url_base)
    _dismiss_consent_if_any(drv)
    _select_date_and_submit(drv, d.year, d.month, d.day)

    for _ in range(3):
        try:
            WebDriverWait(drv, 25).until(EC.presence_of_element_located((By.CSS_SELECTOR, "table")))
            break
        except TimeoutException:
            time.sleep(1.2)
    else:
        return []

    time.sleep(0.8)
    return _parse_table_rows(drv, d)

def backfill_prev_year_split():
    y = _target_year()
    _create_table_if_not_exists()
    hook = PostgresHook(PG_CONN_ID)

    s = _station()
    url_base = f"https://www.wunderground.com/history/daily/th/mueang-songkhla/{s}"

    targets = _resolve_targets(y, hook)
    if not targets:
        logger.info(f"[{DAG_ID}] Nothing to do.")
        return
    logger.info(f"[{DAG_ID}] Station={s} Year={y} Resume {targets[0]} -> {targets[-1]} (days={len(targets)})")

    # ตรวจว่ามีคอลัมน์ updated_at จริงไหม (ถ้าไม่มีจะไม่อ้างถึง)
    has_updated_at = _table_has_col(hook, TARGET_TABLE, "updated_at")
    logger.info(f"[{DAG_ID}] has_updated_at={has_updated_at}")

    drv = _open_driver()
    rows_total = 0
    try:
        for i, d in enumerate(targets, 1):
            if _has_data_for_day(hook, d):
                logger.info(f"[SKIP] {d} already exists")
                continue

            ok = False
            for attempt in range(1, 4):
                try:
                    recs = _scrape_one_day(drv, url_base, d)
                    if not recs:
                        raise Exception("empty result/table not loaded")

                    vals = [(r["datetime"], r["temperatureF"], r["humidity_pct"],
                             r["wind_speed_kmh"], r["pressure_in"], r["condition"]) for r in recs]
                    rows_total += _bulk_upsert(hook, vals, has_updated_at)  # << ใช้ flag
                    logger.info(f"[COMMIT] {i}/{len(targets)} day={d} rows_total={rows_total}")
                    ok = True
                    break
                except (WebDriverException, Exception) as e:
                    logger.warning(f"Select/parse failed on {d} (attempt {attempt}): {e}")
                    if _should_restart_driver(e) or attempt >= 2:
                        try: drv.quit()
                        except Exception: pass
                        drv = _open_driver()
                    time.sleep(1.0 + uniform(0.5, 1.5))

            if not ok:
                logger.warning(f"[SKIP AFTER RETRIES] {d}")

            time.sleep(uniform(0.6, 1.5))
            if i % 30 == 0:
                time.sleep(uniform(5.0, 9.0))

        logger.info(f"[SUMMARY] year={y} rows_total={rows_total}")

    finally:
        try: drv.quit()
        except Exception: pass

# ===== Airflow DAG =====
default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id=DAG_ID,
    description=f"Backfill previous year's half-hourly weather (with condition) into Postgres (table={TARGET_TABLE})",
    start_date=datetime(2025, 1, 1),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    concurrency=1,
    default_args=default_args,
    tags=["weather","songkhla","backfill","half-hourly","split"],
) as dag:
    PythonOperator(
        task_id="backfill_prev_year_split",
        python_callable=backfill_prev_year_split,
    )
