# app_songkhla_highway_risk.py
import streamlit as st
import folium
from streamlit_folium import st_folium
import osmnx as ox
import geopandas as gpd
import pandas as pd
import numpy as np
import os

# ----------------------------------------------------------
# ⚙️ การตั้งค่า
# ----------------------------------------------------------
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

# ----------------------------------------------------------
# 🧠 โมเดลความเสี่ยงของคุณ (แทนที่ฟังก์ชันนี้ด้วยโมเดลจริง)
# ----------------------------------------------------------
def predict_accident_risk(highway_ref: str) -> float:
    """
    ฟังก์ชันนี้คือ "โมเดลของคุณ"
    Input: หมายเลขทางหลวง เช่น "4", "41", "402"
    Output: โอกาสเกิดอุบัติเหตุ (%) สำหรับทางหลวงสายนั้นทั้งเส้น
    
    ตัวอย่าง (จำลอง):
    """
    risk_map = {
        "4": 25.0,
        "41": 20.0,
        "42": 18.0,
        "43": 19.0,
        "401": 15.0,
        "402": 22.0,
        "403": 16.0,
        "404": 14.0,
    }
    return risk_map.get(highway_ref, 12.0)  # default 12% สำหรับทางหลวงอื่น


# ----------------------------------------------------------
# 🔍 วิเคราะห์การใช้ทางหลวงแต่ละสาย
# ----------------------------------------------------------
def analyze_highway_usage(route_gdf, highways_gdf):
    """
    วิเคราะห์ว่าเส้นทางใช้ "ทางหลวงแต่ละสาย" ไปกี่เมตร
    Returns: dict เช่น {"4": 12500.0, "41": 3200.0, "non_highway": 5000.0}
    """
    if route_gdf.empty:
        return {"non_highway": 0.0}

    # แปลงเป็น projected CRS (เมตริก)
    route_gdf = route_gdf.to_crs(epsg=3857)
    highways_gdf = highways_gdf.to_crs(epsg=3857)
    route_union = route_gdf.geometry.unary_union

    highway_usage = {}
    total_highway_len = 0.0

    for idx, hw_row in highways_gdf.iterrows():
        ref_val = hw_row.get("ref", None)
        if pd.isna(ref_val) or ref_val == "":
            continue

        inter = hw_row.geometry.intersection(route_union)
        if inter.is_empty:
            continue

        # คำนวณความยาว (เมตร)
        if inter.geom_type == "LineString":
            seg_len = inter.length
        elif inter.geom_type == "MultiLineString":
            seg_len = sum(part.length for part in inter.geoms)
        else:
            seg_len = 0.0

        if seg_len <= 0:
            continue

        # รองรับหลาย ref ในช่องเดียว (เช่น "4;41")
        refs = str(ref_val).split(";")
        for r in refs:
            r_clean = r.strip()
            if not r_clean:
                continue
            if r_clean not in highway_usage:
                highway_usage[r_clean] = 0.0
            highway_usage[r_clean] += seg_len
            total_highway_len += seg_len

    total_route_len = route_gdf.length.sum()
    non_highway_len = max(0.0, total_route_len - total_highway_len)
    highway_usage["non_highway"] = non_highway_len
    return highway_usage


# ----------------------------------------------------------
# 📏 คำนวณระยะทางรวม (เมตร)
# ----------------------------------------------------------
def get_route_length_meters(gdf):
    if gdf.empty:
        return 0.0
    gdf_proj = gdf.to_crs(epsg=3857)
    return float(gdf_proj.length.sum())


# ----------------------------------------------------------
# 🗺️ โหลดข้อมูล
# ----------------------------------------------------------
@st.cache_data
def load_highways():
    path = os.path.join(DATA_DIR, "songkhla_highways.geojson")
    if os.path.exists(path):
        return gpd.read_file(path)
    else:
        G = ox.graph_from_place("Songkhla Province, Thailand", network_type="drive")
        edges = ox.graph_to_gdfs(G, nodes=False, edges=True)
        edges = edges.to_crs(epsg=4326)
        if "ref" not in edges.columns:
            edges["ref"] = None
        highways = edges[
            edges["ref"].notna() & 
            (edges["ref"] != "") & 
            (edges["ref"] != "None")
        ].copy()
        highways.to_file(path, driver="GeoJSON")
        return highways


@st.cache_resource
def load_graph_and_kdtree():
    path = os.path.join(DATA_DIR, "songkhla.graphml")
    if os.path.exists(path):
        G = ox.load_graphml(path)
    else:
        with st.spinner("กำลังโหลดแผนที่สงขลา..."):
            G = ox.graph_from_place("Songkhla Province, Thailand", network_type="drive")
            ox.save_graphml(G, path)
    
    from sklearn.neighbors import BallTree
    nodes = ox.graph_to_gdfs(G, edges=False)[["x", "y"]]
    coords = np.deg2rad(nodes[["y", "x"]].values)
    kdtree = BallTree(coords, metric="haversine")
    return G, kdtree, nodes


@st.cache_data
def get_map_center():
    return {"center": [7.18, 100.6]}


# ----------------------------------------------------------
# 🎨 UI หลัก
# ----------------------------------------------------------
st.set_page_config(page_title="🛣️ Highway Risk Analyzer", layout="wide")
st.title("🛣️ วิเคราะห์การใช้ทางหลวง + ความเสี่ยงอุบัติเหตุ")

# โหลดข้อมูล
G, kdtree, nodes_gdf = load_graph_and_kdtree()
highways = load_highways()
map_info = get_map_center()

# State
if "points" not in st.session_state:
    st.session_state["points"] = []
if "route_coords" not in st.session_state:
    st.session_state["route_coords"] = None
if "analysis" not in st.session_state:
    st.session_state["analysis"] = {}

col_map, col_ctrl = st.columns([3, 1])

# 🗺️ แผนที่
with col_map:
    m = folium.Map(location=map_info["center"], zoom_start=10)
    colors = ["green", "red"]
    labels = ["🚩 จุดเริ่มต้น", "🏁 จุดปลายทาง"]
    for i, p in enumerate(st.session_state["points"]):
        folium.Marker(
            [p["lat"], p["lng"]],
            icon=folium.Icon(color=colors[i], icon="map-marker", prefix="fa"),
            tooltip=labels[i]
        ).add_to(m)

    if st.session_state["route_coords"]:
        folium.PolyLine(
            st.session_state["route_coords"],
            color="red", weight=4, opacity=0.9
        ).add_to(m)

    out = st_folium(m, width=900, height=600, key="main_map")

# 🎛️ ควบคุม
with col_ctrl:
    st.subheader("📍 ปัก 2 จุดเพื่อวิเคราะห์")

    if st.button("🗑 ล้างทั้งหมด"):
        st.session_state["points"] = []
        st.session_state["route_coords"] = None
        st.session_state["analysis"] = {}
        st.rerun()

    if out and out.get("last_clicked") and len(st.session_state["points"]) < 2:
        lat = out["last_clicked"]["lat"]
        lng = out["last_clicked"]["lng"]
        query = np.deg2rad([[lat, lng]])
        _, idx = kdtree.query(query, k=1)
        nearest_node = nodes_gdf.index[idx[0][0]]
        node_data = G.nodes[nearest_node]
        st.session_state["points"].append({"lat": node_data["y"], "lng": node_data["x"]})
        st.session_state["route_coords"] = None
        st.session_state["analysis"] = {}
        st.rerun()

    if st.session_state["points"]:
        st.write(f"ปักแล้ว: {len(st.session_state['points'])}/2 จุด")
        for i, p in enumerate(st.session_state["points"]):
            st.text(f"{labels[i]}: {p['lat']:.5f}, {p['lng']:.5f}")

    # คำนวณเมื่อครบ 2 จุด
    if len(st.session_state["points"]) == 2 and st.session_state["route_coords"] is None:
        try:
            with st.spinner("🔍 วิเคราะห์เส้นทางและทางหลวง..."):
                p1, p2 = st.session_state["points"]
                query1 = np.deg2rad([[p1["lat"], p1["lng"]]])
                _, idx1 = kdtree.query(query1, k=1)
                orig = nodes_gdf.index[idx1[0][0]]

                query2 = np.deg2rad([[p2["lat"], p2["lng"]]])
                _, idx2 = kdtree.query(query2, k=1)
                dest = nodes_gdf.index[idx2[0][0]]

                route = ox.shortest_path(G, orig, dest, weight="length")
                if route is None:
                    raise Exception("ไม่พบเส้นทาง")

                route_gdf = ox.routing.route_to_gdf(G, route)

                # แปลงเป็นพิกัดสำหรับแผนที่
                route_coords = []
                for geom in route_gdf.geometry:
                    if geom.geom_type == "LineString":
                        route_coords.extend([(lat, lon) for lon, lat in geom.coords])
                    elif geom.geom_type == "MultiLineString":
                        for part in geom.geoms:
                            route_coords.extend([(lat, lon) for lon, lat in part.coords])

                # 🔍 วิเคราะห์การใช้ทางหลวง
                highway_usage = analyze_highway_usage(route_gdf, highways)
                total_len = get_route_length_meters(route_gdf)

                # 🧠 คำนวณความเสี่ยงรวม
                total_risk = 0.0
                if total_len > 0:
                    for ref, length in highway_usage.items():
                        if ref == "non_highway":
                            risk = 8.0  # ค่า default สำหรับถนนทั่วไป
                        else:
                            risk = predict_accident_risk(ref)
                        weight = length / total_len
                        total_risk += weight * risk

                st.session_state["route_coords"] = route_coords
                st.session_state["analysis"] = {
                    "highway_usage": highway_usage,
                    "total_length": total_len,
                    "overall_risk": total_risk
                }
                st.rerun()
        except Exception as e:
            st.error(f"❌ ข้อผิดพลาด: {str(e)}")

    # แสดงผลลัพธ์
    if st.session_state["analysis"]:
        analysis = st.session_state["analysis"]
        usage = analysis["highway_usage"]
        total_len = analysis["total_length"]
        overall_risk = analysis["overall_risk"]

        st.success("✅ วิเคราะห์เสร็จสิ้น!")
        
        # 📏 ระยะทางรวม
        st.metric("📏 ระยะทางรวม", f"{total_len:.0f} เมตร")
        
        # 🛣️ แสดงการใช้ทางหลวง
        st.markdown("### 🛣️ การใช้ถนน")
        for ref, length in sorted(usage.items(), key=lambda x: -x[1]):
            pct = (length / total_len * 100) if total_len > 0 else 0
            if ref == "non_highway":
                label = "ถนนทั่วไป"
            else:
                label = f"ถนนหมายเลข {ref}"
            st.write(f"- **{label}**: {length:.0f} ม. ({pct:.1f}%)")
        
        # ⚠️ ความเสี่ยงรวม
        st.metric("⚠️ โอกาสเกิดอุบัติเหตุโดยรวม", f"{overall_risk:.1f}%")
        
        st.caption("ℹ️ ความเสี่ยงคำนวณจากโมเดลของคุณ + ถ่วงน้ำหนักตามระยะทาง")