# app_one.py (เวอร์ชันลื่น: ถนนสาย 4 + ถนนรอบๆ ใน buffer)
import streamlit as st
import geopandas as gpd
import networkx as nx
import folium
from streamlit_folium import st_folium
from shapely.geometry import Point, LineString, MultiLineString
import osmnx as ox
import numpy as np
import os
import math

# ----------------------------------------------------------
# ⚙️ การตั้งค่าเริ่มต้น
# ----------------------------------------------------------
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
BUFFER_M = 2000  # ระยะ buffer รอบถนนสาย 4 (เมตร)

# ----------------------------------------------------------
# 🔹 Helper Functions
# ----------------------------------------------------------
def haversine_m(a, b):
    """คำนวณระยะทางระหว่าง 2 จุด (lon, lat) หน่วยเมตร"""
    lon1, lat1 = a
    lon2, lat2 = b
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    A = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(A), math.sqrt(1 - A))

def linestring_length_m(geom):
    coords = list(geom.coords)
    return sum(haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))

# ----------------------------------------------------------
# 🚗 โหลดข้อมูลถนนสาย 4 และถนนใน buffer (cache + local file)
# ----------------------------------------------------------
@st.cache_data
def load_geo_data(buffer_m=BUFFER_M):
    path_edges4 = os.path.join(DATA_DIR, "edges_highway4.geojson")
    path_edges_buffer = os.path.join(DATA_DIR, f"edges_buffer_{buffer_m}m.geojson")

    # ------------------------
    # 1️⃣ โหลดถนนสาย 4
    # ------------------------
    if os.path.exists(path_edges4):
        st.info("📂 ใช้ไฟล์ถนนสาย 4 จาก cache")
        edges_4 = gpd.read_file(path_edges4)
    else:
        st.info("🌐 กำลังโหลดถนนสาย 4 จาก OSM...")
        G_main = ox.graph_from_place(
            "Songkhla Province, Thailand",
            network_type="drive",
            simplify=True,
            custom_filter='["highway"~"motorway|trunk|primary|secondary"]'
        )
        edges_all = ox.graph_to_gdfs(G_main, nodes=False, edges=True)
        edges_all = edges_all.to_crs(epsg=4326)
        edges_all["ref"] = edges_all.get("ref", "").astype(str)
        edges_4 = edges_all[edges_all["ref"].str.contains(r"(^|;)4($|;)", na=False)]
        edges_4.to_file(path_edges4, driver="GeoJSON")
        st.success("✅ บันทึกถนนสาย 4 เรียบร้อย")

    # ------------------------
    # 2️⃣ สร้าง buffer รอบถนนสาย 4
    # ------------------------
    edges_4m = edges_4.to_crs(3857)
    buffer_geom = gpd.GeoSeries([edges_4m.geometry.unary_union.buffer(buffer_m)], crs=3857).to_crs(4326).unary_union

    # ------------------------
    # 3️⃣ โหลดถนนทั้งหมดใน buffer (ครั้งเดียว)
    # ------------------------
    if os.path.exists(path_edges_buffer):
        st.info(f"📂 ใช้ถนนใน buffer {buffer_m/1000:.1f} กม. จาก cache")
        edges_in_buffer = gpd.read_file(path_edges_buffer)
    else:
        st.info("🌐 กำลังโหลดถนนในพื้นที่ buffer จาก OSM...")
        G_local = ox.graph_from_polygon(buffer_geom, network_type="drive")
        edges_in_buffer = ox.graph_to_gdfs(G_local, nodes=False, edges=True)
        edges_in_buffer = edges_in_buffer.to_crs(4326)
        edges_in_buffer.to_file(path_edges_buffer, driver="GeoJSON")
        st.success("✅ โหลดและบันทึกถนนใน buffer สำเร็จ")

    # ------------------------
    # 4️⃣ ลดรายละเอียด geometry เพื่อความลื่น
    # ------------------------
    edges_in_buffer["geometry"] = edges_in_buffer["geometry"].simplify(0.0001)
    return edges_in_buffer, buffer_geom

# ----------------------------------------------------------
# 🧮 ฟังก์ชันสร้างกราฟ + เส้นทาง
# ----------------------------------------------------------
def build_graph_from_edges(edges_gdf):
    G = nx.DiGraph()
    for _, row in edges_gdf.iterrows():
        geom = row.geometry
        if geom is None:
            continue
        parts = geom.geoms if isinstance(geom, MultiLineString) else [geom]
        for part in parts:
            coords = list(part.coords)
            for i in range(len(coords) - 1):
                u = (coords[i][0], coords[i][1])
                v = (coords[i + 1][0], coords[i + 1][1])
                G.add_edge(u, v, length=haversine_m(u, v))
    return G

def route_to_gdf(G, nodes):
    lines = [LineString([u, v]) for u, v in zip(nodes[:-1], nodes[1:])]
    return gpd.GeoDataFrame({"geometry": lines}, geometry="geometry", crs="EPSG:4326")

# ----------------------------------------------------------
# 🎨 UI Streamlit
# ----------------------------------------------------------
st.set_page_config(page_title="🚗 Highway 4 Path Finder", layout="wide")
st.title("🚗 เส้นทางสั้นสุดในพื้นที่รอบถนนหมายเลข 4")

edges_in_buffer, buffer_geom = load_geo_data()

if "points" not in st.session_state:
    st.session_state["points"] = []
if "route_gdf" not in st.session_state:
    st.session_state["route_gdf"] = None

col_map, col_ctrl = st.columns([3, 1])

# ----------------------------------------------------------
# 🗺️ แผนที่
# ----------------------------------------------------------
with col_map:
    centroid = buffer_geom.centroid
    m = folium.Map(location=[centroid.y, centroid.x], zoom_start=11)

    # buffer
    folium.GeoJson(buffer_geom, style_function=lambda x: {
        "color": "blue", "weight": 2, "fillColor": "#B0E0E6", "fillOpacity": 0.25
    }).add_to(m)

    # # ถนน
    # folium.GeoJson(edges_in_buffer[["geometry"]],
    #                style_function=lambda x: {"color": "#999", "weight": 1}).add_to(m)

    # จุดเริ่มต้น/ปลาย
    icons = [("green", "🚩 จุดเริ่มต้น"), ("red", "🏁 จุดปลายทาง")]
    for i, p in enumerate(st.session_state["points"]):
        color, label = icons[i] if i < 2 else ("blue", f"จุดที่ {i+1}")
        folium.Marker(
            [p["lat"], p["lng"]],
            popup=f"<b>{label}</b>",
            tooltip=label,
            icon=folium.Icon(color=color, icon="map-marker", prefix="fa")
        ).add_to(m)

    # เส้นทาง
    if st.session_state["route_gdf"] is not None:
        for _, row in st.session_state["route_gdf"].iterrows():
            folium.PolyLine([(lat, lon) for lon, lat in row.geometry.coords],
                            color="red", weight=6, opacity=0.9).add_to(m)

    out = st_folium(m, width=900, height=600, key="main_map")

# ----------------------------------------------------------
# 🎛️ ส่วนควบคุม
# ----------------------------------------------------------
with col_ctrl:
    st.subheader("⚙️ การควบคุม")

    if st.button("🗑 ล้างหมุดทั้งหมด"):
        st.session_state["points"] = []
        st.session_state["route_gdf"] = None
        st.experimental_rerun()

    if out and out.get("last_clicked"):
        lat, lon = out["last_clicked"]["lat"], out["last_clicked"]["lng"]
        point = Point(lon, lat)
        dist = edges_in_buffer.distance(point)
        nearest_idx = dist.idxmin()
        nearest_geom = edges_in_buffer.loc[nearest_idx, "geometry"]
        nearest_point = nearest_geom.interpolate(nearest_geom.project(point))
        new_point = {"lat": nearest_point.y, "lng": nearest_point.x}
        if len(st.session_state["points"]) < 2:
            st.session_state["points"].append(new_point)
        else:
            st.warning("ปักได้สูงสุด 2 จุด (เริ่มต้น–ปลายทาง)")
        st.experimental_rerun()

    # แสดงจุดที่เลือก
    if st.session_state["points"]:
        st.markdown("### 📍 จุดที่เลือก")
        for i, p in enumerate(st.session_state["points"]):
            st.write(f"{i+1}. lat = {p['lat']:.5f}, lon = {p['lng']:.5f}")

    # หาเส้นทาง
    if len(st.session_state["points"]) == 2:
        try:
            G = build_graph_from_edges(edges_in_buffer)
            p1, p2 = st.session_state["points"]
            orig = min(G.nodes, key=lambda n: Point(n).distance(Point(p1["lng"], p1["lat"])))
            dest = min(G.nodes, key=lambda n: Point(n).distance(Point(p2["lng"], p2["lat"])))
            route_nodes = nx.shortest_path(G, orig, dest, weight="length", method="dijkstra")
            route_gdf = route_to_gdf(G, route_nodes)
            st.session_state["route_gdf"] = route_gdf
            length = sum(linestring_length_m(g) for g in route_gdf.geometry)
            st.success(f"✅ ระยะทางสั้นสุด {length/1000:.2f} km")
            st.metric("🛣️ ระยะทางรวม", f"{length:.0f} m")
            st.experimental_rerun()
        except nx.NetworkXNoPath:
            st.error("❌ ไม่มีเส้นทางเชื่อมระหว่างจุดทั้งสอง")
        except Exception as e:
            st.error(f"❌ เกิดข้อผิดพลาด: {e}")
