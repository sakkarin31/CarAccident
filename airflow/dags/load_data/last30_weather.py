# -*- coding: utf-8 -*-
from datetime import datetime, timedelta, date
import time, logging, re, math
from collections import defaultdict, Counter

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook

# ========== IMPORT SELENIUM (จำเป็น!) ==========
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
WU_URL = "https://www.wunderground.com/history/daily/th/mueang-songkhla/VTSS"  # ⚠️ ลบช่องว่างท้าย!
WAIT_SECS_TABLE = 20
SLEEP_BETWEEN_DAYS = 1.2
PAGE_LOAD_TIMEOUT = 45

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==============================
# HELPERS (จำเป็นสำหรับ _select_date และ _parse_rows)
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
    table = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table")))
    tbody = table.find_element(By.TAG_NAME, "tbody")

    recs = []
    for tr in tbody.find_elements(By.TAG_NAME, "tr"):
        tds = tr.find_elements(By.TAG_NAME, "td")
        if len(tds) < 10:
            continue
        try:
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
# NEW: สร้างตารางสำหรับ last 30 days
# ==============================
def create_daily_table_if_not_exists():
    pg = PostgresHook(postgres_conn_id=PG_CONN_ID)
    pg.run("""
        CREATE TABLE IF NOT EXISTS last30weather_db (
            id SERIAL PRIMARY KEY,
            date DATE UNIQUE,
            avg_temperatureF NUMERIC(6,2),
            avg_humidity_pct NUMERIC(6,2),
            avg_wind_speed_kmh NUMERIC(6,2),
            avg_pressure_in NUMERIC(6,3),
            common_condition TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    logger.info("✅ สร้าง/ตรวจสอบตาราง last30weather_db เรียบร้อย")

# ==============================
# NEW: ได้ช่วง 30 วันล่าสุด
# ==============================
def get_last_30_days():
    today = date.today()
    start_date = today - timedelta(days=29)
    return start_date, today

# ==============================
# HELPER: รวมข้อมูลรายวัน
# ==============================
def aggregate_daily_data(all_records):
    daily = defaultdict(list)
    for rec in all_records:
        d = rec["datetime"].date()
        daily[d].append(rec)

    results = []
    for d, records in daily.items():
        temps = [r["temperatureF"] for r in records if r["temperatureF"] is not None]
        humids = [r["humidity_pct"] for r in records if r["humidity_pct"] is not None]
        winds = [r["wind_speed_kmh"] for r in records if r["wind_speed_kmh"] is not None]
        pressures = [r["pressure_in"] for r in records if r["pressure_in"] is not None]
        conditions = [r["condition"] for r in records if r["condition"]]

        avg_temp = round(sum(temps) / len(temps), 2) if temps else None
        avg_humid = round(sum(humids) / len(humids), 2) if humids else None
        avg_wind = round(sum(winds) / len(winds), 2) if winds else None
        avg_press = round(sum(pressures) / len(pressures), 3) if pressures else None

        common_cond = None
        if conditions:
            common_cond = Counter(conditions).most_common(1)[0][0]

        results.append({
            "date": d,
            "avg_temperatureF": avg_temp,
            "avg_humidity_pct": avg_humid,
            "avg_wind_speed_kmh": avg_wind,
            "avg_pressure_in": avg_press,
            "common_condition": common_cond
        })
    return results

# ==============================
# MAIN TASK
# ==============================
def scrape_last_30_days_and_save_daily_avg(**_):
    create_daily_table_if_not_exists()
    start_date, end_date = get_last_30_days()
    logger.info(f"🔄 จะสแครปข้อมูลจาก {start_date} ถึง {end_date} (30 วัน)")

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

    all_records = []
    try:
        driver.get(WU_URL)
        time.sleep(1.5)

        d = start_date
        while d <= end_date:
            logger.info(f"🔄 สแครปวันที่ {d}")
            try:
                _select_date(driver, d.year, d.month, d.day, wait)
                recs = _parse_rows(driver, d, wait)
                all_records.extend(recs)
                logger.info(f"✅ ได้ {len(recs)} แถว สำหรับวันที่ {d}")
            except Exception as e:
                logger.warning(f"⚠️ วันที่ {d} ผิดพลาด: {e}")
            d += timedelta(days=1)
            time.sleep(SLEEP_BETWEEN_DAYS)
    finally:
        driver.quit()

    if not all_records:
        logger.warning("📭 ไม่ได้ข้อมูลเลยจาก 30 วันล่าสุด")
        return

    daily_data = aggregate_daily_data(all_records)
    logger.info(f"📊 รวมได้ {len(daily_data)} วัน สำหรับบันทึกลงฐานข้อมูล")

    hook = PostgresHook(PG_CONN_ID)
    conn = hook.get_conn()
    with conn:
        with conn.cursor() as cur:
            upsert_sql = """
                INSERT INTO last30weather_db
                (date, avg_temperatureF, avg_humidity_pct, avg_wind_speed_kmh, avg_pressure_in, common_condition)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (date) DO UPDATE SET
                    avg_temperatureF = EXCLUDED.avg_temperatureF,
                    avg_humidity_pct = EXCLUDED.avg_humidity_pct,
                    avg_wind_speed_kmh = EXCLUDED.avg_wind_speed_kmh,
                    avg_pressure_in = EXCLUDED.avg_pressure_in,
                    common_condition = EXCLUDED.common_condition,
                    created_at = NOW();
            """
            for row in daily_data:
                cur.execute(upsert_sql, (
                    row["date"],
                    row["avg_temperatureF"],
                    row["avg_humidity_pct"],
                    row["avg_wind_speed_kmh"],
                    row["avg_pressure_in"],
                    row["common_condition"]
                ))
    logger.info("✅ บันทึกข้อมูล 30 วันล่าสุดลง last30weather_db เรียบร้อย")

# ==============================
# DAG
# ==============================
default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

if __name__ == "__main__":
    scrape_last_30_days_and_save_daily_avg()
