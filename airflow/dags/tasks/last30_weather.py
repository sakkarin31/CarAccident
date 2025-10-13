from datetime import datetime, timedelta, date
import time, logging, re, os
from collections import Counter

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PG_CONN_ID = "postgres_default"
WU_URL = "https://www.wunderground.com/history/daily/th/mueang-songkhla/VTSS"

def _to_float(s: str):
    if not s: return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s.replace(",", ""))
    return float(m.group(0)) if m else None

def create_daily_table_if_not_exists():
    """สร้างตาราง last30weather_db สำหรับเก็บค่าเฉลี่ยรายวัน"""
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

def _select_date(driver, year: int, month: int, day: int, wait: WebDriverWait):
    try:
        year_sel  = Select(wait.until(EC.presence_of_element_located((By.ID, "yearSelection"))))
        month_sel = Select(wait.until(EC.presence_of_element_located((By.ID, "monthSelection"))))
        day_sel   = Select(wait.until(EC.presence_of_element_located((By.ID, "daySelection"))))
        year_sel.select_by_visible_text(str(year))
        month_sel.select_by_index(month-1)
        day_sel.select_by_visible_text(str(day))
        wait.until(EC.element_to_be_clickable((By.ID, "dateSubmit"))).click()
        return
    except Exception:
        pass
    year_sel  = Select(wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "select[name='year']"))))
    month_sel = Select(driver.find_element(By.CSS_SELECTOR, "select[name='month']"))
    day_sel   = Select(driver.find_element(By.CSS_SELECTOR, "select[name='day']"))
    year_sel.select_by_value(str(year))
    month_sel.select_by_value(str(month))
    day_sel.select_by_value(str(day))
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

def _parse_rows(driver, base_date: date, wait: WebDriverWait):
    table = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table")))
    thead = table.find_element(By.TAG_NAME, "thead")
    headers = [th.text.replace("\n"," ").strip().lower() for th in thead.find_elements(By.TAG_NAME, "th")]

    def idx(*cands):
        for c in cands:
            if c in headers: return headers.index(c)
        return None

    i_time  = idx("time", "local time")
    i_temp  = idx("temperature", "temp")
    i_hum   = idx("humidity")
    i_wind  = idx("wind", "wind speed")
    i_press = idx("pressure", "barometric pressure", "pressure (in)")
    i_cond  = idx("conditions", "condition", "weather", "description")

    if None in (i_time, i_temp, i_hum, i_wind, i_press):
        i_time, i_temp, i_hum, i_wind, i_press, i_cond = 0, 1, 3, 5, 7, -1

    recs = []
    for tr in table.find_element(By.TAG_NAME, "tbody").find_elements(By.TAG_NAME, "tr"):
        tds = tr.find_elements(By.TAG_NAME, "td")
        if not tds: 
            continue
        try:
            ts = tds[i_time].text.strip()
            tm = datetime.strptime(
                ts.split()[0] + " " + (ts.split()[1] if len(ts.split())>1 else "AM"),
                "%I:%M %p"
            ).time()
            dt_local = datetime.combine(base_date, tm)

            temp_f = _to_float(tds[i_temp].text.strip())
            humid  = _to_float(tds[i_hum].text.strip())

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
                "humidity_pct": humid,
                "wind_speed_kmh": wind_kmh,
                "pressure_in": press_in,
                "condition": cond_txt,
            })
        except Exception:
            continue
    return recs

def scrape_last_30_days_and_upload_daily_avg(**_):
    create_daily_table_if_not_exists()

    today = date.today()
    expected_dates = {today - timedelta(days=i) for i in range(30)}

    # 🔍 ตรวจสอบว่ามีข้อมูลครบ 30 วันล่าสุดใน DB หรือไม่
    pg_hook = PostgresHook(postgres_conn_id=PG_CONN_ID)
    df_existing = pg_hook.get_pandas_df("""
        SELECT date FROM last30weather_db
        WHERE date >= %s
    """, parameters=[today - timedelta(days=29)])

    if not df_existing.empty:
        existing_date_set = set(df_existing['date'].dt.date)
    else:
        existing_date_set = set()

    if expected_dates.issubset(existing_date_set):
        logger.info("✅ ข้อมูล 30 วันล่าสุดมีอยู่ครบในฐานข้อมูลแล้ว — ข้ามการ scrape")
        return

    logger.info(f"🔍 พบข้อมูล {len(existing_date_set)} วันจาก 30 วันที่ต้องการ — จะดำเนินการ scrape ต่อ")

    # --- เริ่ม scraping ---
    targets = [today - timedelta(days=i) for i in range(30)]

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1200,1600")
    opts.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36")
    opts.binary_location = "/usr/bin/chromium"

    driver = webdriver.Chrome(options=opts)
    wait = WebDriverWait(driver, 20)

    daily_data = {}

    try:
        driver.get(WU_URL)
        time.sleep(1.5)

        for d in targets:
            logger.info(f"🔄 Scraping {d.isoformat()}")
            try:
                _select_date(driver, d.year, d.month, d.day, wait)
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table")))
                time.sleep(0.8)
                recs = _parse_rows(driver, d, wait)
                if recs:
                    daily_data[d] = recs
                else:
                    logger.warning(f"📭 ไม่มีข้อมูลสำหรับ {d}")
            except Exception as e:
                logger.error(f"❌ ข้อผิดพลาดขณะ scrape {d}: {e}")
                continue

        # รวมเป็นค่าเฉลี่ยรายวัน
        daily_averages = []
        for d, records in daily_data.items():
            temps = [r["temperatureF"] for r in records if r["temperatureF"] is not None]
            hums  = [r["humidity_pct"] for r in records if r["humidity_pct"] is not None]
            winds = [r["wind_speed_kmh"] for r in records if r["wind_speed_kmh"] is not None]
            press = [r["pressure_in"] for r in records if r["pressure_in"] is not None]
            conds = [r["condition"] for r in records if r["condition"] is not None]

            avg_temp = round(sum(temps)/len(temps), 2) if temps else None
            avg_hum  = round(sum(hums)/len(hums), 2) if hums else None
            avg_wind = round(sum(winds)/len(winds), 2) if winds else None
            avg_press = round(sum(press)/len(press), 3) if press else None

            common_cond = None
            if conds:
                common_cond = Counter(conds).most_common(1)[0][0]

            daily_averages.append((d, avg_temp, avg_hum, avg_wind, avg_press, common_cond))

        if not daily_averages:
            logger.warning("⚠️ ไม่มีข้อมูลรายวันใดๆ ที่จะบันทึก")
            return

        # UPSERT ลงตาราง
        upsert_sql = """
            INSERT INTO last30weather_db
            (date, avg_temperatureF, avg_humidity_pct, avg_wind_speed_kmh, avg_pressure_in, common_condition)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (date) DO UPDATE SET
                avg_temperatureF     = EXCLUDED.avg_temperatureF,
                avg_humidity_pct     = EXCLUDED.avg_humidity_pct,
                avg_wind_speed_kmh   = EXCLUDED.avg_wind_speed_kmh,
                avg_pressure_in      = EXCLUDED.avg_pressure_in,
                common_condition     = EXCLUDED.common_condition,
                created_at           = NOW();
        """
        conn = pg_hook.get_conn()
        with conn:
            with conn.cursor() as cur:
                for i in range(0, len(daily_averages), 500):
                    cur.executemany(upsert_sql, daily_averages[i:i+500])

        logger.info(f"✅ บันทึกค่าเฉลี่ยรายวันเรียบร้อย: {len(daily_averages)} วัน")

    finally:
        try:
            driver.quit()
        except Exception:
            pass

# ----------------------------
# DAG Definition
# ----------------------------
default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="songkhla_weather_daily_avg_last30",
    default_args=default_args,
    description="Scrape last 30 days → compute daily averages → store in last30weather_db (skip if already complete)",
    schedule_interval="0 2 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["weather", "songkhla", "daily", "last30"],
) as dag:
    PythonOperator(
        task_id="scrape_last_30_days_and_upload_daily_avg",
        python_callable=scrape_last_30_days_and_upload_daily_avg
    )