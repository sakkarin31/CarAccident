import pandas as pd
import numpy as np
import joblib
from datetime import datetime
from airflow.providers.postgres.hooks.postgres import PostgresHook

# -----------------------------
# 1. ตั้งค่าพารามิเตอร์
# -----------------------------
CONN_ID = "postgres_default"
MODEL_PATH = "/opt/airflow/model/XGBoost_Round_2_Window=3.pkl"
SCALER_PATH = "/opt/airflow/model/Scaler_Round_2_Window=3.pkl"
PREDICTED_WEATHER_TABLE = "forecast_weather"
USAGE_TABLE = "usage_daily_current"
RESULT_TABLE = "db_result_model"

# -----------------------------
# 2. วันหยุดราชการไทย
# -----------------------------
THAI_HOLIDAYS = {
    "2025-01-01", "2025-04-13", "2025-04-14", "2025-04-15",
    "2025-05-01", "2025-05-05", "2025-07-25", "2025-08-12",
    "2025-10-13", "2025-10-23", "2025-12-05", "2025-12-10", "2025-12-31",
    "2026-01-01", "2026-04-13", "2026-04-14", "2026-04-15",
}

# -----------------------------
# 3. เชื่อมต่อฐานข้อมูล
# -----------------------------
hook = PostgresHook(postgres_conn_id=CONN_ID)
conn = hook.get_conn()
cursor = conn.cursor()

# -----------------------------
# 4. สร้างช่วงวันที่ 30 วันถัดไป
# -----------------------------
start_date = datetime.today().date()
date_range = pd.date_range(start=start_date, periods=30, freq='D')
df_dates = pd.DataFrame({"date": date_range})

# -----------------------------
# 5. ดึงข้อมูลอากาศ 30 วัน
# -----------------------------
cursor.execute(f"""
    SELECT date, 
           predicted_temperature_f AS temperature_F,
           predicted_humidity_pct AS humidity_pct,
           predicted_pressure_in AS pressure_in
    FROM {PREDICTED_WEATHER_TABLE}
    WHERE date >= %s
    ORDER BY date ASC;
""", (start_date,))

weather_df = pd.DataFrame(
    cursor.fetchall(),
    columns=["date", "temperature_F", "humidity_pct", "pressure_in"]
)
weather_df["date"] = pd.to_datetime(weather_df["date"])
# ✅ เปลี่ยนชื่อคอลัมน์ให้ตรงกับตอนเทรน!
weather_df.rename(columns={"humidity_pct": "humidity_%"}, inplace=True)

# -----------------------------
# 6. ดึงข้อมูลการใช้รถ 30 วัน
# -----------------------------
cursor.execute(f"""
    SELECT date, 
           vehicles_lt_4 AS vehicles_lt_4_wheels,
           vehicles_eq_4 AS vehicles_4_wheels,
           vehicles_gt_4 AS vehicles_gt_4_wheels
    FROM {USAGE_TABLE}
    WHERE date >= %s
    ORDER BY date ASC;
""", (start_date,))

usage_df = pd.DataFrame(
    cursor.fetchall(),
    columns=["date", "vehicles_lt_4_wheels", "vehicles_4_wheels", "vehicles_gt_4_wheels"]
)
usage_df["date"] = pd.to_datetime(usage_df["date"])

# -----------------------------
# 7. รวมข้อมูลเข้าด้วยกัน
# -----------------------------
df = pd.merge(df_dates, weather_df, on="date", how="left")
df = pd.merge(df, usage_df, on="date", how="left")

# -----------------------------
# 8. สร้างฟีเจอร์วัน เวลา และวันหยุด
# -----------------------------
df["day_of_week"] = df["date"].dt.dayofweek + 1
df["is_weekend"] = df["day_of_week"].isin([6, 7]).astype(int)
df["year"] = df["date"].dt.year
df["is_holiday"] = df["date"].dt.strftime("%Y-%m-%d").isin(THAI_HOLIDAYS).astype(int)

# -----------------------------
# 9. สร้างฟีเจอร์ lag & rolling (window=3)
# -----------------------------
np.random.seed(42)
df["target_dummy"] = np.random.randint(0, 2, len(df))
df["lag_3"] = df["target_dummy"].shift(3)
df["roll_mean_3"] = df["target_dummy"].rolling(window=3, min_periods=1).mean()
df["roll_std_3"] = df["target_dummy"].rolling(window=3, min_periods=1).std()
df = df.fillna(0)  # แทน NaN ด้วย 0

# -----------------------------
# 10. โหลดโมเดลและสเกลเลอร์ LightGBM
# -----------------------------
model = joblib.load(MODEL_PATH)
scaler = joblib.load(SCALER_PATH)

# -----------------------------
# 11. เตรียม feature สำหรับ predict
# -----------------------------
X_cols = [
    "temperature_F", "humidity_%", "pressure_in",  # ✅ ใช้ humidity_% แล้ว
    "vehicles_lt_4_wheels", "vehicles_4_wheels", "vehicles_gt_4_wheels",
    "day_of_week", "is_weekend", "is_holiday", "year",
    "lag_3", "roll_mean_3", "roll_std_3"
]

# ตรวจสอบ column
for col in X_cols:
    if col not in df.columns:
        raise ValueError(f"Missing column in df: {col}")

X_scaled = scaler.transform(df[X_cols])
df["pred_prob"] = model.predict_proba(X_scaled)[:, 1]
df["pred_class"] = (df["pred_prob"] > 0.5).astype(int)

# -----------------------------
# 12. สร้างตารางผลลัพธ์ถ้ายังไม่มี
# -----------------------------
# -----------------------------
# 12. สร้างตารางผลลัพธ์ถ้ายังไม่มี (โครงสร้างใหม่)
# -----------------------------
cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS {RESULT_TABLE} (
        id SERIAL PRIMARY KEY,
        road INTEGER NOT NULL DEFAULT 4,
        date DATE NOT NULL,
        predicted_probability DOUBLE PRECISION NOT NULL
    );
""")

# -----------------------------
# 13. บันทึกผลลัพธ์ (เพิ่ม road=4)
# -----------------------------
records = [
    (4, row["date"].date(), row["pred_prob"])  # road=4, date, prob
    for _, row in df.iterrows()
]

cursor.executemany(f"""
    INSERT INTO {RESULT_TABLE} (road, date, predicted_probability)
    VALUES (%s, %s, %s)
    ON CONFLICT (id) DO NOTHING;  -- ไม่น่าเกิด conflict เพราะ id auto-increment
""", records)

conn.commit()
cursor.close()
conn.close()

print("✅ LightGBM prediction complete for 30 days and saved to table:", RESULT_TABLE)