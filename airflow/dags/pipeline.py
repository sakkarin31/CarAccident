# dags/yearly_clean_when_ready.py
from __future__ import annotations
from datetime import datetime, timedelta
import calendar
from pathlib import Path
import pandas as pd
import os
import subprocess
import logging
from airflow.utils.trigger_rule import TriggerRule
from airflow import DAG
from airflow.operators.python import PythonOperator, BranchPythonOperator, ShortCircuitOperator
from airflow.operators.bash import BashOperator
from airflow.operators.dummy import DummyOperator
from airflow.utils.task_group import TaskGroup
from airflow.providers.postgres.hooks.postgres import PostgresHook

# ===== CONFIG =====
DATA_DIR = Path("/opt/airflow/data")
PG_CONN_ID = "postgres_default"
TARGET_TABLE = "songkhla_daily_clean"
DAGS_FOLDER = os.path.dirname(__file__)
TASKS_FOLDER = os.path.join(DAGS_FOLDER, "load_data")
SPARK_FOLDER = "/opt/airflow/spark/application/spark_jobs"

# ===== LOGGER =====
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# -----------------------------
# Helper functions
# -----------------------------
def run_script(script_path: str):
    print(f"🚀 Running script: {script_path}")
    result = subprocess.run(["python", script_path], capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError(f"❌ Script failed: {script_path}")
    print(f"✅ Completed: {os.path.basename(script_path)}")

def run_python_script(script_path: str, **context):
    run_script(script_path)

def run_spark_job(script_path: str, **context):
    print(f"🚀 Running Spark job: {script_path}")
    result = subprocess.run(["spark-submit", script_path], capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError(f"❌ Spark job failed: {script_path}")
    print(f"✅ Spark job completed: {os.path.basename(script_path)}")

# -----------------------------
# Default args
# -----------------------------
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=2),
}

# -----------------------------
# Helper Logic
# -----------------------------
def target_year(execution_date: datetime) -> int:
    return execution_date.year - 1

def is_aadt_ready(year: int, hook: PostgresHook) -> bool:
    n = hook.get_first(f"SELECT COUNT(*) FROM aadt_table WHERE source_year_be = {year+543}")[0]
    return (n or 0) > 0

def is_accident_ready(year: int, hook: PostgresHook) -> bool:
    months = hook.get_first(
        f"SELECT COUNT(DISTINCT EXTRACT(MONTH FROM accident_date)) "
        f"FROM accident_events WHERE EXTRACT(YEAR FROM accident_date) = {year}"
    )[0]
    return (months or 0) >= 12

def is_weather_ready(year: int, hook: PostgresHook) -> bool:
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
    logger.info(f"[readiness] year={y} aadt={aadt_ok} accident={acc_ok} weather={wea_ok}")
    return aadt_ok and acc_ok and wea_ok

def check_update(**context):
    y = target_year(context["logical_date"])
    hook = PostgresHook(PG_CONN_ID)

    # นับแถวปีล่าสุด
    result = hook.get_first(
        f"SELECT COUNT(*) FROM {TARGET_TABLE} WHERE EXTRACT(YEAR FROM date) = {y}"
    )
    n_rows = result[0] if result else 0

    print(f"[check_update] year={y}, rows_in_table={n_rows}")

    if n_rows > 0:
        print("✅ Data for last year exists. Run forecast pipeline.")
        # return task_id แบบเต็ม
        return "forecast_pipeline.run_last30_weather"
    else:
        print("⏳ Data for last year not found. Run cleaning pipeline first.")
        return "clean_pipeline.gate_all_sources_ready_for_prev_year"




# -----------------------------
# Load FINAL → Postgres
# -----------------------------
def create_target_table_if_not_exists():
    hook = PostgresHook(PG_CONN_ID)
    hook.run(f"""
    CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (
        date date PRIMARY KEY,
        temperature_f double precision,
        humidity_pct double precision,
        pressure_in double precision,
        accidents integer,
        vehicles_lt_4 integer,
        vehicles_eq_4 integer,
        vehicles_gt_4 integer,
        day_of_week smallint,
        is_weekend boolean,
        is_holiday boolean
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
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date

    cols = ["date","temperature_f","humidity_pct","pressure_in",
            "accidents","vehicles_lt_4","vehicles_eq_4","vehicles_gt_4",
            "day_of_week","is_weekend","is_holiday"]
    df = df[[c for c in cols if c in df.columns]]

    int_cols = [c for c in ["accidents","vehicles_lt_4","vehicles_eq_4","vehicles_gt_4","day_of_week"] if c in df.columns]
    for c in int_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).round().astype(int)

    for b in ["is_weekend","is_holiday"]:
        if b in df.columns:
            df[b] = df[b].map({1: True, 0: False, "1": True, "0": False, True: True, False: False}).fillna(False).astype(bool)

    for f in ["temperature_f","humidity_pct","pressure_in"]:
        if f in df.columns:
            df[f] = pd.to_numeric(df[f], errors="coerce")

    df = df.dropna(subset=["date"])
    tmp = csv_path.parent / "to_load_all.csv"
    df.to_csv(tmp, index=False)

    stage = f"_load_{TARGET_TABLE}"
    col_list = ", ".join(df.columns)
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

# -----------------------------
# Archive / Prune
# -----------------------------
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
    cutoff = y - 5
    hook.run(f"DELETE FROM accident_events WHERE EXTRACT(YEAR FROM accident_date) <= {cutoff};", autocommit=True)
    hook.run("VACUUM (ANALYZE) accident_events;", autocommit=True)

# ===========================================================
#                       DAG DEFINITION
# ===========================================================
with DAG(
    dag_id="ready",
    description="Clean & forecast data when new year data available",
    default_args=default_args,
    start_date=datetime(2025, 1, 1),
    schedule_interval="30 8 * * *",
    catchup=False,
    tags=["yearly", "clean", "songkhla"],
) as dag:

    # ---------- START TASK ----------
    start = DummyOperator(task_id="start")

    # ---------- BRANCH CHECK ----------
    t_check_update = BranchPythonOperator(
        task_id="check_update",
        python_callable=check_update,
        provide_context=True,
    )

    # ---------- CLEAN PIPELINE ----------
    with TaskGroup("clean_pipeline") as clean_pipeline:
        t_gate = ShortCircuitOperator(
            task_id="gate_all_sources_ready_for_prev_year",
            python_callable=gate_all_ready
        )

        run_env = "PYTHONPATH=/opt/airflow/dags"
        cd_data = f"cd {DATA_DIR}"

        t_export = PythonOperator(task_id="export_year_from_postgres",
                                  python_callable=lambda: print("Exporting data..."))

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
        t_archive = PythonOperator(task_id="archive_raw_year", python_callable=archive_raw_year)
        t_prune   = PythonOperator(task_id="prune_raw_year", python_callable=prune_raw_year)

        t_gate >> t_export >> [t_use1_aadt, t_use1_acc, t_use1_wea]
        [t_use1_aadt, t_use1_acc, t_use1_wea] >> t_use2_merge >> t_use3_addcar >> t_use4_final >> t_load >> t_archive >> t_prune

    # ---------- FORECAST PIPELINE ----------
    with TaskGroup("forecast_pipeline") as forecast_pipeline:
        run_last30_weather = PythonOperator(
            task_id="run_last30_weather",
            python_callable=run_python_script,
            op_args=[os.path.join(DAGS_FOLDER, "load_data/last30_weather.py")],
        )

        run_usage_current = PythonOperator(
            task_id="run_usage_current",
            python_callable=run_python_script,
            op_args=[os.path.join(DAGS_FOLDER, "load_data/usage_current.py")],
        )

        run_lstm_model = PythonOperator(
            task_id="run_lstm_model",
            python_callable=run_python_script,
            op_args=[os.path.join(SPARK_FOLDER, "lstm.py")],
        )

        run_predict_acc = PythonOperator(
            task_id="run_predict_acc",
            python_callable=run_python_script,
            op_args=[os.path.join(SPARK_FOLDER, "predict_acc.py")],
        )

        run_last30_weather >> run_usage_current >> run_lstm_model >> run_predict_acc

    # ---------- END TASK ----------
    end_task = DummyOperator(
        task_id="end_task",
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS 
    )

    # ---------- DAG FLOW ----------
    start >> t_check_update
    t_check_update >> clean_pipeline >> end_task
    t_check_update >> forecast_pipeline >> end_task


