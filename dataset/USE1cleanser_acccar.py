import pandas as pd
import numpy as np
from pathlib import Path

# CONFIG
IN_DIR   = Path(".")     # โฟลเดอร์ที่มี accidentYYYY.csv
OUT_DIR  = Path(".")     # โฟลเดอร์เอาท์พุต
YEARS    = [2020, 2021, 2022, 2023, 2024]   # ปีที่อยากประมวลผล
FILE_FMT = "accident{year}.csv"             # ชื่อไฟล์อินพุต
PROVINCE = "สงขลา"
ROUTE    = "4"           # รหัสสายทางที่ต้องการ


def normalize_date_mdy_or_serial(series):
    """
    แปลงเฉพาะค่าเป็น MM/DD/YYYY -> DD/MM/YYYY และรองรับ Excel serial
    ค่าที่ parse ไม่ได้/เป็นรูปแบบอื่น คงเดิม
    """
    s = series.astype(str).str.strip()
    dt_mdy = pd.to_datetime(s, format="%m/%d/%Y", errors="coerce")
    num = pd.to_numeric(s, errors="coerce")
    dt_serial = pd.to_datetime(num, unit="D", origin="1899-12-30", errors="coerce")
    dt = dt_mdy.combine_first(dt_serial)
    mask = dt.notna()
    s.loc[mask] = dt.loc[mask].dt.strftime("%d/%m/%Y")
    return s

def to_hhmm_ampm(series):
    """
    แปลงเวลาเป็น 12 ชั่วโมง HH:MM AM/PM
    รองรับ "H:M", "H:M:S" และ Excel fraction of day
    """
    s = series.astype(str).str.strip()
    num = pd.to_numeric(s, errors="coerce")
    t_num = pd.to_datetime(num, unit="D", origin="1899-12-30", errors="coerce")
    t_hms = pd.to_datetime(s, format="%H:%M:%S", errors="coerce")
    t_hm  = pd.to_datetime(s, format="%H:%M", errors="coerce")
    t = t_hms.combine_first(t_hm).combine_first(t_num)
    mask = t.notna()
    s.loc[mask] = t.loc[mask].dt.strftime("%I:%M %p")
    return s

def process_one_file(in_path: Path, out_dir: Path) -> Path | None:
    if not in_path.exists():
        print(f"ไม่พบไฟล์: {in_path.name} (ข้าม)")
        return None

    # อ่านไฟล์ (ลอง utf-8-sig ก่อน แล้วค่อย utf-8 / cp874)
    tried = []
    for enc in ["utf-8-sig", "utf-8", "cp874", "iso-8859-11"]:
        try:
            df = pd.read_csv(in_path, encoding=enc)
            break
        except Exception as e:
            tried.append((enc, str(e)))
            df = None
    if df is None:
        print(f"อ่านไฟล์ล้มเหลว: {in_path.name}\n  tried={tried}")
        return None

    # 1) แปลงวันที่/เวลา (ถ้ามีคอลัมน์)
    if 'วันที่เกิดเหตุ' in df.columns:
        df['วันที่เกิดเหตุ'] = normalize_date_mdy_or_serial(df['วันที่เกิดเหตุ'])
    if 'วันที่รายงาน' in df.columns:
        df['วันที่รายงาน'] = normalize_date_mdy_or_serial(df['วันที่รายงาน'])
    if 'เวลา' in df.columns:
        df['เวลา'] = to_hhmm_ampm(df['เวลา'])
    if 'เวลาที่รายงาน' in df.columns:
        df['เวลาที่รายงาน'] = to_hhmm_ampm(df['เวลาที่รายงาน'])

    # 2) ลบคอลัมน์ไม่จำเป็น (ถ้ามี)
    cols_to_drop = [
        'ACC_CODE','หน่วยงาน','สายทางหน่วยงาน','สายทาง','KM','รถคันที่1',
        'บริเวณที่เกิดเหตุ','มูลเหตุสันนิษฐาน','ลักษณะการเกิดเหตุ','สภาพอากาศ','รถและคนที่เกิดเหตุ','คนเดินเท้า',
        'ผู้เสียชีวิต','ผู้บาดเจ็บสาหัส','ผู้บาดเจ็บเล็กน้อย','รวมจำนวนผู้บาดเจ็บ',
        'วันที่รายงาน','เวลาที่รายงาน'
    ]
    df.drop(columns=[c for c in cols_to_drop if c in df.columns], inplace=True, errors="ignore")

    # 3) กรองจังหวัด/รหัสสายทาง
    if 'จังหวัด' in df.columns:
        df['จังหวัด'] = df['จังหวัด'].astype(str).str.strip()
        df = df[df['จังหวัด'] == PROVINCE]
    else:
        print("ไม่มีคอลัมน์ 'จังหวัด' — ไม่ได้กรองจังหวัด")

    if 'รหัสสายทาง' in df.columns:
        df['รหัสสายทาง'] = df['รหัสสายทาง'].astype(str).str.strip()
        df = df[df['รหัสสายทาง'] == ROUTE]
    else:
        print("ไม่มีคอลัมน์ 'รหัสสายทาง' — ไม่ได้กรองสายทาง")

    # 4) alias ชื่อคอลัมน์ประเภทรถ (บางไฟล์ใช้ชื่อไม่เหมือนกัน)
    alias = {
        'รถปิคอัพบรรทุก 4 ล้อ': 'รถปิคอัพบรรทุก4ล้อ',
        'รถยนต์นั่งส่วนบุคคล/รถยนต์นั่งสาธารณะ': 'รถยนต์นั่งส่วนบุคคล',
        'รถโดยสารมากกว่า 4 ล้อ': 'รถโดยสารมากกว่า4ล้อ',
        'รถบรรทุกมากกว่า 10 ล้อ (รถพ่วง)': 'รถบรรทุกมากกว่า10ล้อ',
        'รถบรรทุกมากกว่า 6 ล้อ ไม่เกิน 10 ล้อ': 'รถบรรทุกไม่เกิน10ล้อ',
    }
    df.rename(columns={k:v for k,v in alias.items() if k in df.columns}, inplace=True)

    # 5) รวมกลุ่มล้อ
    cols_lt4 = ['รถจักรยานยนต์','รถสามล้อเครื่อง']
    cols_eq4 = ['รถยนต์นั่งส่วนบุคคล','รถตู้','รถปิคอัพโดยสาร','รถปิคอัพบรรทุก4ล้อ','รถอีแต๋น','รถอื่นๆ']
    cols_gt4 = ['รถโดยสารมากกว่า4ล้อ','รถบรรทุก6ล้อ','รถบรรทุกไม่เกิน10ล้อ','รถบรรทุกมากกว่า10ล้อ']

    lt4_exist = [c for c in cols_lt4 if c in df.columns]
    eq4_exist = [c for c in cols_eq4 if c in df.columns]
    gt4_exist = [c for c in cols_gt4 if c in df.columns]

    df['น้อยกว่ารถ4ล้อ'] = df[lt4_exist].sum(axis=1, skipna=True) if lt4_exist else 0
    df['รถ4ล้อ']        = df[eq4_exist].sum(axis=1, skipna=True) if eq4_exist else 0
    df['มากกว่ารถ4ล้อ']  = df[gt4_exist].sum(axis=1, skipna=True) if gt4_exist else 0

    # ลบคอลัมน์ประเภทรถเดิมเพื่อความสะอาด
    df.drop(columns=lt4_exist + eq4_exist + gt4_exist, inplace=True, errors="ignore")

    # 6) ธงเหตุ = 1
    df['เกิดเหตุ'] = 1

    # 7) ปัดเวลาเป็นกริด 30 นาที + sync วันที่/เวลาให้ตรงกับผลปัด
    if {'วันที่เกิดเหตุ','เวลา'} <= set(df.columns):
        dt = pd.to_datetime(
            df['วันที่เกิดเหตุ'].astype(str).str.strip() + ' ' + df['เวลา'].astype(str).str.strip(),
            format='%d/%m/%Y %I:%M %p',
            errors='coerce'
        )
        df['__dt30__'] = dt.dt.round('30min')
        mask_ok = df['__dt30__'].notna()
        df.loc[mask_ok, 'วันที่เกิดเหตุ'] = df.loc[mask_ok, '__dt30__'].dt.strftime('%d/%m/%Y')
        df.loc[mask_ok, 'เวลา']         = df.loc[mask_ok, '__dt30__'].dt.strftime('%I:%M %p')
        df.drop(columns='__dt30__', inplace=True)
    else:
        print("ไม่มี 'วันที่เกิดเหตุ' หรือ 'เวลา' — ข้ามการปัดเวลา")

    # 8) เซฟ
    out_path = out_dir / (in_path.stem + "-4_songkhla.csv")
    df.to_csv(out_path, index=False, encoding="utf-8-sig")

    print(f"{in_path.name} -> {out_path.name} | rows={len(df)}")
    return out_path

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for y in YEARS:
        in_path = IN_DIR / FILE_FMT.format(year=y)
        process_one_file(in_path, OUT_DIR)

if __name__ == "__main__":
    main()
