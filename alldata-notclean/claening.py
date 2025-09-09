import pandas as pd

df = pd.read_csv("Alldataaccident.csv")

# รวมคอลัมน์วันที่ + เวลา
df["วันเวลาเกิด"] = pd.to_datetime(
    df["วันที่เกิดเหตุ"].astype(str).str.strip() + " " + df["เวลา"].astype(str).str.strip(),
    errors="coerce",
    dayfirst=True   #บังคับให้อ่านแบบ d/m/yyyy
)

# บันทึกกลับ
df.to_csv("Alldataaccident_fixed2.csv", index=False, encoding="utf-8-sig")
