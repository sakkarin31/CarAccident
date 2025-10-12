# -*- coding: utf-8 -*-
"""
AADT -> Postgres (normalized, minimal cleaning)
- ดึง resource ล่าสุดที่มีจริง (CSV/XLSX) จากหน้า traf62 (ชอบ CSV ก่อน)
- อ่าน CSV แบบเดา encoding (utf-8 / cp874 / latin1)
- แม็พหัวคอลัมน์ด้วย regex แบบยืดหยุ่น (รับมือช่องว่าง/วงเล็บ)
- แปลงตัวเลข: ลบ ',' และ '%' แล้ว cast
- CREATE TABLE aadt_table (คอลัมน์อังกฤษ snake_case)
- UPSERT ตาม (source_year_be, route_no, control_section, station_km)
- schedule: ทุกวันที่ 1 เวลา 07:00
"""

from datetime import datetime, timedelta
import logging, re

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- helpers ----------------
def _thai_prev_year_be():
    # ปี พ.ศ. ของปีก่อนหน้า (เว็บอัปเดตเป็นปีก่อนปัจจุบัน)
    return datetime.now().year + 543 - 1

def _find_latest_aadt_resource():
    """คืน (year_be, url, fmt) โดยปีที่มีจริงมากที่สุด <= ปีเป้าหมาย; ให้ 'CSV' มาก่อน 'XLSX' เสมอ"""
    import requests
    from bs4 import BeautifulSoup

    target = _thai_prev_year_be()
    pages = [
        "https://datagov.mot.go.th/en/dataset/traf62",
        "https://datagov.mot.go.th/dataset/traf62",
    ]

    per_year = {}  # year -> {"csv": url or None, "xlsx": url or None}
    for url in pages:
        try:
            r = requests.get(url, timeout=30); r.raise_for_status()
        except Exception as e:
            logger.warning(f"skip {url}: {e}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for li in soup.select("li.resource-item"):
            title = (li.select_one("a.heading").get("title") or "").strip() if li.select_one("a.heading") else ""
            a     = li.select_one("a.resource-url-analytics")
            href  = (a.get("href") or "").strip() if a else ""
            if not href:
                continue

            # format
            fmt = None
            if li.select_one("span[data-format='csv']"):  fmt = "csv"
            if li.select_one("span[data-format='xlsx']"): fmt = "xlsx"
            if not fmt:
                fmt = "csv" if href.lower().endswith(".csv") else ("xlsx" if href.lower().endswith(".xlsx") else None)
            if fmt not in ("csv","xlsx"):
                continue

            m = re.search(r"ปี\s*(\d{4})", title) or re.search(r"(25\d{2})", title)
            if not m:
                continue
            y = int(m.group(1))
            per_year.setdefault(y, {"csv": None, "xlsx": None})
            # เก็บลิงก์อันแรกที่เจอของแต่ละฟอร์แมตต่อปี
            if fmt == "csv" and not per_year[y]["csv"]:
                per_year[y]["csv"] = href
            elif fmt == "xlsx" and not per_year[y]["xlsx"]:
                per_year[y]["xlsx"] = href

    if not per_year:
        raise RuntimeError("ไม่พบ resource AADT บนหน้า traf62")

    # หา year <= target ที่ใหญ่สุด; ถ้าไม่มี ให้ใช้ปีล่าสุดที่มี
    candidates = sorted([y for y in per_year.keys() if y <= target], reverse=True)
    if not candidates:
        candidates = sorted(per_year.keys(), reverse=True)

    for y in candidates:
        urls = per_year[y]
        if urls["csv"]:
            logger.info(f"🎯 ใช้ปี {y} (CSV) -> {urls['csv']}")
            return y, urls["csv"], "csv"
        if urls["xlsx"]:
            logger.info(f"🎯 ใช้ปี {y} (XLSX) -> {urls['xlsx']}")
            return y, urls["xlsx"], "xlsx"

    raise RuntimeError("เจอปีแล้วแต่ไม่มี CSV/XLSX ที่อ่านได้")

def _read_tabular(bytes_data, fmt="csv"):
    import pandas as pd
    from io import BytesIO
    if fmt == "xlsx":
        # ถ้าจำเป็นต้องอ่าน xlsx จะต้องมี openpyxl ใน environment
        try:
            return pd.read_excel(BytesIO(bytes_data))
        except ImportError as e:
            raise ImportError("ต้องติดตั้ง openpyxl เพื่ออ่านไฟล์ XLSX (แนะนำให้ใช้ CSV จากแหล่งเดียวกัน)") from e
    # csv: ลองหลาย encoding
    for enc in [None, "utf-8", "cp874", "latin1"]:
        try:
            return pd.read_csv(BytesIO(bytes_data), encoding=enc)
        except Exception:
            pass
    raise RuntimeError("อ่าน CSV ไม่สำเร็จ (encoding)")

def _num(x, pct=False):
    """ลบคอมมา/เปอร์เซ็นต์ -> float; ว่างเป็น 0"""
    if x is None: return 0.0
    s = str(x).strip().replace(",", "")
    if pct: s = s.replace("%", "")
    if s == "": return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0

def _n(s):
    """normalize header: บีบช่องว่าง"""
    return re.sub(r"\s+", " ", str(s)).strip()

def _pick_regex(cols, patterns):
    """เลือกคอลัมน์จาก regex หลายแบบ (return ชื่อคอลัมน์จริง)"""
    for c in cols:
        sc = _n(c)
        for pat in patterns:
            if re.search(pat, sc, flags=re.IGNORECASE):
                return c
    return None

# ---------------- DDL ----------------
def create_table_if_not_exists():
    pg = PostgresHook(postgres_conn_id="postgres_default")
    conn = pg.get_conn(); cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS aadt_table (
        source_year_be INTEGER NOT NULL,
        route_no INTEGER NOT NULL,
        control_section INTEGER NOT NULL,
        route_name TEXT,
        station_km TEXT,
        car_le_7 BIGINT,
        car_gt_7 BIGINT,
        bus_small BIGINT,
        bus_medium BIGINT,
        bus_large BIGINT,
        truck_4w BIGINT,
        truck_6w BIGINT,
        truck_10w BIGINT,
        trailer BIGINT,
        semi_trailer BIGINT,
        total BIGINT,
        heavy_pct NUMERIC(6,3),
        bicycle_2_3 BIGINT,
        moto_tricycle BIGINT,
        highway_district TEXT,
        province TEXT,
        ingested_at TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (source_year_be, route_no, control_section, station_km)
    );
    """)
    conn.commit(); cur.close(); conn.close()
    logger.info("✅ ตรวจสอบ/สร้างตาราง aadt_table เรียบร้อย")

# ---------------- main task ----------------
def load_aadt_to_postgres(**context):
    import requests
    import pandas as pd

    year_be, href, fmt = _find_latest_aadt_resource()
    resp = requests.get(href, timeout=90); resp.raise_for_status()
    df = _read_tabular(resp.content, fmt=fmt)

    if df.empty:
        logger.warning("⚠️ ไฟล์ว่าง"); return

    cols = list(df.columns)

    # ---------- Flexible column mapping ----------
    mapped = {
        "route_no":        _pick_regex(cols, [r"^ทางหลวงสาย$"]),
        "control_section": _pick_regex(cols, [r"^ตอนควบคุม$"]),
        "route_name":      _pick_regex(cols, [r"^ชื่อสายทาง$"]),
        "station_km":      _pick_regex(cols, [r"^จุดสำรวจ$"]),

        "car_le_7":        _pick_regex(cols, [r"^รถยนต์นั่ง *\(? *ไม่เกิน *7 *คน *\)?$"]),
        "car_gt_7":        _pick_regex(cols, [r"^รถยนต์นั่ง *\(? *เกิน *7 *คน *\)?$"]),

        "bus_small":       _pick_regex(cols, [r"^รถโดยสารขนาดเล็ก$"]),
        "bus_medium":      _pick_regex(cols, [r"^รถโดยสารขนาดกลาง$"]),
        "bus_large":       _pick_regex(cols, [r"^รถโดยสารขนาดใหญ่$"]),

        # รับมือกรณีมี/ไม่มีช่องว่างในวงเล็บ
        "truck_4w":        _pick_regex(cols, [
                              r"^รถบรรทุกขนาดเล็ก *\(? *4 *ล้อ *\)?$",
                              r"^รถบรรทุก *\(? *4 *ล้อ *\)?$"
                            ]),
        "truck_6w":        _pick_regex(cols, [
                              r"^รถบรรทุกขนาด *2 *เพลา *\(? *6 *ล้อ *\)?$",
                              r"^รถบรรทุก *\(? *6 *ล้อ *\)?$"
                            ]),
        "truck_10w":       _pick_regex(cols, [
                              r"^รถบรรทุกขนาด *3 *เพลา *\(? *10 *ล้อ *\)?$",
                              r"^รถบรรทุก *\(? *10 *ล้อ *\)?$"
                            ]),
        "trailer":         _pick_regex(cols, [r"^รถบรรทุกพ่วง *\(? *มากกว่า *3 *เพลา *\)?$"]),
        "semi_trailer":    _pick_regex(cols, [r"^รถบรรทุกกึ่งพ่วง *\(? *มากกว่า *3 *เพลา *\)?$"]),

        "total":           _pick_regex(cols, [r"^รวม$"]),
        "heavy_pct":       _pick_regex(cols, [r"^% *ของยานยนต์หนัก$"]),

        "bicycle_2_3":     _pick_regex(cols, [r"^จักรยาน *2 *ล้อ *และ *จักรยาน *3 *ล้อ$"]),
        "moto_tricycle":   _pick_regex(cols, [r"^สามล้อเครื่องและจักรยานยนต์$"]),

        "highway_district":_pick_regex(cols, [r"^แขวงทางหลวง$"]),
        "province":        _pick_regex(cols, [r"^จังหวัด$"]),
    }

    required_keys = [
        "route_no","control_section","route_name","station_km",
        "car_le_7","car_gt_7","bus_small","bus_medium","bus_large",
        "truck_4w","truck_6w","truck_10w","trailer","semi_trailer",
        "total","heavy_pct","bicycle_2_3","moto_tricycle",
        "highway_district","province"
    ]
    missing = [k for k,v in mapped.items() if v is None and k in required_keys]
    if missing:
        logger.error("หัวคอลัมน์ที่หาไม่เจอ: %s", missing)
        logger.error("หัวคอลัมน์ในไฟล์ (normalize แล้ว): %s", [_n(c) for c in cols])
        raise RuntimeError(f"ไม่พบคอลัมน์ที่จำเป็น: {missing}")

    # ---------- Transform & load ----------
    create_table_if_not_exists()

    rows = []
    for _, r in df.iterrows():
        try:
            route_no        = int(_num(r[mapped["route_no"]]))
            control_section = int(_num(r[mapped["control_section"]]))
        except Exception:
            # ถ้า route/ตอน ไม่ใช่ตัวเลข → ข้ามแถวนี้
            continue

        row = dict(
            source_year_be = int(year_be),
            route_no = route_no,
            control_section = control_section,
            route_name = None if pd.isna(r[mapped["route_name"]]) else str(r[mapped["route_name"]]).strip(),
            station_km = None if pd.isna(r[mapped["station_km"]]) else str(r[mapped["station_km"]]).strip(),

            car_le_7   = int(_num(r[mapped["car_le_7"]])),
            car_gt_7   = int(_num(r[mapped["car_gt_7"]])),
            bus_small  = int(_num(r[mapped["bus_small"]])),
            bus_medium = int(_num(r[mapped["bus_medium"]])),
            bus_large  = int(_num(r[mapped["bus_large"]])),
            truck_4w   = int(_num(r[mapped["truck_4w"]])),
            truck_6w   = int(_num(r[mapped["truck_6w"]])),
            truck_10w  = int(_num(r[mapped["truck_10w"]])),
            trailer    = int(_num(r[mapped["trailer"]])),
            semi_trailer = int(_num(r[mapped["semi_trailer"]])),
            total      = int(_num(r[mapped["total"]])),
            heavy_pct  = float(_num(r[mapped["heavy_pct"]], pct=True)),
            bicycle_2_3 = int(_num(r[mapped["bicycle_2_3"]])),
            moto_tricycle = int(_num(r[mapped["moto_tricycle"]])),
            highway_district = None if pd.isna(r[mapped["highway_district"]]) else str(r[mapped["highway_district"]]).strip(),
            province   = None if pd.isna(r[mapped["province"]]) else str(r[mapped["province"]]).strip(),
        )
        rows.append(row)

    if not rows:
        logger.warning("⚠️ ไม่มีแถวที่ผ่านเงื่อนไข route_no/control_section เป็นตัวเลข"); 
        return

    pg = PostgresHook(postgres_conn_id="postgres_default")
    conn = pg.get_conn(); cur = conn.cursor()

    upsert = """
    INSERT INTO aadt_table
    (source_year_be, route_no, control_section, route_name, station_km,
     car_le_7, car_gt_7, bus_small, bus_medium, bus_large,
     truck_4w, truck_6w, truck_10w, trailer, semi_trailer,
     total, heavy_pct, bicycle_2_3, moto_tricycle, highway_district, province)
    VALUES
    (%(source_year_be)s, %(route_no)s, %(control_section)s, %(route_name)s, %(station_km)s,
     %(car_le_7)s, %(car_gt_7)s, %(bus_small)s, %(bus_medium)s, %(bus_large)s,
     %(truck_4w)s, %(truck_6w)s, %(truck_10w)s, %(trailer)s, %(semi_trailer)s,
     %(total)s, %(heavy_pct)s, %(bicycle_2_3)s, %(moto_tricycle)s, %(highway_district)s, %(province)s)
    ON CONFLICT (source_year_be, route_no, control_section, station_km)
    DO UPDATE SET
      route_name = EXCLUDED.route_name,
      car_le_7 = EXCLUDED.car_le_7,
      car_gt_7 = EXCLUDED.car_gt_7,
      bus_small = EXCLUDED.bus_small,
      bus_medium = EXCLUDED.bus_medium,
      bus_large = EXCLUDED.bus_large,
      truck_4w = EXCLUDED.truck_4w,
      truck_6w = EXCLUDED.truck_6w,
      truck_10w = EXCLUDED.truck_10w,
      trailer = EXCLUDED.trailer,
      semi_trailer = EXCLUDED.semi_trailer,
      total = EXCLUDED.total,
      heavy_pct = EXCLUDED.heavy_pct,
      bicycle_2_3 = EXCLUDED.bicycle_2_3,
      moto_tricycle = EXCLUDED.moto_tricycle,
      highway_district = EXCLUDED.highway_district,
      province = EXCLUDED.province,
      ingested_at = NOW();
    """
    for row in rows:
        cur.execute(upsert, row)

    conn.commit(); cur.close(); conn.close()
    logger.info(f"✅ นำเข้า/อัปเดต aadt_table จำนวน {len(rows)} แถว สำเร็จ")

# ---------------- DAG ----------------
default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="aadt_to_postgres",
    start_date=datetime(2025, 1, 1),
    schedule_interval="0 7 1 * *",  # ทุกวันที่ 1 เวลา 07:00
    catchup=False,
    default_args=default_args,
    tags=["aadt", "normalized", "postgres"],
) as dag:

    load_task = PythonOperator(
        task_id="load_aadt_to_postgres",
        python_callable=load_aadt_to_postgres
    )
