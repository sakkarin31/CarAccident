import pandas as pd
from datetime import datetime, timedelta

# โหลดไฟล์ต้นฉบับ
df = pd.read_csv("accident2025.csv")

# --- ฟังก์ชันแปลงวันที่/เวลา ---
def excel_serial_to_date(serial):
    return (datetime(1899, 12, 30) + timedelta(days=int(serial))).strftime('%d/%m/%Y')

def time_to_hhmm_ampm(t):
    t = str(t).strip()
    if ':' in t:
        parts = t.split(':')
        if len(parts) == 2:
            t24 = f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:00"
        elif len(parts) == 3:
            t24 = f"{parts[0].zfill(2)}:{parts[1].zfill(2)}:{parts[2].zfill(2)}"
        else:
            t24 = "00:00:00"
    else:
        t24 = "00:00:00"
    return datetime.strptime(t24, "%H:%M:%S").strftime("%I:%M %p")

# --- แปลง 'วันที่' เป็น dd/mm/yyyy และ 'เวลา' เป็น 12 ชั่วโมง AM/PM ---
df['วันที่เกิดเหตุ'] = df['วันที่เกิดเหตุ'].apply(excel_serial_to_date)
df['วันที่รายงาน'] = df['วันที่รายงาน'].apply(excel_serial_to_date)
df['เวลา'] = df['เวลา'].apply(time_to_hhmm_ampm)
df['เวลาที่รายงาน'] = df['เวลาที่รายงาน'].apply(time_to_hhmm_ampm)

# --- ลบคอลัมน์ไม่จำเป็น ---
cols_to_drop = [
    'ACC_CODE','หน่วยงาน','สายทางหน่วยงาน','สายทาง','KM','รถคันที่1',
    'บริเวณที่เกิดเหตุ','มูลเหตุสันนิษฐาน','ลักษณะการเกิดเหตุ','สภาพอากาศ',
    'LATITUDE','LONGITUDE','รถและคนที่เกิดเหตุ','คนเดินเท้า',
    'ผู้เสียชีวิต','ผู้บาดเจ็บสาหัส','ผู้บาดเจ็บเล็กน้อย','รวมจำนวนผู้บาดเจ็บ','วันที่รายงาน','เวลาที่รายงาน'
]
df.drop(columns=[c for c in cols_to_drop if c in df.columns], inplace=True)

# --- กรองเฉพาะจังหวัด สงขลา ---
if 'จังหวัด' in df.columns:
    df = df[df['จังหวัด'] == 'สงขลา']

# ---------------- จัดกลุ่มจำนวนล้อ ----------------
cols_lt4 = ['รถจักรยานยนต์','รถสามล้อเครื่อง']
cols_eq4 = ['รถยนต์นั่งส่วนบุคคล','รถตู้','รถปิคอัพโดยสาร','รถปิคอัพบรรทุก4ล้อ','รถอีแต๋น','รถอื่นๆ']
cols_gt4 = ['รถโดยสารมากกว่า4ล้อ','รถบรรทุก6ล้อ','รถบรรทุกไม่เกิน10ล้อ','รถบรรทุกมากกว่า10ล้อ']

lt4_exist = [c for c in cols_lt4 if c in df.columns]
eq4_exist = [c for c in cols_eq4 if c in df.columns]
gt4_exist = [c for c in cols_gt4 if c in df.columns]

df['น้อยกว่ารถ4ล้อ'] = df[lt4_exist].sum(axis=1, skipna=True) if lt4_exist else 0
df['รถ4ล้อ']       = df[eq4_exist].sum(axis=1, skipna=True) if eq4_exist else 0
df['มากกว่ารถ4ล้อ'] = df[gt4_exist].sum(axis=1, skipna=True) if gt4_exist else 0

# ---------------- ลบคอลัมน์ประเภทรถเดิม ----------------
df.drop(columns=lt4_exist + eq4_exist + gt4_exist, inplace=True)

# --- เพิ่มคอลัมน์ 'เกิดเหตุ' = 1 ทุกแถว ---
df['เกิดเหตุ'] = 1

# บันทึกเป็นไฟล์ใหม่
df.to_csv("accident2025_songkhla.csv", index=False)

print("เสร็จสิ้น: accident2025_songkhla_separated.csv — วันที่และเวลาแยกเป็นคนละคอลัมน์ และกรองเฉพาะจังหวัดสงขลาเรียบร้อย")
