import pandas as pd

# โหลดไฟล์
df = pd.read_csv("Alldataaccident.csv")

# รวมคอลัมน์วันที่และเวลา
df["datetime"] = pd.to_datetime(
    df["วันที่เกิดเหตุ"].astype(str) + " " + df["เวลา"].astype(str),
    errors="coerce"
)

# เอาค่าที่รวมแล้วไปแทนคอลัมน์ "เวลาเกิด"
df["วันเวลาเกิด"] = df["datetime"]

# ลบคอลัมน์ datetime ชั่วคราวออก (ถ้าไม่อยากเก็บไว้)
df = df.drop(columns=["datetime"])

# บันทึกกลับเป็นไฟล์ใหม่
df.to_csv("Alldataaccident_fixed.csv", index=False)

print(df[["วันที่เกิดเหตุ","เวลา","วันเวลาเกิด"]].head(20))
