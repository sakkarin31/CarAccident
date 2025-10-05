import streamlit as st
import osmnx as ox
import geopandas as gpd
import networkx as nx
import folium
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium
from shapely.geometry import Point, LineString
import numpy as np
import pandas as pd
import matplotlib.colors as mcolors
import os

# ---------------------------
# โหลดข้อมูลจากไฟล์ GeoJSON ที่มีอยู่ (ไม่ดึงจาก OSM)
# ---------------------------
def load_from_geojson(data_dir="data", buffer_m=1000):
    path_edges = os.path.join(data_dir, "edges_all.geojson")
    path_edges4 = os.path.join(data_dir, "edges_highway4.geojson")
    path_buffer = os.path.join(data_dir, f"buffer_{buffer_m}m.geojson")

    # ตรวจสอบว่าไฟล์ครบหรือไม่
    missing = [p for p in [path_edges, path_edges4, path_buffer] if not os.path.exists(p)]
    if missing:
        st.error("❌ ไม่พบไฟล์ GeoJSON ครบชุด กรุณาใส่ไฟล์ในโฟลเดอร์ 'data/'")
        st.stop()

    # โหลดข้อมูลจากไฟล์ GeoJSON
    edges = gpd.read_file(path_edges)
    edges_4 = gpd.read_file(path_edges4)
    buffer_gdf = gpd.read_file(path_buffer)

    # ตรวจสอบ CRS
    if edges.crs is None:
        edges.set_crs(epsg=4326, inplace=True)
    if edges_4.crs is None:
        edges_4.set_crs(epsg=4326, inplace=True)
    if buffer_gdf.crs is None:
        buffer_gdf.set_crs(epsg=4326, inplace=True)

    return edges, edges_4, buffer_gdf

# ---------------------------
# โหลดเฉพาะถนนทางหลวงหมายเลข 4
# ---------------------------
@st.cache_data
def load_highway4_graph():
    try:
        # 🔹 โหลดถนนหลักทั้งหมดในสงขลา
        G = ox.graph_from_place(
            "Songkhla Province, Thailand",
            network_type="drive",
            simplify=True,
            custom_filter='["highway"~"motorway|trunk|primary"]'
        )

        # 🔹 แปลงเป็น GeoDataFrame
        edges = ox.graph_to_gdfs(G, nodes=False, edges=True)
        if edges.empty:
            st.warning("⚠️ ไม่มีข้อมูลถนนในพื้นที่นี้")
            return None, None

        # 🔹 กรองเฉพาะถนนที่ ref = 4 เป๊ะ ๆ เท่านั้น
        edges["ref"] = edges["ref"].astype(str)
        edges_4 = edges[edges["ref"].str.contains(r"(^|;)4($|;)", na=False)].copy()

        # ถ้ายังไม่มีข้อมูล ลองเช็กชื่อถนนเผื่อบางช่วงไม่ได้ใส่ ref
        if edges_4.empty:
            edges["name"] = edges["name"].astype(str)
            edges_4 = edges[edges["name"].str.contains("เพชรเกษม", na=False)].copy()

        if edges_4.empty:
            st.warning("⚠️ ไม่พบถนนทางหลวงหมายเลข 4 ใน Songkhla")
            return G, None

        edges_4 = edges_4.to_crs(epsg=4326)
        return G, edges_4

    except Exception as e:
        st.error(f"❌ โหลดกราฟไม่สำเร็จ: {e}")
        return None, None

    
# ---------------------------
# ฟังก์ชันวาด buffer รอบสาย 4
# ---------------------------
def draw_highway4_buffer_only(m, edges_4, buffer_m=1000, simplify_tol=50):
    try:
        if edges_4 is None or edges_4.empty:
            st.warning("⚠️ ไม่มีข้อมูลถนนสาย 4 สำหรับสร้าง buffer")
            return None

        if edges_4.crs is None:
            edges_4 = edges_4.set_crs(epsg=4326)

        gdf_proj = edges_4.to_crs(epsg=3857)
        buffer_union = gdf_proj.buffer(buffer_m).unary_union
        buffer_simplified = gpd.GeoSeries([buffer_union], crs="EPSG:3857").simplify(simplify_tol)
        buffer_wgs = buffer_simplified.to_crs(epsg=4326)

        folium.GeoJson(
            buffer_wgs.__geo_interface__,
            name="Highway 4 Buffer",
            style_function=lambda x: {
                "color": "blue",
                "weight": 2,
                "fillColor": "lightblue",
                "fillOpacity": 0.2,
            },
        ).add_to(m)

        return buffer_wgs.iloc[0]

    except Exception as e:
        st.error(f"❌ โหลดขอบเขตไม่สำเร็จ: {e}")
        return None

# ---------------------------
# ฟังก์ชันช่วยเหลือ
# ---------------------------
def simplify_graph(G):
    H = nx.DiGraph()
    for u, v, data in G.edges(data=True):
        length = data.get("length", 1)
        if H.has_edge(u, v):
            if length < H[u][v]["length"]:
                H[u][v].update(data)
        else:
            H.add_edge(u, v, **data)
    return H

def route_to_gdf(G, nodes):
    edges = []
    geoms = []
    for u, v in zip(nodes[:-1], nodes[1:]):
        if G.has_edge(u, v):
            data = min(G.get_edge_data(u, v).values(), key=lambda d: d.get("length", 1))
            edges.append(data)
            geom = data.get("geometry", LineString([
                (G.nodes[u]["x"], G.nodes[u]["y"]),
                (G.nodes[v]["x"], G.nodes[v]["y"])
            ]))
            geoms.append(geom)
    if not edges:
        return gpd.GeoDataFrame()
    return gpd.GeoDataFrame(edges, geometry=geoms, crs="EPSG:4326")

def get_route_length_meters(gdf):
    if gdf.empty:
        return 0
    gdf = gdf.to_crs(epsg=3857)
    return gdf.length.sum()

def predict_risk(ref):
    return np.random.uniform(0, 10)

def calc_weighted_risk(route_gdf):
    if route_gdf.empty:
        return 0.0
    gdf_proj = route_gdf.to_crs(epsg=3857)
    total_len = gdf_proj.length.sum()
    if total_len == 0:
        return 0.0
    risk_sum = 0.0
    for _, row in gdf_proj.iterrows():
        hw_type = row.get("highway", "")
        if isinstance(hw_type, list):
            hw_type = hw_type[0] if hw_type else ""
        seg_len = row.geometry.length
        if hw_type in {"trunk", "primary"}:
            ref = row.get("ref", "unknown")
            risk = predict_risk(ref)
            risk_sum += risk * seg_len
    return risk_sum / total_len

def draw_route_colored(m, gdf):
    if gdf is None or gdf.empty:
        return
    for _, row in gdf.iterrows():
        geom = row.geometry
        if geom is None:
            continue
        hw_type = row.get("highway", "")
        if isinstance(hw_type, list):
            hw_type = hw_type[0] if hw_type else ""
        color = "gray"
        if hw_type in {"motorway", "trunk", "primary"}:
            ref = row.get("ref", "unknown")
            risk = predict_risk(ref)
            norm_risk = min(max(risk / 10, 0), 1)
            rgba = mcolors.to_rgba("red", alpha=0.3 + 0.7 * norm_risk)
            color = mcolors.to_hex(rgba)
        folium.PolyLine(
            [(y, x) for x, y in row.geometry.coords],
            color=color, weight=5, opacity=0.9,
            popup=f"{hw_type or 'non-highway'}"
        ).add_to(m)

# ---------------------------
# Streamlit UI
# ---------------------------
st.set_page_config(page_title="🚗 Highway 4 (Songkhla)", layout="wide")
st.title("🚗 วิเคราะห์ความเสี่ยงบนทางหลวงหมายเลข 4 (Songkhla)")

# โหลดข้อมูลจากไฟล์ GeoJSON
edges, edges_4, buffer_gdf = load_from_geojson(buffer_m=1000)
buffer_geom = buffer_gdf.iloc[0] if buffer_gdf is not None else None

if edges is None or edges.empty or buffer_geom is None:
    st.error("❌ ไม่สามารถโหลดข้อมูลถนนหรือ buffer ได้")
else:
    if "points" not in st.session_state:
        st.session_state["points"] = []

    col_map, col_ctrl = st.columns([3, 1])

    with col_map:
        m = folium.Map(location=[7.1, 100.6], zoom_start=11)

        # ✅ แสดงขอบเขต buffer รอบสาย 4
        folium.GeoJson(
            buffer_gdf,
            name="Highway 4 Buffer",
            style_function=lambda x: {
                "color": "blue",
                "weight": 2,
                "fillColor": "lightblue",
                "fillOpacity": 0.2,
            },
        ).add_to(m)

        # ✅ วาดถนนทั้งหมด (ภายใน buffer)
        edges_in_buffer = edges[edges.intersects(buffer_geom)]
        if not edges_in_buffer.empty:
            folium.GeoJson(
                edges_in_buffer,
                style_function=lambda x: {"color": "gray", "weight": 2},
                name="Roads in Buffer"
            ).add_to(m)

        # ✅ แสดงหมุดที่ผู้ใช้ปักแล้ว
        marker_cluster = MarkerCluster().add_to(m)
        for i, p in enumerate(st.session_state["points"]):
            folium.Marker(
                [p["lat"], p["lng"]],
                popup=f"Point {i+1}",
                tooltip=f"Point {i+1}"
            ).add_to(marker_cluster)

        # ✅ ตรวจสอบการคลิกแผนที่
        out = st_folium(m, width=900, height=600, key="main_map")

    with col_ctrl:
        st.subheader("⚙️ Controls")

        if st.button("🗑 ล้างหมุดทั้งหมด"):
            st.session_state["points"] = []
            st.rerun()

        # ✅ เมื่อคลิกบนแผนที่
        if out and out.get("last_clicked"):
            raw = out["last_clicked"]
            lat, lon = raw["lat"], raw["lng"]

            # ตรวจสอบว่าจุดอยู่ใน buffer หรือไม่
            if Point(lon, lat).within(buffer_geom):
                new_point = {"lat": lat, "lng": lon}
                if new_point not in st.session_state["points"]:
                    st.session_state["points"].append(new_point)
                    st.rerun()
            else:
                st.warning("⚠️ กรุณาปักหมุดภายในพื้นที่ buffer รอบถนนสาย 4 เท่านั้น")

        # ✅ แสดงข้อมูลหมุดที่ปักแล้ว
        if st.session_state["points"]:
            st.markdown("**📍 หมุดที่เลือก:**")
            df_points = pd.DataFrame(st.session_state["points"])
            df_points.index = [f"Point {i+1}" for i in range(len(df_points))]
            st.dataframe(df_points)
