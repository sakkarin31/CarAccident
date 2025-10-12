# -*- coding: utf-8 -*-
"""
Airflow DAG: Download MOT roadaccident CSV -> normalize to real columns -> UPSERT into Postgres
- PRIMARY KEY = acc_code
- Convert Excel serial dates (e.g., 45658) to DATE
- Convert Excel time fractions / serial-with-fraction / 'HH:MM' to TIME
- Numeric coercion for vehicle counts & injuries
"""

from datetime import datetime, timedelta
import logging
import re

from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------
# Helpers
# ---------------------------
def _be_target_year():
    # ใช้ปี พ.ศ. ปีก่อนปัจจุบัน (dataset ออกเป็นปีที่แล้ว)
    return datetime.now().year + 543 - 1

def excel_serial_to_date(x):
    """
    Excel serial date -> Python date (base 1899-12-30)
    Example: 45658 -> 2025-01-01
    """
    from datetime import datetime, timedelta
    if x in (None, ""):
        return None
    try:
        d = datetime(1899, 12, 30) + timedelta(days=int(float(x)))
        return d.date()
    except Exception:
        return None

def excel_time_to_str(x):
    """
    รองรับเวลา:
    - 'H:MM' หรือ 'HH:MM:SS'
    - เศษส่วนของวัน (0..1), เช่น 0.008333333 -> 00:12:00
    - เลข serial ที่มีทศนิยม (ใช้ส่วนเศษเป็นเวลา), เช่น 45461.599305556 -> 14:23:00
    คืนค่าเป็นสตริง 'HH:MM:SS' หรือ None
    """
    if x in (None, ""):
        return None
    s = str(x).strip()

    # สตริงรูปแบบเวลา
    if ":" in s:
        try:
            if len(s.split(":")) == 2:
                s += ":00"
            from datetime import datetime as _dt
            return _dt.strptime(s, "%H:%M:%S").time().strftime("%H:%M:%S")
        except Exception:
            pass

    # ตัวเลข: ใช้ส่วนเศษของวัน
    try:
        f = float(s)
        frac = f % 1.0
        total_seconds = int(round(frac * 24 * 60 * 60)) % (24 * 60 * 60)
        hh = total_seconds // 3600
        mm = (total_seconds % 3600) // 60
        ss = total_seconds % 60
        return f"{hh:02d}:{mm:02d}:{ss:02d}"
    except Exception:
        return None

def to_int(x):
    try:
        if x is None or str(x).strip() == "":
            return 0
        return int(float(str(x).replace(",", "").strip()))
    except Exception:
        return 0

def to_float(x):
    try:
        if x is None or str(x).strip() == "":
            return None
        return float(str(x).replace(",", "").strip())
    except Exception:
        return None

def norm_colname_th(c):
    # ลดช่องว่างเพื่อให้จับชื่อคอลัมน์ไทยได้แม่นขึ้น
    return re.sub(r"\s+", " ", str(c)).strip()

def pick(cols, pattern):
    """เลือกชื่อคอลัมน์จากหัวคอลัมน์จริงด้วย regex (บนหัวไทยที่ normalize แล้ว)"""
    norm_map = {norm_colname_th(c): c for c in cols}
    for nc, orig in norm_map.items():
        if re.fullmatch(pattern, nc):
            return orig
    return None

# ---------------------------
# Postgres: create table
# ---------------------------
def create_accident_table_if_not_exists():
    """
    ตาราง normalized พร้อม PRIMARY KEY(acc_code)
    """
    pg = PostgresHook(postgres_conn_id='postgres_default')
    conn = pg.get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS accident_events (
            acc_code           TEXT PRIMARY KEY,
            year_be            INTEGER,
            accident_date      DATE,
            accident_time      TIME,
            report_date        DATE,
            report_time        TIME,
            agency             TEXT,
            agency_route_type  TEXT,
            route_code         TEXT,
            route_name         TEXT,
            km_marker          NUMERIC(10,3),
            province           TEXT,
            vehicle1           TEXT,
            site_desc          TEXT,
            assumed_cause      TEXT,
            accident_type      TEXT,
            weather            TEXT,
            latitude           NUMERIC(12,8),
            longitude          NUMERIC(12,8),
            total_vehicles     INTEGER,
            total_entities     INTEGER,
            mc                 INTEGER,
            samlor             INTEGER,
            passenger_car      INTEGER,
            van                INTEGER,
            pickup_passenger   INTEGER,
            bus_gt_4w          INTEGER,
            pickup_4w          INTEGER,
            truck_6w           INTEGER,
            truck_le_10w       INTEGER,
            truck_gt_10w       INTEGER,
            e_tan              INTEGER,
            vehicle_other      INTEGER,
            pedestrian         INTEGER,
            fatalities         INTEGER,
            inj_severe         INTEGER,
            inj_minor          INTEGER,
            injuries_total     INTEGER,
            ingested_at        TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    logger.info("✅ ตรวจสอบ/สร้างตาราง accident_events เรียบร้อย")

# ---------------------------
# Main task
# ---------------------------
def download_transform_load_accident(**context):
    # lazy import กัน Broken DAG ตอน parse
    import pandas as pd
    import requests
    from bs4 import BeautifulSoup
    from io import BytesIO

    year_be = _be_target_year()

    # 1) หา resource CSV ปีเป้าหมายบนหน้าชุดข้อมูล roadaccident
    base_url = "https://datagov.mot.go.th/dataset/roadaccident"
    r = requests.get(base_url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (Airflow Accidents)"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    csv_url = None
    for li in soup.select("li.resource-item"):
        heading = li.select_one("a.heading")
        title = (heading.get("title") if heading else "") or ""
        is_csv = li.select_one("span[data-format='csv']") is not None
        if f"อุบัติเหตุทางถนน ปี{year_be}" in title and is_csv:
            a = li.select_one("a.resource-url-analytics")
            if a and a.get("href"):
                csv_url = a["href"].strip()
                break

    if not csv_url:
        # สำรอง: ถ้าปีเป้าหมายยังไม่มี ให้ลองปี-1
        alt_year = year_be - 1
        for li in soup.select("li.resource-item"):
            heading = li.select_one("a.heading")
            title = (heading.get("title") if heading else "") or ""
            is_csv = li.select_one("span[data-format='csv']") is not None
            a = li.select_one("a.resource-url-analytics")
            if a and a.get("href") and f"อุบัติเหตุทางถนน ปี{alt_year}" in title:
                csv_url = a["href"].strip()
                year_be = alt_year
                break

    if not csv_url:
        raise RuntimeError("ไม่พบลิงก์ CSV อุบัติเหตุสำหรับปี พ.ศ. ที่กำหนด")

    csv_bytes = requests.get(csv_url, timeout=60, headers={"User-Agent": "Mozilla/5.0 (Airflow Accidents)"}).content

    # 2) อ่าน CSV (ลองหลาย encoding)
    def read_guess(b):
        import pandas as pd
        for enc in [None, "utf-8", "cp874", "latin1"]:
            try:
                return pd.read_csv(BytesIO(b), encoding=enc)
            except Exception:
                last = enc
        raise RuntimeError(f"อ่าน CSV ไม่สำเร็จ (enc tried: {last})")

    df = read_guess(csv_bytes)
    if df.empty:
        logger.warning("⚠️ CSV ว่างเปล่า")
        return

    # 3) map คอลัมน์ไทย -> ตัวแปร
    cols = list(df.columns)

    c_year      = pick(cols, r"ปีที่เกิดเหตุ")
    c_date      = pick(cols, r"วันที่เกิดเหตุ")
    c_time      = pick(cols, r"เวลา")
    c_rdate     = pick(cols, r"วันที่รายงาน")
    c_rtime     = pick(cols, r"เวลาที่รายงาน")
    c_code      = pick(cols, r"ACC_CODE")
    c_agency    = pick(cols, r"หน่วยงาน")
    c_type      = pick(cols, r"สายทางหน่วยงาน")
    c_routecode = pick(cols, r"รหัสสายทาง")
    c_routename = pick(cols, r"สายทาง")
    c_km        = pick(cols, r"KM")
    c_prov      = pick(cols, r"จังหวัด")
    c_v1        = pick(cols, r"รถคันที่1")
    c_site      = pick(cols, r"บริเวณที่เกิดเหตุ")
    c_cause     = pick(cols, r"มูลเหตุสันนิษฐาน")
    c_acctype   = pick(cols, r"ลักษณะการเกิดเหตุ")
    c_weather   = pick(cols, r"สภาพอากาศ")
    c_lat       = pick(cols, r"LATITUDE")
    c_lon       = pick(cols, r"LONGITUDE")

    c_totveh    = pick(cols, r"รถที่เกิดเหตุ")
    c_totent    = pick(cols, r"รถและคนที่เกิดเหตุ")
    c_mc        = pick(cols, r"รถจักรยานยนต์")
    c_samlor    = pick(cols, r"รถสามล้อเครื่อง")
    c_pcar      = pick(cols, r"รถยนต์นั่งส่วนบุคคล")
    c_van       = pick(cols, r"รถตู้")
    c_pkpax     = pick(cols, r"รถปิคอัพโดยสาร")
    c_busgt4    = pick(cols, r"รถโดยสารมากกว่า4ล้อ")
    c_pk4       = pick(cols, r"รถปิคอัพบรรทุก4ล้อ")
    c_tr6       = pick(cols, r"รถบรรทุก6ล้อ")
    c_trle10    = pick(cols, r"รถบรรทุกไม่เกิน10ล้อ")
    c_trgt10    = pick(cols, r"รถบรรทุกมากกว่า10ล้อ")
    c_etan      = pick(cols, r"รถอีแต๋น")
    c_other     = pick(cols, r"รถอื่นๆ")
    c_walk      = pick(cols, r"คนเดินเท้า")
    c_fatal     = pick(cols, r"ผู้เสียชีวิต")
    c_injsevere = pick(cols, r"ผู้บาดเจ็บสาหัส")
    c_injminor  = pick(cols, r"ผู้บาดเจ็บเล็กน้อย")
    c_injtotal  = pick(cols, r"รวมจำนวนผู้บาดเจ็บ")

    required = [c_code, c_year, c_date, c_time]
    if any(v is None for v in required):
        raise RuntimeError("หัวคอลัมน์สำคัญหาย: ต้องมีอย่างน้อย ACC_CODE, ปีที่เกิดเหตุ, วันที่เกิดเหตุ, เวลา")

    # 4) แปลงและเตรียมแถวสำหรับ UPSERT
    records = []
    for _, row in df.iterrows():
        acc_code = str(row.get(c_code, "")).strip()
        if not acc_code:
            continue

        record = dict(
            acc_code         = acc_code,
            year_be          = to_int(row.get(c_year)),
            accident_date    = excel_serial_to_date(row.get(c_date)),
            accident_time    = excel_time_to_str(row.get(c_time)),
            report_date      = excel_serial_to_date(row.get(c_rdate)),
            report_time      = excel_time_to_str(row.get(c_rtime)),
            agency           = (str(row.get(c_agency)) if c_agency else None),
            agency_route_type= (str(row.get(c_type)) if c_type else None),
            route_code       = (str(row.get(c_routecode)) if c_routecode else None),
            route_name       = (str(row.get(c_routename)) if c_routename else None),
            km_marker        = to_float(row.get(c_km)) if c_km else None,
            province         = (str(row.get(c_prov)) if c_prov else None),
            vehicle1         = (str(row.get(c_v1)) if c_v1 else None),
            site_desc        = (str(row.get(c_site)) if c_site else None),
            assumed_cause    = (str(row.get(c_cause)) if c_cause else None),
            accident_type    = (str(row.get(c_acctype)) if c_acctype else None),
            weather          = (str(row.get(c_weather)) if c_weather else None),
            latitude         = to_float(row.get(c_lat)) if c_lat else None,
            longitude        = to_float(row.get(c_lon)) if c_lon else None,
            total_vehicles   = to_int(row.get(c_totveh)) if c_totveh else 0,
            total_entities   = to_int(row.get(c_totent)) if c_totent else 0,
            mc               = to_int(row.get(c_mc)) if c_mc else 0,
            samlor           = to_int(row.get(c_samlor)) if c_samlor else 0,
            passenger_car    = to_int(row.get(c_pcar)) if c_pcar else 0,
            van              = to_int(row.get(c_van)) if c_van else 0,
            pickup_passenger = to_int(row.get(c_pkpax)) if c_pkpax else 0,
            bus_gt_4w        = to_int(row.get(c_busgt4)) if c_busgt4 else 0,
            pickup_4w        = to_int(row.get(c_pk4)) if c_pk4 else 0,
            truck_6w         = to_int(row.get(c_tr6)) if c_tr6 else 0,
            truck_le_10w     = to_int(row.get(c_trle10)) if c_trle10 else 0,
            truck_gt_10w     = to_int(row.get(c_trgt10)) if c_trgt10 else 0,
            e_tan            = to_int(row.get(c_etan)) if c_etan else 0,
            vehicle_other    = to_int(row.get(c_other)) if c_other else 0,
            pedestrian       = to_int(row.get(c_walk)) if c_walk else 0,
            fatalities       = to_int(row.get(c_fatal)) if c_fatal else 0,
            inj_severe       = to_int(row.get(c_injsevere)) if c_injsevere else 0,
            inj_minor        = to_int(row.get(c_injminor)) if c_injminor else 0,
            injuries_total   = to_int(row.get(c_injtotal)) if c_injtotal else 0,
        )
        records.append(record)

    logger.info(f"📥 accidents: เตรียมอัปโหลด {len(records)} แถว")

    # 5) เขียนลง Postgres (UPSERT ตาม acc_code)
    pg = PostgresHook(postgres_conn_id='postgres_default')
    conn = pg.get_conn()
    cur = conn.cursor()

    create_accident_table_if_not_exists()

    upsert_sql = """
        INSERT INTO accident_events
        (acc_code, year_be, accident_date, accident_time, report_date, report_time,
         agency, agency_route_type, route_code, route_name, km_marker, province,
         vehicle1, site_desc, assumed_cause, accident_type, weather,
         latitude, longitude, total_vehicles, total_entities,
         mc, samlor, passenger_car, van, pickup_passenger, bus_gt_4w, pickup_4w,
         truck_6w, truck_le_10w, truck_gt_10w, e_tan, vehicle_other, pedestrian,
         fatalities, inj_severe, inj_minor, injuries_total)
        VALUES
        (%(acc_code)s, %(year_be)s, %(accident_date)s, %(accident_time)s, %(report_date)s, %(report_time)s,
         %(agency)s, %(agency_route_type)s, %(route_code)s, %(route_name)s, %(km_marker)s, %(province)s,
         %(vehicle1)s, %(site_desc)s, %(assumed_cause)s, %(accident_type)s, %(weather)s,
         %(latitude)s, %(longitude)s, %(total_vehicles)s, %(total_entities)s,
         %(mc)s, %(samlor)s, %(passenger_car)s, %(van)s, %(pickup_passenger)s, %(bus_gt_4w)s, %(pickup_4w)s,
         %(truck_6w)s, %(truck_le_10w)s, %(truck_gt_10w)s, %(e_tan)s, %(vehicle_other)s, %(pedestrian)s,
         %(fatalities)s, %(inj_severe)s, %(inj_minor)s, %(injuries_total)s)
        ON CONFLICT (acc_code) DO UPDATE SET
         year_be = EXCLUDED.year_be,
         accident_date = EXCLUDED.accident_date,
         accident_time = EXCLUDED.accident_time,
         report_date = EXCLUDED.report_date,
         report_time = EXCLUDED.report_time,
         agency = EXCLUDED.agency,
         agency_route_type = EXCLUDED.agency_route_type,
         route_code = EXCLUDED.route_code,
         route_name = EXCLUDED.route_name,
         km_marker = EXCLUDED.km_marker,
         province = EXCLUDED.province,
         vehicle1 = EXCLUDED.vehicle1,
         site_desc = EXCLUDED.site_desc,
         assumed_cause = EXCLUDED.assumed_cause,
         accident_type = EXCLUDED.accident_type,
         weather = EXCLUDED.weather,
         latitude = EXCLUDED.latitude,
         longitude = EXCLUDED.longitude,
         total_vehicles = EXCLUDED.total_vehicles,
         total_entities = EXCLUDED.total_entities,
         mc = EXCLUDED.mc,
         samlor = EXCLUDED.samlor,
         passenger_car = EXCLUDED.passenger_car,
         van = EXCLUDED.van,
         pickup_passenger = EXCLUDED.pickup_passenger,
         bus_gt_4w = EXCLUDED.bus_gt_4w,
         pickup_4w = EXCLUDED.pickup_4w,
         truck_6w = EXCLUDED.truck_6w,
         truck_le_10w = EXCLUDED.truck_le_10w,
         truck_gt_10w = EXCLUDED.truck_gt_10w,
         e_tan = EXCLUDED.e_tan,
         vehicle_other = EXCLUDED.vehicle_other,
         pedestrian = EXCLUDED.pedestrian,
         fatalities = EXCLUDED.fatalities,
         inj_severe = EXCLUDED.inj_severe,
         inj_minor = EXCLUDED.inj_minor,
         injuries_total = EXCLUDED.injuries_total,
         ingested_at = NOW();
    """

    for rec in records:
        cur.execute(upsert_sql, rec)

    conn.commit()
    cur.close()
    conn.close()
    logger.info("✅ อัปโหลด/อัปเดต accident_events เรียบร้อย")

# ---------------------------
# DAG definition
# ---------------------------
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    'accident_to_postgres',
    default_args=default_args,
    description='MOT accidents -> normalized columns -> Postgres (UPSERT by acc_code)',
    schedule_interval='0 7 1 * *',  # ทุกวันที่ 1 เวลา 07:00
    start_date=datetime(2025, 1, 1),
    catchup=False,
    tags=['accident', 'normalized', 'postgres'],
) as dag:

    load_task = PythonOperator(
        task_id='download_transform_load_accident',
        python_callable=download_transform_load_accident
    )
