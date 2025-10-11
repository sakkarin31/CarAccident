# app_songkhla.py
import streamlit as st
import folium
from streamlit_folium import st_folium
import osmnx as ox
from osmnx import graph as ox_graph
from osmnx import geocoder as ox_geo
import geopandas as gpd
import pandas as pd
import numpy as np
import os
import re
from shapely.geometry import Point
from shapely.ops import unary_union
import zipfile, urllib.request
from sqlalchemy import create_engine, text
from datetime import date, timedelta

# ----------------------------------------------------------
# ⚙️ การตั้งค่าฐานข้อมูล
# ----------------------------------------------------------
DB_CONFIG = {
    "host": "localhost",
    "port": 5433,
    "database": "airflow",
    "user": "airflow",
    "password": "airflow"
}

def get_db_connection():
    conn_str = f"postgresql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
    return create_engine(conn_str)

# ----------------------------------------------------------
# 🛣️ รายการทางหลวงในสงขลา
# ----------------------------------------------------------
SONGKHLA_HIGHWAYS = {
    4, 42, 43, 406, 407, 408, 414, 4053, 4054,
    4083, 4085, 4095, 4113, 4135, 4145, 4208,
    4243, 4287, 4309
}

# ----------------------------------------------------------
# 🗺️ โหลดกราฟถนน
# ----------------------------------------------------------
@st.cache_resource
def load_graph():
    DATA_DIR = "data"
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "songkhla.graphml")
    if os.path.exists(path):
        G = ox.load_graphml(path)
    else:
        with st.spinner("📦 กำลังดาวน์โหลดข้อมูลถนนสงขลา..."):
            G = ox_graph.graph_from_place("Songkhla Province, Thailand", network_type="drive")
            ox.save_graphml(G, path)
    return G

@st.cache_data
def get_map_center():
    return {"center": [7.18, 100.6]}

# ----------------------------------------------------------
# 🗾 โหลดขอบเขตจังหวัดสงขลา (บนบกเท่านั้น)
# ----------------------------------------------------------
@st.cache_data
def load_boundary():
    DATA_DIR = "data"
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "songkhla_boundary_auto.geojson")
    if os.path.exists(path):
        return gpd.read_file(path)

    st.info("🌍 กำลังโหลดขอบเขตจังหวัดสงขลาเฉพาะบนบก (ครั้งแรกอาจใช้เวลา 1-2 นาที)...")

    bbox = (99.8, 6.3, 101.3, 7.8)
    url = "https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip"
    land_zip = os.path.join(DATA_DIR, "land-polygons-split-4326.zip")
    land_dir = os.path.join(DATA_DIR, "land_polygons_songkhla")

    if not os.path.exists(land_zip):
        urllib.request.urlretrieve(url, land_zip)
    if not os.path.exists(land_dir):
        with zipfile.ZipFile(land_zip, "r") as zf:
            zf.extractall(land_dir)

    land_path = None
    for root, _, files in os.walk(land_dir):
        for f in files:
            if f.endswith(".shp"):
                land_path = os.path.join(root, f)
                break
        if land_path:
            break

    land = gpd.read_file(land_path, bbox=bbox).to_crs(epsg=4326)
    land_union = unary_union(land.geometry)
    province = ox_geo.geocode_to_gdf("Songkhla Province, Thailand").to_crs(epsg=4326)
    province_on_land = province.intersection(land_union)

    geom_list = []
    for geom in province_on_land:
        if geom is None:
            continue
        if hasattr(geom, "geoms"):
            geom_list.extend(list(geom.geoms))
        else:
            geom_list.append(geom)

    boundary_no_sea = gpd.GeoDataFrame(geometry=geom_list, crs="EPSG:4326")
    boundary_no_sea.to_file(path, driver="GeoJSON")
    return boundary_no_sea

# ----------------------------------------------------------
# 📊 ดึงข้อมูลความเสี่ยงสำหรับวันที่ที่เลือก
# ----------------------------------------------------------
@st.cache_data(ttl=300)
def load_road_risk_for_date(target_date: date):
    try:
        engine = get_db_connection()
        query = text("SELECT road, pct FROM db_result_model WHERE date = :target_date")
        df = pd.read_sql(query, engine, params={"target_date": target_date})
        
        df["road"] = pd.to_numeric(df["road"], errors="coerce")
        risk_map = {}
        for _, row in df.iterrows():
            if pd.notna(row["road"]):
                risk_map[int(row["road"])] = float(row["pct"])
        return risk_map
    except Exception as e:
        st.warning(f"⚠️ ไม่พบข้อมูลความเสี่ยงสำหรับวันที่ {target_date}")
        return {}

# ----------------------------------------------------------
# 🔍 ดึงหมายเลขถนนจาก ref
# ----------------------------------------------------------
def extract_road_numbers_from_ref(ref_str):
    if not ref_str or str(ref_str).lower() in ["nan", "none", "", "null"]:
        return []
    numbers = []
    parts = str(ref_str).split(";")
    for part in parts:
        part = part.strip()
        if part.isdigit():
            try:
                numbers.append(int(part))
            except ValueError:
                continue
    return numbers

# ----------------------------------------------------------
# 📊 วิเคราะห์เส้นทาง
# ----------------------------------------------------------
def analyze_route_final(G, route, risk_map):
    total_length = 0.0
    weighted_risk_sum = 0.0
    highway_length_by_number = {}
    local_length = 0.0

    for u, v in zip(route[:-1], route[1:]):
        data = G.get_edge_data(u, v)[0]
        length = data.get("length", 0.0)
        ref = data.get("ref", "")
        hwy_type = data.get("highway", "")

        road_numbers = extract_road_numbers_from_ref(ref)
        is_highway = False
        risk = 0.0

        for num in road_numbers:
            if num in SONGKHLA_HIGHWAYS:
                is_highway = True
                if num not in highway_length_by_number:
                    highway_length_by_number[num] = 0.0
                highway_length_by_number[num] += length
                risk = risk_map.get(num, 0.0)
                break

        if not is_highway and hwy_type in ["motorway", "trunk", "primary"]:
            is_highway = True
            risk = 0.0

        if is_highway:
            pass
        else:
            local_length += length

        total_length += length
        weighted_risk_sum += risk * length

    avg_risk = weighted_risk_sum / total_length if total_length > 0 else 0.0

    return {
        "total_length": total_length,
        "total_risk": avg_risk,
        "highway_length_by_number": highway_length_by_number,
        "local_length": local_length
    }

# ----------------------------------------------------------
# 🎨 UI หลัก
# ----------------------------------------------------------
st.set_page_config(page_title="🛣️ Highway Risk Analyzer (สงขลา)", layout="wide")
st.title("🛣️ วิเคราะห์เส้นทางและความเสี่ยงทางหลวง (สงขลา)")

G = load_graph()
boundary = load_boundary()
map_info = get_map_center()

if "points" not in st.session_state:
    st.session_state["points"] = []
if "route_coords" not in st.session_state:
    st.session_state["route_coords"] = None
if "analysis" not in st.session_state:
    st.session_state["analysis"] = None
if "show_boundary" not in st.session_state:
    st.session_state["show_boundary"] = True

col_map, col_ctrl = st.columns([3, 1])

# ----------------------------------------------------------
# 🗺️ แผนที่
# ----------------------------------------------------------
with col_map:
    m = folium.Map(location=map_info["center"], zoom_start=10)

    if st.session_state["show_boundary"]:
        folium.GeoJson(
            boundary.__geo_interface__,
            style_function=lambda x: {"color": "black", "fill": False, "weight": 2},
            tooltip="ขอบเขตจังหวัดสงขลา (บนบก)"
        ).add_to(m)

    colors = ["green", "red"]
    labels = ["🚩 จุดเริ่มต้น", "🏁 จุดปลายทาง"]
    for i, p in enumerate(st.session_state["points"]):
        folium.Marker(
            [p["lat"], p["lng"]],
            tooltip=labels[i],
            icon=folium.Icon(color=colors[i], icon="map-marker", prefix="fa")
        ).add_to(m)

    if st.session_state["route_coords"]:
        folium.PolyLine(
            st.session_state["route_coords"], color="blue", weight=4, opacity=0.9
        ).add_to(m)

    out = st_folium(m, width="100%", height=600, key="main_map")

# ----------------------------------------------------------
# 🎛️ ควบคุม
# ----------------------------------------------------------
with col_ctrl:
    st.subheader("📍 ปัก 2 จุดเพื่อวิเคราะห์เส้นทาง")

    # 📅 ปฎิทินเลือกวันที่
    today = date.today()
    max_date = today + timedelta(days=30)
    selected_date = st.date_input(
        "📅 เลือกวันที่สำหรับวิเคราะห์ความเสี่ยง:",
        value=today,
        min_value=today,
        max_value=max_date,
        key="selected_date"
    )

    # ✅ Checkbox ขอบเขต
    show_boundary = st.checkbox(
        "แสดงขอบเขตจังหวัดสงขลา (บนบก)",
        value=st.session_state.get("show_boundary", True),
        key="show_boundary"
    )

    if st.button("🗑️ ล้างแผนที่", type="secondary", use_container_width=True):
        st.session_state["points"] = []
        st.session_state["route_coords"] = None
        st.session_state["analysis"] = None
        st.rerun()

    if out and out.get("last_clicked") and len(st.session_state["points"]) < 2:
        lat = out["last_clicked"]["lat"]
        lng = out["last_clicked"]["lng"]
        point = Point(lng, lat)

        if not boundary.unary_union.contains(point):
            st.warning("ตำแหน่งนี้อยู่นอกพื้นที่จังหวัดสงขลา")
        else:
            try:
                nearest_node = ox.distance.nearest_nodes(G, lng, lat)
                nearest_point = G.nodes[nearest_node]
                new_lat = nearest_point["y"]
                new_lng = nearest_point["x"]
                st.session_state["points"].append({"lat": new_lat, "lng": new_lng})
                st.session_state["route_coords"] = None
                st.session_state["analysis"] = None
                st.rerun()
            except Exception:
                st.warning("⚠️ ไม่สามารถปักหมุดตรงนี้ได้ (อยู่นอกเครือข่ายถนน)")

    if st.session_state["points"]:
        st.write(f"ปักแล้ว: {len(st.session_state['points'])}/2 จุด")
        for i, p in enumerate(st.session_state["points"]):
            st.text(f"{labels[i]}: {p['lat']:.5f}, {p['lng']:.5f}")

    if len(st.session_state["points"]) == 2 and st.session_state["route_coords"] is None:
        try:
            with st.spinner(f"🧭 กำลังวิเคราะห์เส้นทางสำหรับวันที่ {selected_date}..."):
                p1, p2 = st.session_state["points"]
                orig = ox.distance.nearest_nodes(G, p1["lng"], p1["lat"])
                dest = ox.distance.nearest_nodes(G, p2["lng"], p2["lat"])

                route = ox.shortest_path(G, orig, dest, weight="length")
                if route is None:
                    raise Exception("ไม่พบเส้นทาง")

                risk_map = load_road_risk_for_date(selected_date)
                analysis_result = analyze_route_final(G, route, risk_map)
                analysis_result["target_date"] = selected_date

                route_gdf = ox.routing.route_to_gdf(G, route)
                route_coords = []
                for geom in route_gdf.geometry:
                    if geom.geom_type == "LineString":
                        route_coords.extend([(lat, lon) for lon, lat in geom.coords])
                    elif geom.geom_type == "MultiLineString":
                        for part in geom.geoms:
                            route_coords.extend([(lat, lon) for lon, lat in part.coords])

                st.session_state["route_coords"] = route_coords
                st.session_state["analysis"] = analysis_result
                st.rerun()
        except Exception as e:
            st.error(f"❌ ข้อผิดพลาด: {str(e)}")

    if st.session_state["analysis"]:
        a = st.session_state["analysis"]
        target_date = a["target_date"]
        st.success(f"✅ วิเคราะห์เส้นทางสำหรับวันที่ {target_date} เรียบร้อย!")
        
        total_km = a['total_length'] / 1000
        col1, col2 = st.columns(2)
        with col1:
            st.metric("📏 ระยะทางรวม", f"{total_km:.2f} กม.")
        with col2:
            st.metric("⚠️ ความเสี่ยงโดยรวม", f"{a['total_risk']:.1f}%")
        
        with st.expander("ดูรายละเอียดการใช้ทางหลวง", expanded=False):
            if a["highway_length_by_number"]:
                st.markdown("### ระยะทางตามหมายเลขทางหลวง")
                for road_num, length_m in sorted(a["highway_length_by_number"].items()):
                    length_km = length_m / 1000
                    st.write(f"- **ทางหลวงหมายเลข {road_num}**: {length_km:.2f} กม.")
            else:
                st.write("ไม่พบการใช้ทางหลวงหมายเลข")
            
            local_km = a['local_length'] / 1000
            st.write(f"**ถนนทั่วไป (ไม่นับความเสี่ยง)**: {local_km:.2f} กม.")
            st.caption("ℹ️ ความเสี่ยงคำนวณเฉพาะจากทางหลวงที่มีข้อมูลในระบบ")