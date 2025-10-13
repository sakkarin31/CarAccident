# -*- coding: utf-8 -*-
"""
Airflow DAG: เติมข้อมูลสภาพอากาศ Songkhla (VTSS) ตั้งแต่ 1 ม.ค. ถึงปัจจุบัน
- สแครปจาก Weather Underground (รายครึ่งชั่วโมง)
- บันทึกลง Postgres "ทันทีหลังจบแต่ละวัน" (commit รายวัน)
- ถ้าข้อมูลปีปัจจุบันครบถึงวันนี้แล้ว จะไม่สแครปเพิ่ม
"""

from datetime import datetime, timedelta, date
import time, logging, re, os, math

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException

# ==============================
# CONFIG
# ==============================
PG_CONN_ID = "postgres_default"
WU_URL = "https://www.wunderground.com/history/daily/th/mueang-songkhla/VTSS"
WAIT_SECS_TABLE = 20
SLEEP_BETWEEN_DAYS = 1.2
PAGE_LOAD_TIMEOUT = 45
MAX_DAYS_PER_RUN = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================
# HELPERS
# ==============================
def _to_float(s: str):
    if not s:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s.replace(",", ""))
    return float(m.group(0)) if m else None


def _truncate(x: float, decimals: int = 2) -> float:
    if x is None:
        return None
    factor = 10 ** decimals
    return math.trunc(x * factor) / factor


def create_hourly_table_if_not_exists():
    pg = PostgresHook(postgres_conn_id=PG_CONN_ID)
    pg.run("""
        CREATE TABLE IF NOT EXISTS songkhla_weather_half_hourly (
            id SERIAL PRIMARY KEY,
            datetime TIMESTAMP UNIQUE,
            temperatureF NUMERIC(6,2),
            humidity_pct NUMERIC(6,2),
            wind_speed_kmh NUMERIC(6,2),
            pressure_in NUMERIC(6,3),
            condition TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    pg.run("ALTER TABLE songkhla_weather_half_hourly ADD COLUMN IF NOT EXISTS condition TEXT;")
    logger.info("✅ ตรวจสอบ/สร้างตาราง songkhla_weather_half_hourly เรียบร้อย")


def get_date_range_to_scrape():
    pg = PostgresHook(postgres_conn_id=PG_CONN_ID)
    conn = pg.get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 
                MIN(datetime) FILTER (WHERE EXTRACT(YEAR FROM datetime) = EXTRACT(YEAR FROM CURRENT_DATE)),
                MAX(datetime) FILTER (WHERE EXTRACT(YEAR FROM datetime) = EXTRACT(YEAR FROM CURRENT_DATE)),
                COUNT(DISTINCT DATE(datetime)) FILTER (WHERE EXTRACT(YEAR FROM datetime) = EXTRACT(YEAR FROM CURRENT_DATE))
            FROM songkhla_weather_half_hourly;
        """)
        row = cur.fetchone()

    min_dt, max_dt, count_days = row
    current_year = date.today().year
    start_date = date(current_year, 1, 1)
    today = date.today()
    total_days_should_have = (today - start_date).days + 1

    if min_dt is None or max_dt is None:
        logger.info("🟡 ยังไม่มีข้อมูลปีนี้เลย → scrape ทั้งปี")
        return [start_date, today]

    if max_dt.date() < today:
        logger.info(f"🟡 มีข้อมูลถึง {max_dt.date()} → เติมต่อถึง {today}")
        return [max_dt.date() + timedelta(days=1), today]

    if count_days < total_days_should_have:
        logger.warning(f"⚠️ พบว่าข้อมูลปีนี้มีแค่ {count_days} วันจาก {total_days_should_have} วัน → สแครปใหม่ทั้งปี")
        return [start_date, today]

    logger.info("✅ ข้อมูลปีนี้ครบทุกวันจนถึงวันนี้แล้ว ไม่ต้อง scrape เพิ่ม")
    return None


def _select_date(driver, year: int, month: int, day: int, wait: WebDriverWait):
    try:
        year_sel = Select(wait.until(EC.presence_of_element_located((By.ID, "yearSelection"))))
        month_sel = Select(wait.until(EC.presence_of_element_located((By.ID, "monthSelection"))))
        day_sel = Select(wait.until(EC.presence_of_element_located((By.ID, "daySelection"))))
        year_sel.select_by_visible_text(str(year))
        month_sel.select_by_index(month - 1)
        day_sel.select_by_visible_text(str(day))
        wait.until(EC.element_to_be_clickable((By.ID, "dateSubmit"))).click()
        return
    except Exception:
        pass

    # fallback
    year_sel = Select(wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "select[name='year']"))))
    month_sel = Select(driver.find_element(By.CSS_SELECTOR, "select[name='month']"))
    day_sel = Select(driver.find_element(By.CSS_SELECTOR, "select[name='day']"))
    year_sel.select_by_value(str(year))
    month_sel.select_by_value(str(month))
    day_sel.select_by_value(str(day))
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()


def _parse_rows(driver, base_date: date, wait: WebDriverWait):
    """อ่านข้อมูลแต่ละแถวในตาราง (แก้ใหม่ให้รองรับ DOM ล่าสุดของ WU)"""
    table = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table")))
    tbody = table.find_element(By.TAG_NAME, "tbody")

    recs = []
    for tr in tbody.find_elements(By.TAG_NAME, "tr"):
        tds = tr.find_elements(By.TAG_NAME, "td")
        if len(tds) < 10:
            continue
        try:
            # เวลา
            time_str = tds[0].text.strip()
            tm = datetime.strptime(
                time_str.split()[0] + " " + (time_str.split()[1] if len(time_str.split()) > 1 else "AM"),
                "%I:%M %p"
            ).time()
            dt_local = datetime.combine(base_date, tm)

            temp_f = _to_float(tds[1].text)
            humid = _to_float(tds[3].text)
            pressure = _to_float(tds[7].text)
            condition = tds[9].text.strip()

            # ✅ wind speed จาก span.wu-value / wu-label
            wind_kmh = None
            try:
                wind_cell = tds[5]
                val_elem = wind_cell.find_element(By.CSS_SELECTOR, "span.wu-value")
                unit_elem = wind_cell.find_element(By.CSS_SELECTOR, "span.wu-label")
                val = float(val_elem.text.strip())
                unit = unit_elem.text.strip().lower()
                wind_kmh = val * 1.60934 if "mph" in unit else val
                wind_kmh = _truncate(wind_kmh, 2)
            except Exception:
                wind_raw = tds[5].text.strip()
                if "calm" in wind_raw.lower():
                    wind_kmh = 0.0
                else:
                    match = re.search(r"[-+]?\d+(?:\.\d+)?", wind_raw)
                    if match:
                        val = float(match.group(0))
                        kmh = val * 1.60934 if "mph" in wind_raw.lower() else val
                        wind_kmh = _truncate(kmh, 2)

            recs.append({
                "datetime": dt_local,
                "temperatureF": temp_f,
                "humidity_pct": humid,
                "wind_speed_kmh": wind_kmh,
                "pressure_in": pressure,
                "condition": condition or None
            })
        except Exception as e:
            logger.warning(f"⚠️ parse แถวผิดพลาด: {e}")
            continue
    return recs


# ==============================
# MAIN TASK
# ==============================
def scrape_missing_days_and_upload(**_):
    create_hourly_table_if_not_exists()
    range_to_scrape = get_date_range_to_scrape()
    if not range_to_scrape:
        logger.info("✅ ข้อมูลปีปัจจุบันครบถึงวันนี้แล้ว ไม่ต้อง scrape เพิ่ม")
        return

    start_date, end_date = range_to_scrape
    logger.info(f"📆 เติมข้อมูลจาก {start_date} ถึง {end_date}")

    hook = PostgresHook(PG_CONN_ID)
    conn = hook.get_conn()
    conn.autocommit = False

    upsert_sql = """
        INSERT INTO songkhla_weather_half_hourly
        (datetime, temperatureF, humidity_pct, wind_speed_kmh, pressure_in, condition)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (datetime) DO UPDATE SET
          temperatureF   = COALESCE(EXCLUDED.temperatureF,   songkhla_weather_half_hourly.temperatureF),
          humidity_pct   = COALESCE(EXCLUDED.humidity_pct,   songkhla_weather_half_hourly.humidity_pct),
          wind_speed_kmh = COALESCE(EXCLUDED.wind_speed_kmh, songkhla_weather_half_hourly.wind_speed_kmh),
          pressure_in    = COALESCE(EXCLUDED.pressure_in,    songkhla_weather_half_hourly.pressure_in),
          condition      = COALESCE(EXCLUDED.condition,      songkhla_weather_half_hourly.condition);
    """

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1200,1600")
    opts.binary_location = "/usr/bin/chromium"

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    wait = WebDriverWait(driver, WAIT_SECS_TABLE)

    try:
        driver.get(WU_URL)
        time.sleep(1.5)

        with conn:
            with conn.cursor() as cur:
                d = start_date
                while d <= end_date:
                    logger.info(f"🔄 Scrape {d.isoformat()}")
                    try:
                        _select_date(driver, d.year, d.month, d.day, wait)
                        recs = _parse_rows(driver, d, wait)
                        if recs:
                            day_vals = sorted([
                                (
                                    r["datetime"],
                                    r["temperatureF"],
                                    r["humidity_pct"],
                                    r["wind_speed_kmh"],
                                    r["pressure_in"],
                                    r["condition"]
                                )
                                for r in recs
                            ], key=lambda x: x[0])
                            cur.executemany(upsert_sql, day_vals)
                            conn.commit()
                            logger.info(f"✅ บันทึก {len(day_vals)} แถวสำหรับวันที่ {d}")
                        else:
                            logger.warning(f"📭 ไม่มีข้อมูลในวันที่ {d}")
                    except Exception as e:
                        logger.warning(f"⚠️ วันที่ {d} มีปัญหา: {e}")
                        conn.rollback()

                    d += timedelta(days=1)
                    time.sleep(SLEEP_BETWEEN_DAYS)

    finally:
        try:
            driver.quit()
        except Exception:
            pass
        conn.close()

    logger.info("✅ งานรอบนี้เสร็จสิ้น")


# ==============================
# DAG Definition
# ==============================
default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="songkhla_weather_daily_fill_to_present",
    default_args=default_args,
    description="Scrape VTSS Songkhla weather (half-hourly) until present → Postgres (commit daily)",
    schedule_interval="0 9 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["weather", "songkhla", "daily-fill"],
) as dag:
    PythonOperator(
        task_id="scrape_missing_days_and_upload",
        python_callable=scrape_missing_days_and_upload,
    )
