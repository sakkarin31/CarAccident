# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import re
from pathlib import Path
from typing import List

# CONFIG
YEARS = [63,64,65,66,67]        # ปรับปีได้
IN_PATTERN  = "aadt-{year}.csv"
OUT_PATTERN = "car-count{year}.csv"
BASE_DIR = Path(".")

# HELPERS
def read_csv_thai(path: Path) -> pd.DataFrame:
    """อ่าน CSV รองรับไทย: ลอง utf-8, utf-8-sig, cp874 ตามลำดับ"""
    for enc in (None, "utf-8-sig", "cp874"):
        try:
            return pd.read_csv(path, encoding=enc) if enc else pd.read_csv(path)
        except UnicodeDecodeError:
            continue
    # fallback
    return pd.read_csv(path, encoding="cp874", engine="python")

def normalize_name(s: str) -> str:
    """ปรับชื่อคอลัมน์ให้เปรียบเทียบง่าย (เล็กหมด ตัดช่องว่าง/สัญลักษณ์)"""
    if not isinstance(s, str):
        s = str(s)
    s = s.strip().lower()
    s = re.sub(r"[ \t\n\r\f\v\u200b\u00a0]+", "", s)  # ตัด space, NBSP, ZWSP
    s = re.sub(r"[,:;.%()\-]", "", s)
    return s

def pick_col(df: pd.DataFrame, candidates: List[str]) -> List[str]:
    """
    เลือกคอลัมน์ที่ตรงกับคำหลัก (แบบ normalize แล้ว) คืนชื่อคอลัมน์จริงใน df
    """
    norm_map = {normalize_name(c): c for c in df.columns}
    matches = []
    for real_norm, real_name in norm_map.items():
        for cand in candidates:
            cn = normalize_name(cand)
            if cn == real_norm or cn in real_norm or real_norm in cn:
                matches.append(real_name)
                break
    # ไม่ซ้ำ รักษาลำดับ
    out, seen = [], set()
    for c in matches:
        if c not in seen:
            out.append(c); seen.add(c)
    return out

def to_numeric_safe(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    """แปลงคอลัมน์เป็นตัวเลข: ตัด comma/ช่องว่าง, NaN->0, เป็น int64"""
    for c in cols:
        if c not in df.columns:
            continue
        df[c] = (
            df[c].astype(str)
                 .str.replace(",", "", regex=False)
                 .str.replace(" ", "", regex=False)
                 .str.replace("\u200b", "", regex=False)
        )
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0).astype(np.int64)
    return df

def drop_unused(df: pd.DataFrame) -> pd.DataFrame:
    """ลบคอลัมน์ที่ไม่ใช้ ถ้ามีก็ลบ ไม่มีก็ข้าม"""
    to_drop_patterns = [
        ",ตอนควบคุม", "ตอนควบคุม",
        "ชื่อสายทาง",
        "จุดสำรวจ",
        "รวม",                 # รวม (total) เดิม
        "%ของยานยนต์หนัก", "% ของยานยนต์หนัก",
        "แขวงทางหลวง",
    ]
    cols_to_drop = pick_col(df, to_drop_patterns)
    return df.drop(columns=cols_to_drop, errors="ignore")

def bucketize(df: pd.DataFrame) -> pd.DataFrame:
    """
    สร้างคอลัมน์ผลลัพธ์:
    - ทางหลวงสาย (บังคับให้เป็น 4 หลังกรอง)
    - รถน้อยกว่า4ล้อ
    - รถ4ล้อ
    - รถมากกว่า4ล้อ
    + เพิ่มแถว 'รวม' ท้ายตาราง
    """
    # ระบุคอลัมน์หลัก
    col_province = pick_col(df, ["จังหวัด"])
    col_highway  = pick_col(df, ["ทางหลวงสาย", "สายทาง", "ชื่อสายทาง"])
    if not col_highway:
        raise ValueError("ไม่พบคอลัมน์ 'ทางหลวงสาย' หรือเทียบเท่า")

    highway_col = col_highway[0]

    # จัดกลุ่มคีย์เวิร์ดของประเภทรถ (ยืดหยุ่นกับชื่อคอลัมน์)
    less4_candidates = [
        "จักรยาน 2 ล้อ", "จักรยาน2ล้อ", "จักรยาน 3 ล้อ", "จักรยาน3ล้อ",
        "สามล้อเครื่องและจักรยานยนต์", "จักรยานยนต์", "มอเตอร์ไซค์"
    ]
    four_candidates = [
        "รถยนต์นั่ง (ไม่เกิน 7 คน)", "รถยนต์นั่ง (เกิน 7 คน)", "รถยนต์นั่ง",
        "รถโดยสารขนาดเล็ก", "รถบรรทุกขนาดเล็ก (4 ล้อ)", "4ล้อ"
    ]
    more4_candidates = [
        "รถโดยสารขนาดกลาง", "รถโดยสารขนาดใหญ่",
        "รถบรรทุกขนาด 2 เพลา", "รถบรรทุกขนาด2เพลา", "6ล้อ",
        "รถบรรทุกขนาด 3 เพลา", "รถบรรทุกขนาด3เพลา", "10ล้อ",
        "รถบรรทุกพ่วง", "รถบรรทุกกึ่งพ่วง", "มากกว่า3เพลา"
    ]

    less4_cols = pick_col(df, less4_candidates)
    four_cols  = pick_col(df, four_candidates)
    more4_cols = pick_col(df, more4_candidates)

    # ตัวเลขให้เรียบร้อย
    num_cols = less4_cols + four_cols + more4_cols
    df = to_numeric_safe(df, num_cols)

    # กรองจังหวัดสงขลา (ถ้ามีคอลัมน์จังหวัด)
    if col_province:
        prov_col = col_province[0]
        df = df[df[prov_col].astype(str).str.contains("สงขลา", na=False)]

    # กรองเฉพาะทางหลวงสาย 4
    def is_four(x):
        try:
            # เผื่อคอลัมน์เป็นสตริงมีข้อความอื่น ให้แยกตัวแรก
            return int(str(x).strip().split()[0]) == 4
        except Exception:
            return False
    df = df[df[highway_col].apply(is_four)]

    # สร้างผลลัพธ์
    df_out = pd.DataFrame()
    df_out["ทางหลวงสาย"]     = 4  # normalized เป็นเลข 4
    df_out["vehicles_lt_4_wheels"] = df[less4_cols].sum(axis=1) if less4_cols else 0
    df_out["vehicles_4_wheels"]         = df[four_cols].sum(axis=1) if four_cols else 0
    df_out["vehicles_gt_4_wheels"]   = df[more4_cols].sum(axis=1) if more4_cols else 0

    # เพิ่มแถวรวม
    total_row = {
        "ทางหลวงสาย": "all",
        "vehicles_lt_4_wheels": int(df_out["vehicles_lt_4_wheels"].sum()),
        "vehicles_4_wheels": int(df_out["vehicles_4_wheels"].sum()),
        "vehicles_gt_4_wheels": int(df_out["vehicles_gt_4_wheels"].sum()),
    }
    df_out = pd.concat([df_out, pd.DataFrame([total_row])], ignore_index=True)
    return df_out

# MAIN
if __name__ == "__main__":
    for y in YEARS:
        in_path = BASE_DIR / IN_PATTERN.format(year=y)
        try:
            raw = read_csv_thai(in_path)
        except FileNotFoundError:
            print(f"[ปี {y}] ไม่พบไฟล์: {in_path}")
            continue

        # ลบคอลัมน์ที่ไม่ใช้ก่อน
        raw = drop_unused(raw)

        try:
            result = bucketize(raw)
        except Exception as e:
            print(f"[ปี {y}] เกิดข้อผิดพลาด: {e}")
            continue

        out_path = BASE_DIR / OUT_PATTERN.format(year=y)
        result.to_csv(out_path, index=False, encoding="utf-8-sig")
        print(f"[ปี {y}] เขียนไฟล์แล้ว -> {out_path} (rows={len(result)})")
