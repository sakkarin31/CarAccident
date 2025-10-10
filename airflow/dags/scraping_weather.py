from datetime import datetime, timedelta, date
import calendar
import time
import logging
import pandas as pd
import os

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_hourly_table_if_not_exists():
    """สร้างตารางสำหรับเก็บข้อมูลรายครึ่งชั่วโมง"""
    pg_hook = PostgresHook(postgres_conn_id='postgres_default')
    conn = pg_hook.get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS songkhla_weather_half_hourly (
            id SERIAL PRIMARY KEY,
            datetime TIMESTAMP,
            temperatureF NUMERIC(6, 2),
            humidity_pct NUMERIC(6, 2),
            wind_speed_kmh NUMERIC(6, 2),
            pressure_in NUMERIC(6, 3),
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(datetime)
        );
    """)
    conn.commit()
    cursor.close()
    conn.close()
    logger.info("✅ ตรวจสอบ/สร้างตาราง songkhla_weather_half_hourly เรียบร้อย")


def scrape_last_2_days_and_upload(**context):
    """Scrape ข้อมูล 2 วันล่าสุด → ดึงทุกครึ่งชั่วโมง → บันทึกลง DB ทันที"""
    today = date.today()
    # Scrap 2 วัน: วันก่อนหน้า และ วันนี้
    target_dates = [
        today - timedelta(days=1),
        today
    ]

    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.binary_location = "/usr/bin/chromium"

    driver = webdriver.Chrome(options=options)
    wait = WebDriverWait(driver, 15)

    try:
        driver.get("https://www.wunderground.com/history/daily/th/mueang-songkhla/VTSS")
        time.sleep(2)

        all_records = []

        for target_date in target_dates:
            year = target_date.year
            month = target_date.month
            day = target_date.day

            logger.info(f"🔄 เริ่ม scrap วันที่ {target_date}")

            try:
                # เลือกปี
                year_select = Select(wait.until(EC.presence_of_element_located((By.ID, "yearSelection"))))
                year_select.select_by_visible_text(str(year))

                # เลือกเดือน
                month_select = Select(wait.until(EC.presence_of_element_located((By.ID, "monthSelection"))))
                month_select.select_by_visible_text(month_names[month - 1])

                # เลือกวัน
                day_select = Select(wait.until(EC.presence_of_element_located((By.ID, "daySelection"))))
                day_select.select_by_visible_text(str(day))

                # คลิก submit
                submit_btn = wait.until(EC.element_to_be_clickable((By.ID, "dateSubmit")))
                submit_btn.click()

                # รอให้ตารางโหลด
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))

                # ดึงแถวข้อมูล
                rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
                logger.info(f"📊 พบ {len(rows)} แถวในวันที่ {target_date}")

                for r in rows:
                    cols = r.find_elements(By.TAG_NAME, "td")
                    if len(cols) >= 10:
                        try:
                            time_str = cols[0].text.strip()
                            temp_f = float(cols[1].text.replace(" °F", "").strip())
                            humidity = float(cols[3].text.replace(" %", "").strip())
                            wind = float(cols[5].text.replace(" km/h", "").replace(" mph", "").strip())
                            pressure = float(cols[7].text.replace(" in", "").strip())

                            # แปลงเวลาเป็น datetime
                            dt_str = f"{year}-{month:02d}-{day:02d} {time_str}"
                            dt = datetime.strptime(dt_str, "%Y-%m-%d %I:%M %p")

                            all_records.append({
                                'datetime': dt,
                                'temperatureF': temp_f,
                                'humidity_pct': humidity,
                                'wind_speed_kmh': wind,
                                'pressure_in': pressure
                            })
                            logger.debug(f"📥 ดึง: {dt} | Temp: {temp_f}°F | Humidity: {humidity}%")

                        except Exception as parse_err:
                            logger.warning(f"⚠️ ข้ามแถว (parse error): {parse_err}")
                            continue

                time.sleep(1)

            except Exception as e:
                logger.error(f"❌ ข้อผิดพลาดขณะ scrap วันที่ {target_date}: {e}")
                continue

        driver.quit()

        if not all_records:
            logger.warning("⚠️ ไม่พบข้อมูลจาก 2 วันล่าสุด")
            return

        # สร้างตารางหากยังไม่มี
        create_hourly_table_if_not_exists()

        # บันทึกลงฐานข้อมูล
        pg_hook = PostgresHook(postgres_conn_id='postgres_default')
        conn = pg_hook.get_conn()
        cursor = conn.cursor()

        insert_query = """
            INSERT INTO songkhla_weather_half_hourly
            (datetime, temperatureF, humidity_pct, wind_speed_kmh, pressure_in)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (datetime) DO NOTHING;
        """

        inserted_count = 0
        for record in all_records:
            try:
                cursor.execute(insert_query, (
                    record['datetime'],
                    record['temperatureF'],
                    record['humidity_pct'],
                    record['wind_speed_kmh'],
                    record['pressure_in']
                ))
                inserted_count += 1
            except Exception as db_err:
                logger.error(f"❌ บันทึกแถวไม่ได้: {db_err}")

        conn.commit()
        cursor.close()
        conn.close()

        logger.info(f"✅ บันทึกข้อมูลเรียบร้อย: {inserted_count} แถว (ทั้งหมด {len(all_records)} แถว)")

    except Exception as main_err:
        logger.error(f"💥 เกิดข้อผิดพลาดร้ายแรง: {main_err}")
        driver.quit()
        raise


# ----------------------------
# DAG Definition
# ----------------------------
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    'songkhla_weather_half_hourly_automation',
    default_args=default_args,
    description='Scrape last 2 days of Songkhla weather (half-hourly raw data) → save to PostgreSQL',
    schedule_interval='0 */6 * * *',  # รันทุก 6 ชั่วโมง
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['weather', 'songkhla', 'automation', 'half-hourly'],
) as dag:

    scrape_task = PythonOperator(
        task_id='scrape_last_2_days_and_upload',
        python_callable=scrape_last_2_days_and_upload
    )