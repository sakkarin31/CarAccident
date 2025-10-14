import pandas as pd
import numpy as np
import joblib
from tensorflow.keras.models import load_model
from datetime import datetime, timedelta
from airflow.providers.postgres.hooks.postgres import PostgresHook
import os

# -----------------------------
# 1. Load model and scalers
# -----------------------------
MODEL_DIR = "/opt/airflow/model"

print(f"🔍 Checking files in {MODEL_DIR}:")
print(os.listdir(MODEL_DIR))
print("📁 outputs folder contents:", os.listdir(os.path.join(MODEL_DIR, "outputs")))

model = load_model(os.path.join(MODEL_DIR, "outputs", "lstm_multivariate_3outputs.keras"))
scaler_X = joblib.load(os.path.join(MODEL_DIR, "outputs", "feature_scaler.pkl"))
scaler_y = joblib.load(os.path.join(MODEL_DIR, "outputs", "target_scaler.pkl"))

# -----------------------------
# 2. Configuration
# -----------------------------
n_steps = 30
forecast_days = 30

# ใช้ชื่อ column ให้ตรงกับตอน training เดิม
features = ['temperature_F', 'humidity_%', 'wind_speed_kmh', 'pressure_in']
targets = ['temperature_F', 'humidity_%', 'pressure_in']

# -----------------------------
# 3. Fetch last 30 days of weather data
# -----------------------------
pg_hook = PostgresHook(postgres_conn_id='postgres_default')
df_recent = pg_hook.get_pandas_df(sql="""
    SELECT 
        date,
        avg_temperatureF AS temperature_f,
        avg_humidity_pct AS humidity_pct,
        avg_wind_speed_kmh AS wind_speed_kmh,
        avg_pressure_in AS pressure_in
    FROM last30weather_db
    WHERE avg_temperatureF IS NOT NULL
      AND avg_humidity_pct IS NOT NULL
      AND avg_wind_speed_kmh IS NOT NULL
      AND avg_pressure_in IS NOT NULL
    ORDER BY date DESC
    LIMIT 30
""")

if len(df_recent) < n_steps:
    raise ValueError(f"Need at least {n_steps} days of data, but got {len(df_recent)}")

df_recent = df_recent.sort_values('date').reset_index(drop=True)
print(f"✅ Loaded {len(df_recent)} rows of recent weather data")

# -----------------------------
# 4. Prepare scaled input (แก้ชื่อ column ให้ตรงกับ scaler)
# -----------------------------
X_context = df_recent.rename(columns={
    'temperature_f': 'temperature_F',
    'humidity_pct': 'humidity_%'
})[features]

# ✅ ตอนนี้ชื่อ column ตรงกับ feature_scaler แล้ว
X_scaled = scaler_X.transform(X_context)

# -----------------------------
# 5. Recursive forecasting for 30 days
# -----------------------------
predictions = []
current_sequence = X_scaled.copy()

for day in range(forecast_days):
    X_input = current_sequence[-n_steps:].reshape(1, n_steps, len(features))
    y_pred_scaled = model.predict(X_input, verbose=0)
    y_pred = scaler_y.inverse_transform(y_pred_scaled).flatten()
    predictions.append(y_pred)

    # ใช้ historical average wind speed (ไม่ทำนาย)
    avg_wind = np.mean(X_context['wind_speed_kmh'].values)
    next_input = [
        y_pred[0],           # temperature_F
        y_pred[1],           # humidity_%
        avg_wind,            # wind_speed_kmh
        y_pred[2]            # pressure_in
    ]
    next_input_scaled = scaler_X.transform(np.array([next_input]))
    current_sequence = np.vstack([current_sequence[1:], next_input_scaled])

print(f"✅ Forecast completed for {len(predictions)} days")

# -----------------------------
# 6. Create forecast DataFrame
# -----------------------------
start_date = datetime.today().date() + timedelta(days=1)
forecast_dates = [start_date + timedelta(days=i) for i in range(forecast_days)]

pred_array = np.array(predictions)
result_df = pd.DataFrame({
    'date': forecast_dates,
    'predicted_temperature_f': pred_array[:, 0],
    'predicted_humidity_pct': pred_array[:, 1],
    'predicted_pressure_in': pred_array[:, 2]
})

# เตรียมข้อมูลสำหรับ insert ลง DB
result_df['pct'] = result_df['predicted_temperature_f']
result_df['road'] = 4
result_df['date_str'] = result_df['date'].astype(str)
db_ready_df = result_df[['road', 'date_str', 'pct']].rename(columns={'date_str': 'date'})

# -----------------------------
# 7. Save to PostgreSQL (forecast_weather)
# -----------------------------
TARGET_TABLE = "forecast_weather"

conn = pg_hook.get_conn()
cursor = conn.cursor()

# ✅ Create table if not exists
cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (
        date DATE PRIMARY KEY,
        predicted_temperature_f DOUBLE PRECISION,
        predicted_humidity_pct DOUBLE PRECISION,
        predicted_pressure_in DOUBLE PRECISION
    );
""")

# ✅ เตรียมข้อมูลสำหรับ insert
records = [
    (
        row['date'],
        row['predicted_temperature_f'],
        row['predicted_humidity_pct'],
        row['predicted_pressure_in']
    )
    for _, row in result_df.iterrows()
]

# ✅ Insert or update
cursor.executemany(f"""
    INSERT INTO {TARGET_TABLE} (
        date,
        predicted_temperature_f,
        predicted_humidity_pct,
        predicted_pressure_in
    ) VALUES (%s, %s, %s, %s)
    ON CONFLICT (date)
    DO UPDATE SET
        predicted_temperature_f = EXCLUDED.predicted_temperature_f,
        predicted_humidity_pct = EXCLUDED.predicted_humidity_pct,
        predicted_pressure_in = EXCLUDED.predicted_pressure_in;
""", records)

conn.commit()
cursor.close()
conn.close()

print("✅ Forecast saved to PostgreSQL table: forecast_weather!")
