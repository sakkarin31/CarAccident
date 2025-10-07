# app_songkhla.py
import streamlit as st
import folium
from streamlit_folium import st_folium
import osmnx as ox
import geopandas as gpd
import numpy as np
import os

# ----------------------------------------------------------
# ⚙️ การตั้งค่า
# ----------------------------------------------------------
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)


# ----------------------------------------------------------
# 🧠 โมเดลความเสี่ยง (จำลอง)
# ----------------------------------------------------------
def predict_accident_risk(road_type: str) -> float:
    """กำหนดค่าความเสี่ยงเบื้องต้นแบบจำลอง"""
    if road_type == "highway_4":
        return 25.0
    elif road_type == "other_highways":
        return 18.0
    else:  # rural_roads
        return 8.0


# ----------------------------------------------------------
# 🗺️ โหลดข้อมูล
# ----------------------------------------------------------
@st.cache_resource
def load_graph():
    path = os.path.join(DATA_DIR, "songkhla.graphml")
    if os.path.exists(path):
        G = ox.load_graphml(path)
    else:
        with st.spinner("📦 กำลังดาวน์โหลดข้อมูลถนนสงขลา..."):
            G = ox.graph_from_place("Songkhla Province, Thailand", network_type="drive")
            ox.save_graphml(G, path)
    return G


@st.cache_data
def get_map_center():
    return {"center": [7.18, 100.6]}


# ----------------------------------------------------------
# 📊 วิเคราะห์ประเภทถนนจากกราฟ OSMnx โดยตรง
# ----------------------------------------------------------
def analyze_route_by_ref_and_type(G, route):
    """
    แยกเส้นทางตามประเภทถนน:
      - highway_4: ref == '4'
      - other_highways: trunk/primary/motorway
      - rural_roads: อื่นๆ
    """
    highway4_len = 0.0
    other_highway_len = 0.0
    rural_len = 0.0

    for u, v in zip(route[:-1], route[1:]):
        data = G.get_edge_data(u, v)[0]
        length = data.get("length", 0.0)
        ref = str(data.get("ref", ""))
        road_type = data.get("highway", "")

        # 1️⃣ ทางหลวงหมายเลข 4
        if "4" in ref.split(";"):
            highway4_len += length
        # 2️⃣ ทางหลวงอื่น (trunk / primary / motorway)
        elif road_type in ["motorway", "trunk", "primary"]:
            other_highway_len += length
        # 3️⃣ ถนนทั่วไป
        else:
            rural_len += length

    return {
        "highway_4": highway4_len,
        "other_highways": other_highway_len,
        "rural_roads": rural_len
    }


# ----------------------------------------------------------
# 🧮 คำนวณความยาวรวม
# ----------------------------------------------------------
def get_total_length(result):
    return sum(result.values())


# ----------------------------------------------------------
# 🧠 คำนวณความเสี่ยงรวม (ถ่วงน้ำหนักตามระยะทาง)
# ----------------------------------------------------------
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
map_info = get_map_center()

if "points" not in st.session_state:
    st.session_state["points"] = []
if "route_coords" not in st.session_state:
    st.session_state["route_coords"] = None
if "analysis" not in st.session_state:
    st.session_state["analysis"] = None

col_map, col_ctrl = st.columns([3, 1])

# ----------------------------------------------------------
# 🗺️ แผนที่
# ----------------------------------------------------------
with col_map:
    m = folium.Map(location=map_info["center"], zoom_start=10)
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

    out = st_folium(m, width=900, height=600, key="main_map")


# ----------------------------------------------------------
# 🎛️ ควบคุม
# ----------------------------------------------------------
with col_ctrl:
    st.subheader("📍 ปัก 2 จุดเพื่อวิเคราะห์เส้นทาง")

    if st.button("🗑 ล้างทั้งหมด"):
        st.session_state["points"] = []
        st.session_state["route_coords"] = None
        st.session_state["analysis"] = None
        st.rerun()

    # เมื่อคลิกบนแผนที่
    if out and out.get("last_clicked") and len(st.session_state["points"]) < 2:
        lat = out["last_clicked"]["lat"]
        lng = out["last_clicked"]["lng"]
        st.session_state["points"].append({"lat": lat, "lng": lng})
        st.session_state["route_coords"] = None
        st.session_state["analysis"] = None
        st.rerun()

    # แสดงจุดที่เลือกแล้ว
    if st.session_state["points"]:
        st.write(f"ปักแล้ว: {len(st.session_state['points'])}/2 จุด")
        for i, p in enumerate(st.session_state["points"]):
            st.text(f"{labels[i]}: {p['lat']:.5f}, {p['lng']:.5f}")

    # ------------------------------------------------------
    # 🔍 คำนวณเส้นทาง
    # ------------------------------------------------------
    if len(st.session_state["points"]) == 2 and st.session_state["route_coords"] is None:
        try:
            with st.spinner("🧭 กำลังคำนวณเส้นทาง..."):
                p1, p2 = st.session_state["points"]
                orig = ox.distance.nearest_nodes(G, p1["lng"], p1["lat"])
                dest = ox.distance.nearest_nodes(G, p2["lng"], p2["lat"])

                route = ox.shortest_path(G, orig, dest, weight="length")
                if route is None:
                    raise Exception("ไม่พบเส้นทาง")

                # สร้าง GeoDataFrame ของเส้นทาง
                route_gdf = ox.routing.route_to_gdf(G, route)
                route_coords = []
                for geom in route_gdf.geometry:
                    if geom.geom_type == "LineString":
                        route_coords.extend([(lat, lon) for lon, lat in geom.coords])
                    elif geom.geom_type == "MultiLineString":
                        for part in geom.geoms:
                            route_coords.extend([(lat, lon) for lon, lat in part.coords])

                # วิเคราะห์ประเภทถนน
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

    # ------------------------------------------------------
    # 📊 แสดงผลลัพธ์
    # ------------------------------------------------------
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
