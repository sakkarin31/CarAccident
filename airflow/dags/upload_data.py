import os
import csv
from datetime import datetime
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.utils.dates import days_ago
import logging

logger = logging.getLogger(__name__)

# หา path ของไฟล์ CSV
DAGS_FOLDER = os.path.dirname(__file__)
CSV_FILE_PATH = os.path.join(DAGS_FOLDER, 'data', 'cleandaily-all-years.csv')

def create_dataset_table_if_not_exists():
    """สร้างตาราง dataset หากยังไม่มี"""
    pg_hook = PostgresHook(postgres_conn_id='postgres_default')
    conn = pg_hook.get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dataset (
            id SERIAL PRIMARY KEY,
            datetime DATE,
            accident INTEGER,
            temperature_f NUMERIC(8, 4),
            humidity_pct NUMERIC(8, 4),
            pressure_in NUMERIC(8, 4),
            vehicles_lt_4_wheels NUMERIC(10, 2),
            vehicles_4_wheels NUMERIC(10, 2),
            vehicles_gt_4_wheels NUMERIC(10, 2),
            day_of_week INTEGER,
            is_weekend BOOLEAN,
            is_holiday BOOLEAN,
            year INTEGER,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(datetime)
        );
    """)
    conn.commit()
    cursor.close()
    conn.close()
    logger.info("✅ ตาราง 'dataset' ถูกตรวจสอบ/สร้างเรียบร้อยแล้ว")

def load_csv_to_postgres():
    """อ่านไฟล์ CSV และ insert ลง PostgreSQL"""
    pg_hook = PostgresHook(postgres_conn_id='postgres_default')
    conn = pg_hook.get_conn()
    cursor = conn.cursor()

    with open(CSV_FILE_PATH, mode='r', encoding='utf-8-sig') as csvfile:  # <-- เปลี่ยนตรงนี้
        reader = csv.DictReader(csvfile)
        for row in reader:
            try:
                cursor.execute("""
                    INSERT INTO dataset (
                        datetime, accident, temperature_f, humidity_pct, pressure_in,
                        vehicles_lt_4_wheels, vehicles_4_wheels, vehicles_gt_4_wheels,
                        day_of_week, is_weekend, is_holiday, year
                    ) VALUES (
                        %(datetime)s,
                        %(accident)s,
                        %(temperature_f)s,
                        %(humidity_pct)s,
                        %(pressure_in)s,
                        %(vehicles_lt_4_wheels)s,
                        %(vehicles_4_wheels)s,
                        %(vehicles_gt_4_wheels)s,
                        %(day_of_week)s,
                        %(is_weekend)s::BOOLEAN,
                        %(is_holiday)s::BOOLEAN,
                        %(year)s
                    )
                    ON CONFLICT (datetime) DO NOTHING;
                """, {
                    'datetime': row['datetime'],  # ตอนนี้จะใช้ได้แล้ว
                    'accident': int(float(row['เกิดเหตุ'])),
                    'temperature_f': float(row['temperature_F']),
                    'humidity_pct': float(row['humidity_%']),
                    'pressure_in': float(row['pressure_in']),
                    'vehicles_lt_4_wheels': float(row['vehicles_lt_4_wheels']),
                    'vehicles_4_wheels': float(row['vehicles_4_wheels']),
                    'vehicles_gt_4_wheels': float(row['vehicles_gt_4_wheels']),
                    'day_of_week': int(row['day_of_week']),
                    'is_weekend': 'true' if int(row['is_weekend']) == 1 else 'false',
                    'is_holiday': 'true' if int(row['is_holiday']) == 1 else 'false',
                    'year': int(row['year'])
                })
            except Exception as e:
                logger.error(f"❌ ข้อผิดพลาดที่แถว {reader.line_num}: {row}")
                logger.exception(e)
                raise

    conn.commit()
    cursor.close()
    conn.close()
    logger.info("✅ โหลดข้อมูลจาก CSV เข้าสู่ตาราง 'dataset' เรียบร้อยแล้ว")

# --- DAG Definition ---
default_args = {
    'owner': 'airflow',
    'start_date': days_ago(1),
}

dag = DAG(
    'load_daily_dataset_to_postgres',
    default_args=default_args,
    description='โหลดข้อมูล daily จาก CSV เข้า PostgreSQL',
    schedule_interval=None,  # รันด้วยตนเอง หรือเปลี่ยนตามต้องการ
    catchup=False,
)

create_table_task = PythonOperator(
    task_id='create_dataset_table',
    python_callable=create_dataset_table_if_not_exists,
    dag=dag,
)

load_data_task = PythonOperator(
    task_id='load_csv_to_postgres',
    python_callable=load_csv_to_postgres,
    dag=dag,
)

create_table_task >> load_data_task