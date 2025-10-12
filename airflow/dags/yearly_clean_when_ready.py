# dags/yearly_clean_when_ready.py
from __future__ import annotations
from datetime import datetime
import calendar
from pathlib import Path
import pandas as pd

from airflow import DAG
from airflow.operators.python import PythonOperator, ShortCircuitOperator
from airflow.operators.bash import BashOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook

# ===== CONFIG =====
DATA_DIR = Path("/opt/airflow/data")
PG_CONN_ID = "postgres_default"

# เปลี่ยนเป็นชื่อปลายทางของคุณตามจริง
TARGET_TABLE = "songkhla_daily_clean"

def target_year(execution_date: datetime) -> int:
    # ใช้ปีที่ผ่านมา
    return execution_date.year - 1

# ---------- Readiness ----------
def is_aadt_ready(year: int, hook: PostgresHook) -> bool:
    # ตัด source_year_ad ที่ไม่มีออก
    n = hook.get_first(
        f"SELECT COUNT(*) FROM aadt_table WHERE source_year_be = {year+543}"
    )[0]
    return (n or 0) > 0

def is_accident_ready(year: int, hook: PostgresHook) -> bool:
    # ตรวจว่าปีนั้นมีครบ 12 เดือนในตาราง accident_events
    months = hook.get_first(
        f"SELECT COUNT(DISTINCT EXTRACT(MONTH FROM accident_date)) "
        f"FROM accident_events WHERE EXTRACT(YEAR FROM accident_date) = {year}"
    )[0]
    return (months or 0) >= 12

def is_weather_ready(year: int, hook: PostgresHook) -> bool:
    # ใช้ตาราง _prev ตามชุดข้อมูลปัจจุบัน
    expected = 17568 if calendar.isleap(year) else 17520
    min_rows = expected - 48
    rows = hook.get_first(
        f"SELECT COUNT(*) FROM songkhla_weather_half_hourly_prev "
        f"WHERE EXTRACT(YEAR FROM datetime) = {year}"
    )[0]
    return (rows or 0) >= min_rows

def gate_all_ready(**context) -> bool:
    y = target_year(context["logical_date"])
    hook = PostgresHook(PG_CONN_ID)
    aadt_ok = is_aadt_ready(y, hook)
    acc_ok  = is_accident_ready(y, hook)
    wea_ok  = is_weather_ready(y, hook)
    print(f"[readiness] year={y} aadt={aadt_ok} accident={acc_ok} weather={wea_ok}")
    return aadt_ok and acc_ok and wea_ok

# ---------- Export RAW → CSV ----------
def export_year_from_postgres(**context):
    y = target_year(context["logical_date"])
    hook = PostgresHook(PG_CONN_ID)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Accident: เอาเฉพาะคอลัมน์ที่มีจริงใน accident_events
    # (ตัด province_th / veh_* ออก; ให้สคริปต์ clean ไปจัดการต่อเอง)
    acc_sql = f"""
    COPY (
        SELECT
          COALESCE(province, 'สงขลา') AS "จังหวัด",
          route_code  AS "รหัสสายทาง",
          to_char(accident_date,'DD/MM/YYYY') AS "วันที่เกิดเหตุ",
          to_char(accident_time,'HH24:MI:SS') AS "เวลาเกิดเหตุ",
          latitude    AS "ละติจูด",
          longitude   AS "ลองจิจูด"
        FROM accident_events
        WHERE EXTRACT(YEAR FROM accident_date) = {y}
    ) TO STDOUT WITH CSV HEADER ENCODING 'UTF8';
    """
    hook.copy_expert(sql=acc_sql, filename=str(DATA_DIR / f"accident{y}.csv"))

    # AADT: ใช้คอลัมน์ที่มีจริงใน aadt_table (โปรดปรับชื่อ field ให้ตรง schema คุณ)
    year_be = y + 543                # เช่น 2567
    short_year = str(year_be)[-2:]   # ได้ '67'
    aadt_filename = f"aadt-{short_year}.csv"

    aadt_sql = f"""
    COPY (
        SELECT
            province             AS "จังหวัด",
            route_no             AS "ทางหลวงสาย",
            control_section      AS "ตอนควบคุม",
            route_name           AS "ชื่อสายทาง",
            station_km           AS "จุดสำรวจ",

            car_le_7             AS "รถยนต์นั่ง(≤7คน)",
            car_gt_7             AS "รถยนต์นั่ง(>7คน)",
            bus_small            AS "รถโดยสารขนาดเล็ก",
            bus_medium           AS "รถโดยสารขนาดกลาง",
            bus_large            AS "รถโดยสารขนาดใหญ่",
            truck_4w             AS "รถบรรทุก4ล้อ",
            truck_6w             AS "รถบรรทุก6ล้อ",
            truck_10w            AS "รถบรรทุก10ล้อ",
            trailer              AS "รถบรรทุกพ่วง",
            semi_trailer         AS "รถบรรทุกกึ่งพ่วง",
            bicycle_2_3          AS "จักรยาน2-3ล้อ",
            moto_tricycle        AS "สามล้อเครื่องและจักรยานยนต์",

            total                AS "รวม",
            heavy_pct            AS "%ของยานยนต์หนัก",
            highway_district     AS "แขวงทางหลวง"
        FROM aadt_table
        WHERE source_year_be = {year_be}
    ) TO STDOUT WITH CSV HEADER ENCODING 'UTF8';
    """

    hook.copy_expert(sql=aadt_sql, filename=str(DATA_DIR / aadt_filename))
    # Weather: ดึงจากตาราง _prev และจัดหัวตารางให้เข้ากับสคริปต์ clean (date,time,...)
    wea_sql = f"""
    COPY (
        SELECT
          to_char(datetime, 'DD/MM/YYYY') AS "date",
          to_char(datetime, 'HH12:MI AM')  AS "time",
          temperatureF     AS "temperature_F",
          humidity_pct     AS "humidity_%",
          wind_speed_kmh,
          pressure_in,
          condition
        FROM songkhla_weather_half_hourly_prev
        WHERE EXTRACT(YEAR FROM datetime) = {y}
        ORDER BY datetime
    ) TO STDOUT WITH CSV HEADER ENCODING 'UTF8';
    """
    hook.copy_expert(sql=wea_sql, filename=str(DATA_DIR / f"songkhla_weather_{y}.csv"))

# ---------- Load FINAL → Postgres ----------
def create_target_table_if_not_exists():
    hook = PostgresHook(PG_CONN_ID)
    hook.run(f"""
    CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (
        date              date PRIMARY KEY,
        temperature_f     double precision,
        humidity_pct      double precision,
        pressure_in       double precision,
        accidents         integer,
        vehicles_lt_4     integer,
        vehicles_eq_4     integer,
        vehicles_gt_4     integer,
        day_of_week       smallint,
        is_weekend        boolean,
        is_holiday        boolean
    );
    """)

def load_final_all_years_into_postgres(**context):
    hook = PostgresHook(PG_CONN_ID)
    create_target_table_if_not_exists()

    csv_path = DATA_DIR / "cleandaily-all-years.csv"
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    rename_map = {
        "datetime": "date",
        "temperature_F": "temperature_f",
        "humidity_%": "humidity_pct",
        "pressure_in": "pressure_in",
        "เกิดเหตุ": "accidents",
        "vehicles_lt_4_wheels": "vehicles_lt_4",
        "vehicles_4_wheels": "vehicles_eq_4",
        "vehicles_gt_4_wheels": "vehicles_gt_4",
        "day_of_week": "day_of_week",
        "is_weekend": "is_weekend",
        "is_holiday": "is_holiday",
    }

    df = pd.read_csv(csv_path).rename(columns=rename_map)

    # 1) date -> date จริง
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date

    # 2) เลือกเฉพาะคอลัมน์ที่ต้องใช้
    cols = ["date","temperature_f","humidity_pct","pressure_in",
            "accidents","vehicles_lt_4","vehicles_eq_4","vehicles_gt_4",
            "day_of_week","is_weekend","is_holiday"]
    cols = [c for c in cols if c in df.columns]
    df = df[cols]

    # 3) ทำความสะอาดและ cast ชนิดข้อมูลให้ตรง schema
    #    - คอลัมน์นับ/ลำดับวันที่: ปัดเศษแล้วเป็น int
    int_cols = [c for c in ["accidents","vehicles_lt_4","vehicles_eq_4","vehicles_gt_4","day_of_week"] if c in df.columns]
    for c in int_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
        # เผื่อมีค่าเป็น 106.0 ให้ปัด แล้วแปลงเป็น int
        df[c] = df[c].round().astype(int)

    #    - boolean: แปลงเป็น True/False (Postgres boolean รับ 'true'/'false' ได้)
    for b in ["is_weekend","is_holiday"]:
        if b in df.columns:
            # รองรับทั้ง 0/1, '0'/'1', True/False, 'true'/'false'
            df[b] = df[b].map({1: True, 0: False, "1": True, "0": False, True: True, False: False}).fillna(False).astype(bool)

    #    - ค่าตัวชี้วัดสภาพอากาศให้เป็นตัวเลข (float)
    for f in ["temperature_f","humidity_pct","pressure_in"]:
        if f in df.columns:
            df[f] = pd.to_numeric(df[f], errors="coerce")

    #    - ตัดแถวที่ไม่มีวันที่ (กัน NULL date)
    if "date" in df.columns:
        df = df.dropna(subset=["date"])

    # 4) เขียนไฟล์ชั่วคราวแล้ว COPY
    tmp = csv_path.parent / "to_load_all.csv"
    df.to_csv(tmp, index=False)

    stage = f"_load_{TARGET_TABLE}"
    col_list = ", ".join(df.columns)  # ใช้จาก df หลังจัดชัดเจนแล้ว

    hook.run(f"DROP TABLE IF EXISTS {stage};")
    hook.run(f"CREATE TABLE {stage} (LIKE {TARGET_TABLE} INCLUDING DEFAULTS);")

    hook.copy_expert(
        sql=f"COPY {stage} ({col_list}) FROM STDIN WITH CSV HEADER",
        filename=str(tmp)
    )

    set_updates = ", ".join([f"{c} = EXCLUDED.{c}" for c in df.columns if c != "date"])
    hook.run(f"""
        INSERT INTO {TARGET_TABLE} ({col_list})
        SELECT {col_list} FROM {stage}
        ON CONFLICT (date) DO UPDATE SET
          {set_updates};
        DROP TABLE IF EXISTS {stage};
    """)


# ---------- Archive/Prune (ออปชัน) ----------
def archive_raw_year(**context):
    y = target_year(context["logical_date"])
    outdir = DATA_DIR / "archive" / str(y)
    outdir.mkdir(parents=True, exist_ok=True)
    hook = PostgresHook(PG_CONN_ID)

    with hook.get_conn() as conn:
        acc = pd.read_sql(f"SELECT * FROM accident_events WHERE EXTRACT(YEAR FROM accident_date) = {y}", conn)
        wea = pd.read_sql(f"SELECT * FROM songkhla_weather_half_hourly_prev WHERE EXTRACT(YEAR FROM datetime) = {y}", conn)
        aadt= pd.read_sql(f"SELECT * FROM aadt_table WHERE source_year_be = {y+543}", conn)

    if not acc.empty:  acc.to_csv(outdir/"accident_events.csv.gz", index=False, compression="gzip")
    if not wea.empty:  wea.to_csv(outdir/"songkhla_weather_half_hourly_prev.csv.gz", index=False, compression="gzip")
    if not aadt.empty: aadt.to_csv(outdir/"aadt_table.csv.gz", index=False, compression="gzip")

def prune_raw_year(**context):
    y = target_year(context["logical_date"])
    hook = PostgresHook(PG_CONN_ID)
    hook.run(f"DELETE FROM songkhla_weather_half_hourly_prev WHERE EXTRACT(YEAR FROM datetime) = {y};", autocommit=True)
    hook.run("VACUUM (ANALYZE) songkhla_weather_half_hourly_prev;", autocommit=True)
    # Accident เก็บ 5 ปีย้อนหลัง
    cutoff = y - 5
    hook.run(f"DELETE FROM accident_events WHERE EXTRACT(YEAR FROM accident_date) <= {cutoff};", autocommit=True)
    hook.run("VACUUM (ANALYZE) accident_events;", autocommit=True)

# ===== DAG =====
with DAG(
    dag_id="yearly_clean_when_ready",
    start_date=datetime(2025, 1, 1),
    schedule_interval="30 8 * * *",
    catchup=False,
    tags=["yearly","clean","songkhla"],
) as dag:

    t_gate = ShortCircuitOperator(
        task_id="gate_all_sources_ready_for_prev_year",
        python_callable=gate_all_ready
    )

    run_env = "PYTHONPATH=/opt/airflow/dags"
    cd_data = f"cd {DATA_DIR}"

    t_export = PythonOperator(task_id="export_year_from_postgres",
                              python_callable=export_year_from_postgres)

    # หมายเหตุ: ชุด USE* ของคุณคาด format ไฟล์กลางแบบที่เราส่งออกไว้
    # (weather CSV: date,time,temperature_F,humidity_%,pressure_in,condition)
    # และขั้น aggregate รายวัน/รวมปี ดูรูปแบบในสคริปต์ที่คุณให้มา :contentReference[oaicite:3]{index=3} :contentReference[oaicite:4]{index=4}
    t_use1_aadt   = BashOperator(task_id="use1_clean_aadt",
        bash_command=f"{cd_data} && {run_env} python -m clean.USE1claenallcar")
    t_use1_acc    = BashOperator(task_id="use1_clean_accident",
        bash_command=f"{cd_data} && {run_env} python -m clean.USE1cleanser_acccar")
    t_use1_wea    = BashOperator(task_id="use1_clean_weather",
        bash_command=f"{cd_data} && {run_env} python -m clean.USE1cleanweather")
    t_use2_merge  = BashOperator(task_id="use2_merge_acc_weather",
        bash_command=f"{cd_data} && {run_env} python -m clean.USE2mergeaccandweather")
    t_use3_addcar = BashOperator(task_id="use3_add_car_data",
        bash_command=f"{cd_data} && {run_env} python -m clean.USE3addcardata")
    t_use4_final  = BashOperator(task_id="use4_final_daily",
        bash_command=f"{cd_data} && {run_env} python -m clean.USE4finaldata")

    t_load    = PythonOperator(task_id="load_final_all_years_into_postgres",
                               python_callable=load_final_all_years_into_postgres)
    t_archive = PythonOperator(task_id="archive_raw_year",
                               python_callable=archive_raw_year)
    t_prune   = PythonOperator(task_id="prune_raw_year",
                               python_callable=prune_raw_year)

    t_gate >> t_export >> [t_use1_aadt, t_use1_acc, t_use1_wea]
    [t_use1_aadt, t_use1_acc, t_use1_wea] >> t_use2_merge >> t_use3_addcar >> t_use4_final >> t_load >> t_archive >> t_prune
