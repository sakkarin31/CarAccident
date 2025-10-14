import pandas as pd
from airflow.providers.postgres.hooks.postgres import PostgresHook

# -----------------------------------
# 1. ตั้งค่าพื้นฐาน
# -----------------------------------
PG_CONN_ID = "postgres_default"
SOURCE_TABLE = "songkhla_daily_clean"   # ตารางต้นทาง (2020–2024)
TARGET_TABLE = "usage_daily_current"    # ตารางปลายทาง (เฉลี่ยรายวัน)

# -----------------------------------
# 2. โหลดข้อมูลจาก PostgreSQL
# -----------------------------------
hook = PostgresHook(postgres_conn_id=PG_CONN_ID)

query = f"""
    SELECT date, vehicles_lt_4, vehicles_eq_4, vehicles_gt_4
    FROM {SOURCE_TABLE}
    WHERE date BETWEEN '2020-01-01' AND '2024-12-31';
"""

df = hook.get_pandas_df(query)

print(f"✅ โหลดข้อมูลจากตาราง '{SOURCE_TABLE}' สำเร็จ: {len(df):,} แถว")

# -----------------------------------
# 3. เตรียมและจัดกลุ่มข้อมูล
# -----------------------------------
df['date'] = pd.to_datetime(df['date'])
df['month_day'] = df['date'].dt.strftime('%m-%d')

# คำนวณค่าเฉลี่ยข้ามปี
vehicle_cols = ['vehicles_lt_4', 'vehicles_eq_4', 'vehicles_gt_4']
avg_by_day = df.groupby('month_day')[vehicle_cols].mean().reset_index()

# สร้างคอลัมน์ date (ใช้ปี 2024 เป็นตัวแทน)
avg_by_day['date'] = pd.to_datetime('2024-' + avg_by_day['month_day'])
avg_by_day = avg_by_day.sort_values('date').reset_index(drop=True)

# ปัดเศษเป็นจำนวนเต็ม
avg_by_day['vehicles_lt_4'] = avg_by_day['vehicles_lt_4'].round().astype(int)
avg_by_day['vehicles_eq_4'] = avg_by_day['vehicles_eq_4'].round().astype(int)
avg_by_day['vehicles_gt_4'] = avg_by_day['vehicles_gt_4'].round().astype(int)

# คัดเฉพาะคอลัมน์ที่ต้องใช้
result_df = avg_by_day[['date', 'vehicles_lt_4', 'vehicles_eq_4', 'vehicles_gt_4']].copy()
result_df['date'] = result_df['date'].dt.strftime('%Y-%m-%d')

print("✅ ตัวอย่างข้อมูลเฉลี่ยรายวัน:")
print(result_df.head())

# -----------------------------------
# 4. บันทึกลง PostgreSQL
# -----------------------------------
conn = hook.get_conn()
cursor = conn.cursor()

# สร้างตารางเป้าหมายถ้ายังไม่มี
cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS {TARGET_TABLE} (
        date DATE PRIMARY KEY,
        vehicles_lt_4 INTEGER,
        vehicles_eq_4 INTEGER,
        vehicles_gt_4 INTEGER
    );
""")

# ลบข้อมูลเก่าออกก่อน
cursor.execute(f"DELETE FROM {TARGET_TABLE};")

# แปลงข้อมูลเป็น tuple สำหรับ insert
records = [tuple(row) for row in result_df[['date', 'vehicles_lt_4', 'vehicles_eq_4', 'vehicles_gt_4']].values]

# บันทึกข้อมูลใหม่
cursor.executemany(f"""
    INSERT INTO {TARGET_TABLE} (date, vehicles_lt_4, vehicles_eq_4, vehicles_gt_4)
    VALUES (%s, %s, %s, %s);
""", records)

conn.commit()
cursor.close()
conn.close()

print(f"🎯 บันทึกค่าเฉลี่ยรายวันสำเร็จ! → ตาราง '{TARGET_TABLE}' ({len(result_df)} วัน)")
