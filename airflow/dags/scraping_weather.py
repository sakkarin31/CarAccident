from datetime import datetime, timedelta, date
import time, logging, re, os

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
WU_URL = "https://www.wunderground.com/history/daily/th/mueang-songkhla/VTSS"  # สงขลา/หาดใหญ่

def _to_float(s: str):
    if not s: return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", s.replace(",", ""))
    return float(m.group(0)) if m else None

def create_hourly_table_if_not_exists():
    """สร้าง/อัปเดตตารางให้มีคอลัมน์ condition ด้วย"""
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
    logger.info("✅ ตรวจสอบ/สร้างตาราง songkhla_weather_half_hourly (พร้อมคอลัมน์ condition) เรียบร้อย")

def _select_date(driver, year: int, month: int, day: int, wait: WebDriverWait):
    """พยายามเลือกวันที่ด้วย 2 วิธี (ID เดิม / ชื่อ name=year,month,day)"""
    try:
        # ชุด ID แบบเดิม
        year_sel  = Select(wait.until(EC.presence_of_element_located((By.ID, "yearSelection"))))
        month_sel = Select(wait.until(EC.presence_of_element_located((By.ID, "monthSelection"))))
        day_sel   = Select(wait.until(EC.presence_of_element_located((By.ID, "daySelection"))))
        year_sel.select_by_visible_text(str(year))
        month_sel.select_by_index(month-1)  # 0-based
        day_sel.select_by_visible_text(str(day))
        wait.until(EC.element_to_be_clickable((By.ID, "dateSubmit"))).click()
        return
    except Exception:
        pass
    # fallback: select[name='year'|'month'|'day']
    year_sel  = Select(wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "select[name='year']"))))
    month_sel = Select(driver.find_element(By.CSS_SELECTOR, "select[name='month']"))
    day_sel   = Select(driver.find_element(By.CSS_SELECTOR, "select[name='day']"))
    year_sel.select_by_value(str(year))
    month_sel.select_by_value(str(month))  # 1..12
    day_sel.select_by_value(str(day))
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()

def _parse_rows(driver, base_date: date, wait: WebDriverWait):
    """อ่านตารางด้วย header mapping → ดึง condition ให้ได้ (ไม่พึ่งตำแหน่งคงที่)"""
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

    # fallback ถ้าเว็บเปลี่ยนหัว
    if None in (i_time, i_temp, i_hum, i_wind, i_press):
        i_time, i_temp, i_hum, i_wind, i_press, i_cond = 0, 1, 3, 5, 7, -1

    recs = []
    for tr in table.find_element(By.TAG_NAME, "tbody").find_elements(By.TAG_NAME, "tr"):
        tds = tr.find_elements(By.TAG_NAME, "td")
        if not tds: 
            continue
        try:
            # เวลา
            ts = tds[i_time].text.strip()
            tm = datetime.strptime(
                ts.split()[0] + " " + (ts.split()[1] if len(ts.split())>1 else "AM"),
                "%I:%M %p"
            ).time()
            dt_local = datetime.combine(base_date, tm)

            # อุณหภูมิ/ความชื้น
            temp_f = _to_float(tds[i_temp].text.strip())
            humid  = _to_float(tds[i_hum].text.strip())

            # ลม → km/h (รองรับ Calm, mph)
            wind_raw = tds[i_wind].text.strip()
            if wind_raw:
                if "calm" in wind_raw.lower():
                    wind_kmh = 0.0
                else:
                    wn = _to_float(wind_raw)
                    wind_kmh = round(wn*1.60934, 2) if (wn is not None and "mph" in wind_raw.lower()) else wn
            else:
                wind_kmh = None

            # ความกดอากาศ (in)
            press_in = _to_float(tds[i_press].text.strip())

            # สภาพอากาศ
            cond_txt = None
            if i_cond is not None and -len(tds) <= i_cond < len(tds):
                ctext = tds[i_cond].text.strip()
                cond_txt = ctext if ctext else None
            # ถ้าอยากไม่ให้เป็น NULL เลย: เปิดใช้บรรทัดล่าง
            # if cond_txt is None: cond_txt = "Unknown"

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

def scrape_last_2_days_and_upload(**_):
    """Scrape 2 วันล่าสุด → ใส่ Postgres (UPSERT เพื่อเติม condition ได้)"""
    create_hourly_table_if_not_exists()

    today = date.today()
    targets = [today - timedelta(days=1), today]

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

    try:
        driver.get(WU_URL)
        time.sleep(1.5)

        all_vals = []
        for d in targets:
            y, m, dd = d.year, d.month, d.day
            logger.info(f"🔄 Scrape {d.isoformat()}")
            try:
                _select_date(driver, y, m, dd, wait)
            except Exception as e:
                logger.error(f"เลือกวันที่ไม่สำเร็จ: {e}")
                continue

            # รอให้ตารางขึ้น
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table")))
            except Exception:
                logger.warning("ไม่พบตาราง ข้ามวันนี้")
                continue

            time.sleep(0.8)  # กัน DOM สะบัดเล็กน้อย
            recs = _parse_rows(driver, d, wait)
            if not recs:
                logger.warning("📭 วันนี้ไม่มีแถวข้อมูล")
                continue

            all_vals += [
                (r["datetime"], r["temperatureF"], r["humidity_pct"], r["wind_speed_kmh"], r["pressure_in"], r["condition"])
                for r in recs
            ]

        if not all_vals:
            logger.warning("⚠️ ไม่พบข้อมูลจาก 2 วันล่าสุด")
            return

        # UPSERT (ถ้าเคยเก็บไว้แล้วแบบไม่มี condition → เติมให้)
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
        hook = PostgresHook(PG_CONN_ID)
        conn = hook.get_conn()
        with conn:
            with conn.cursor() as cur:
                for i in range(0, len(all_vals), 500):
                    cur.executemany(upsert_sql, all_vals[i:i+500])

        logger.info(f"✅ Upsert เรียบร้อย: {len(all_vals)} แถว")

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
    dag_id="songkhla_weather_half_hourly_automation",
    default_args=default_args,
    description="Scrape last 2 days (half-hourly) → Postgres, with condition",
    schedule_interval="0 */6 * * *",
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=["weather","songkhla","half-hourly"],
) as dag:
    PythonOperator(
        task_id="scrape_last_2_days_and_upload",
        python_callable=scrape_last_2_days_and_upload
    )
