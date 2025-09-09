import pandas as pd

df = pd.read_csv("Alldataaccident_fixed.csv")

# ลบคอลัมน์เดียว
df = df.drop(columns=["Unnamed: 39"])

# ลบหลายคอลัมน์
df = df.drop(columns=["วันที่รายงาน","เวลาที่รายงาน","ACC_CODE","หน่วยงาน","สายทาง","KM" ])

# บันทึกกลับ
df.to_csv("Alldataaccident_clean.csv", index=False, encoding="utf-8-sig")