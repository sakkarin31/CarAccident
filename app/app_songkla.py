# app_songkhla.py (เวอร์ชันเร็ว + ตัดทะเลแล้ว + เปิด/ปิดขอบเขตได้)
import streamlit as st
import folium
from streamlit_folium import st_folium
import osmnx as ox
from osmnx import graph as ox_graph
from osmnx import geocoder as ox_geo
import geopandas as gpd
import numpy as np
import os
from shapely.geometry import Point
from shapely.ops import unary_union
import zipfile, urllib.request

# ----------------------------------------------------------
# ⚙️ การตั้งค่า
# ----------------------------------------------------------
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# ----------------------------------------------------------
# 🧠 โมเดลความเสี่ยง (จำลอง)
# ----------------------------------------------------------
def predict_accident_risk(road_type: str) -> float:
    if road_type == "highway_4":
        return 25.0
    elif road_type == "other_highways":
        return 18.0
    else:
        return 8.0

# ----------------------------------------------------------
# 🗺️ โหลดกราฟถนน
# ----------------------------------------------------------
@st.cache_resource
def load_graph():
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
    """โหลดขอบเขตจังหวัดสงขลาเฉพาะบนบก (ไม่ต้องโหลดไฟล์ shapefile แยก)"""

    path = os.path.join(DATA_DIR, "songkhla_boundary_auto.geojson")
    if os.path.exists(path):
        return gpd.read_file(path)

    st.info("🌍 กำลังโหลดขอบเขตจังหวัดสงขลาเฉพาะบนบก (ครั้งแรกอาจใช้เวลา 1-2 นาที)...")

    # ✅ 1. โหลด land polygons เฉพาะ bounding box รอบสงขลา (ไม่ทั้งโลก)
    bbox = (99.8, 6.3, 101.3, 7.8)  # รอบจังหวัดสงขลา
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

    # ✅ โหลดเฉพาะส่วนใน bbox (ตัดพื้นที่ให้เล็กมาก)
    land = gpd.read_file(land_path, bbox=bbox).to_crs(epsg=4326)
    land_union = unary_union(land.geometry)

    # ✅ 2. โหลดขอบเขตจังหวัดสงขลา
    province = ox_geo.geocode_to_gdf("Songkhla Province, Thailand").to_crs(epsg=4326)

    # ✅ 3. ตัดเฉพาะบนบก
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

    # ✅ 4. บันทึก cache ไว้ใช้ครั้งต่อไป (โหลดเร็วมาก)
    boundary_no_sea.to_file(path, driver="GeoJSON")

    return boundary_no_sea

# ----------------------------------------------------------
# 📊 วิเคราะห์ประเภทถนน
# ----------------------------------------------------------
def analyze_route_by_ref_and_type(G, route):
    highway4_len = 0.0
    other_highway_len = 0.0
    rural_len = 0.0

    for u, v in zip(route[:-1], route[1:]):
        data = G.get_edge_data(u, v)[0]
        length = data.get("length", 0.0)
        ref = str(data.get("ref", ""))
        road_type = data.get("highway", "")

        if "4" in ref.split(";"):
            highway4_len += length
        elif road_type in ["motorway", "trunk", "primary"]:
            other_highway_len += length
        else:
            rural_len += length

    return {
        "highway_4": highway4_len,
        "other_highways": other_highway_len,
        "rural_roads": rural_len
    }

def get_total_length(result):
    return sum(result.values())

def compute_total_risk(result):
    total_len = get_total_length(result)
    if total_len == 0:
        return 0.0
    risk_sum = 0.0
    for key, length in result.items():
        risk = predict_accident_risk(key)
        weight = length / total_len
        risk_sum += risk * weight
    return risk_sum

# ----------------------------------------------------------
# 🎨 UI หลัก
# ----------------------------------------------------------
st.set_page_config(page_title="🛣️ Highway 4 Risk Analyzer", layout="wide")
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

    # 🧭 แสดงขอบเขตเฉพาะเมื่อผู้ใช้เปิด
    if st.session_state["show_boundary"]:
        folium.GeoJson(
            boundary.__geo_interface__,
            name="ขอบเขตจังหวัดสงขลา (บนบก)",
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

    # ไม่ใช้ LayerControl เพราะควบคุมด้วย checkbox แล้ว
    out = st_folium(m, width=900, height=600, key="main_map")

# ----------------------------------------------------------
# 🎛️ ควบคุม
# ----------------------------------------------------------
with col_ctrl:
    st.subheader("📍 ปัก 2 จุดเพื่อวิเคราะห์เส้นทาง")

    # ✅ ปุ่มเปิด/ปิดขอบเขต — ส่วย ใช้งานง่าย
    show_boundary = st.checkbox(
        "แสดงขอบเขตจังหวัดสงขลา (บนบก)",
        value=st.session_state.get("show_boundary", True),
        key="show_boundary"  # ⬅️ ใช้ key เดียวกับ session_state key
    )

    if st.button("🗑 ล้างทั้งหมด"):
        st.session_state["points"] = []
        st.session_state["route_coords"] = None
        st.session_state["analysis"] = None
        st.rerun()

    if out and out.get("last_clicked") and len(st.session_state["points"]) < 2:
        lat = out["last_clicked"]["lat"]
        lng = out["last_clicked"]["lng"]
        point = Point(lng, lat)

        if not boundary.unary_union.contains(point):
            st.warning("🌊 ตำแหน่งนี้อยู่นอกพื้นที่จังหวัดสงขลา (หรืออยู่ในทะเล)")
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
            with st.spinner("🧭 กำลังคำนวณเส้นทาง..."):
                p1, p2 = st.session_state["points"]
                orig = ox.distance.nearest_nodes(G, p1["lng"], p1["lat"])
                dest = ox.distance.nearest_nodes(G, p2["lng"], p2["lat"])

                route = ox.shortest_path(G, orig, dest, weight="length")
                if route is None:
                    raise Exception("ไม่พบเส้นทาง")

                route_gdf = ox.routing.route_to_gdf(G, route)
                route_coords = []
                for geom in route_gdf.geometry:
                    if geom.geom_type == "LineString":
                        route_coords.extend([(lat, lon) for lon, lat in geom.coords])
                    elif geom.geom_type == "MultiLineString":
                        for part in geom.geoms:
                            route_coords.extend([(lat, lon) for lon, lat in part.coords])

                analysis = analyze_route_by_ref_and_type(G, route)
                total_len = get_total_length(analysis)
                total_risk = compute_total_risk(analysis)

                st.session_state["route_coords"] = route_coords
                st.session_state["analysis"] = {
                    "details": analysis,
                    "total_length": total_len,
                    "total_risk": total_risk
                }
                st.rerun()
        except Exception as e:
            st.error(f"❌ ข้อผิดพลาด: {str(e)}")

    if st.session_state["analysis"]:
        a = st.session_state["analysis"]
        details = a["details"]
        st.success("✅ วิเคราะห์เสร็จสิ้น!")
        st.metric("📏 ระยะทางรวม", f"{a['total_length']:.0f} เมตร")
        st.metric("⚠️ ความเสี่ยงโดยรวม", f"{a['total_risk']:.1f}%")
        st.markdown("### 🚗 การแยกประเภทถนน")
        st.write(f"- **ทางหลวงหมายเลข 4:** {details['highway_4']:.0f} ม.")
        st.write(f"- **ทางหลวงอื่น:** {details['other_highways']:.0f} ม.")
        st.write(f"- **ถนนทั่วไป:** {details['rural_roads']:.0f} ม.")
        st.caption("ℹ️ แยกจากข้อมูล ref และประเภทถนน (OSM highway tag)")