import streamlit as st
import geopandas as gpd
import networkx as nx
import folium
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium
from shapely.geometry import Point, LineString
import osmnx as ox
import pandas as pd
import numpy as np
import os

# ----------------------------------------
# โหลดข้อมูลถนนและ buffer (เฉพาะถนนหลัก)
# ----------------------------------------
@st.cache_data
def load_geo_data(data_dir="app/data", buffer_m=1000):
    """
    โหลดเฉพาะถนนทางหลวงหมายเลข 4 ทั้งเส้น แล้วกรองเฉพาะส่วนที่อยู่ในสงขลา
    จากนั้นสร้าง buffer รอบเส้นทางนั้นเท่านั้น
    """
    import shapely
    os.makedirs(data_dir, exist_ok=True)

    path_edges4 = os.path.join(data_dir, "edges_highway4.geojson")
    path_buffer = os.path.join(data_dir, f"buffer_{buffer_m}m.geojson")

    # -------------------------------
    # 1️⃣ โหลดหรือดึงถนนสาย 4 ใหม่จาก OSM
    # -------------------------------
    if os.path.exists(path_edges4):
        try:
            edges_4 = gpd.read_file(path_edges4)
            if edges_4.empty:
                raise ValueError("ไฟล์ edges_highway4.geojson ว่างเปล่า")
            st.info("📂 ใช้ข้อมูลทางหลวงหมายเลข 4 จาก cache เดิม")
        except Exception:
            st.warning("⚠️ ไฟล์ถนนสาย 4 เสียหาย กำลังโหลดใหม่จาก OSM...")
            edges_4 = None
    else:
        edges_4 = None

    if edges_4 is None:
        try:
            st.info("🌐 กำลังโหลดข้อมูลทางหลวงหมายเลข 4 ทั้งเส้นจาก OSM...")
            # โหลดจากประเทศไทยทั้งหมด (ไม่เฉพาะจังหวัด)
            G = ox.graph_from_place(
                "Thailand",
                network_type="drive",
                simplify=True,
                custom_filter='["highway"~"motorway|trunk|primary|secondary"]'
            )
            edges = ox.graph_to_gdfs(G, nodes=False, edges=True)
            edges = edges.to_crs(epsg=4326)

            # 🔹 กรองเฉพาะ ref = 4 (ถนนหมายเลข 4)
            edges["ref"] = edges["ref"].astype(str)
            edges_4 = edges[edges["ref"].str.contains(r"(^|;)4($|;)", na=False)].copy()

            if edges_4.empty:
                st.warning("⚠️ ไม่พบถนนทางหลวงหมายเลข 4 ใน OSM ประเทศไทย")
            else:
                edges_4.to_file(path_edges4, driver="GeoJSON")
                st.success("✅ โหลดถนนทางหลวงหมายเลข 4 ทั้งเส้นเรียบร้อยแล้ว")

        except Exception as e:
            st.error(f"❌ โหลดถนนสาย 4 ไม่สำเร็จ: {e}")
            st.stop()

    # -------------------------------
    # 2️⃣ กรองเฉพาะช่วงที่อยู่ในจังหวัดสงขลา
    # -------------------------------
    try:
        st.info("📍 กำลังกรองเฉพาะช่วงของถนนสาย 4 ที่อยู่ในสงขลา...")
        province_gdf = ox.geocode_to_gdf("Songkhla Province, Thailand")
        province_geom = province_gdf.to_crs(epsg=4326).geometry.unary_union

        edges_4 = edges_4[edges_4.intersects(province_geom)].copy()
        if edges_4.empty:
            st.warning("⚠️ ไม่พบช่วงถนนสาย 4 ในพื้นที่สงขลา")
    except Exception as e:
        st.error(f"❌ ไม่สามารถกรองพื้นที่สงขลาได้: {e}")
        st.stop()

    # -------------------------------
    # 3️⃣ สร้าง buffer รอบสาย 4 เท่านั้น
    # -------------------------------
    st.info("🧭 สร้าง buffer รอบทางหลวงหมายเลข 4 เท่านั้น...")

    # กรองเฉพาะเส้นที่ ref = 4 เป๊ะ ๆ หรือชื่อเพชรเกษม
    edges_4 = edges_4[edges_4["ref"].astype(str).str.fullmatch(r"4", case=False, na=False) |
                    edges_4["name"].astype(str).str.contains("เพชรเกษม", na=False)]

    # หากยังมีถนนอื่นหลงมา ให้ละทิ้ง geometry ที่อยู่ไกลจากเส้นหลัก
    edges_4 = edges_4.to_crs(epsg=3857)
    centroid = edges_4.geometry.unary_union.centroid
    edges_4["dist_center"] = edges_4.distance(centroid)
    edges_4 = edges_4[edges_4["dist_center"] < 200000]  # เฉพาะเส้นในรัศมี 200 กม. รอบศูนย์กลาง

    # รวมเป็นเส้นเดียวและสร้าง buffer รอบเส้นนั้นเท่านั้น
    line_union = edges_4.geometry.unary_union
    buffer_geom = gpd.GeoSeries([line_union.buffer(buffer_m)], crs="EPSG:3857").simplify(50)
    buffer_gdf = buffer_geom.to_crs(epsg=4326)

    buffer_gdf.to_file(path_buffer, driver="GeoJSON")
    st.success("✅ สร้าง buffer รอบถนนเพชรเกษม (สาย 4) สำเร็จแล้ว")

    buffer_geom = buffer_gdf.unary_union

    # -------------------------------
    # 4️⃣ ลดรายละเอียด geometry และคืนค่า
    # -------------------------------
    edges_in_buffer = edges_4[edges_4.intersects(buffer_geom)].copy()
    edges_in_buffer = edges_in_buffer.reset_index(drop=True)
    edges_in_buffer["geometry"] = edges_in_buffer["geometry"].simplify(0.0001)

    return edges_in_buffer, buffer_geom

# ----------------------------------------
# สร้างกราฟจาก GeoDataFrame (ไม่มี MultiIndex)
# ----------------------------------------
def build_graph_from_edges(edges_gdf):
    G = nx.DiGraph()
    for idx, row in edges_gdf.iterrows():
        geom = row.geometry
        if geom is None or geom.geom_type != "LineString":
            continue
        coords = list(geom.coords)
        u = coords[0]
        v = coords[-1]
        length = geom.length * 111000  # แปลง degree → m โดยประมาณ
        G.add_edge(u, v, length=length, geometry=geom, highway=row.get("highway", "unknown"))
    return G


# ----------------------------------------
# แปลงเส้นทางเป็น GeoDataFrame
# ----------------------------------------
def route_to_gdf(G, nodes):
    rows, geoms = [], []
    for u, v in zip(nodes[:-1], nodes[1:]):
        data = G.get_edge_data(u, v)
        if not data:
            continue
        geom = data.get("geometry", LineString([u, v]))
        rows.append(data)
        geoms.append(geom)
    return gpd.GeoDataFrame(rows, geometry=geoms, crs="EPSG:4326")


# ----------------------------------------
# คำนวณความเสี่ยงจำลอง (สมมติจากระยะทาง)
# ----------------------------------------
def predict_accident_risk(length_m, hw_type):
    risk_base = {"motorway": 0.10, "trunk": 0.08, "primary": 0.06, "secondary": 0.04}
    return risk_base.get(hw_type, 0.03) * (length_m / 1000)


def summarize_route(route_gdf):
    if route_gdf.empty:
        return 0, 0
    route_gdf = route_gdf.to_crs(epsg=3857)
    total_len = route_gdf.length.sum()
    total_risk = 0
    for _, row in route_gdf.iterrows():
        length_m = row.geometry.length
        hw_type = row.get("highway", "primary")
        total_risk += predict_accident_risk(length_m, hw_type)
    return total_len, total_risk


# ----------------------------------------
# วาดเส้นทางบนแผนที่
# ----------------------------------------
def draw_route(m, route_gdf):
    for _, row in route_gdf.iterrows():
        geom = row.geometry
        folium.PolyLine([(y, x) for x, y in geom.coords],
                        color="red", weight=6, opacity=0.9).add_to(m)


# ----------------------------------------
# Streamlit UI
# ----------------------------------------
st.set_page_config(page_title="🚗 Highway 4 Path", layout="wide")
st.title("🚗 เส้นทางสั้นสุดภายในพื้นที่ Buffer ถนนสาย 4")

edges_in_buffer, buffer_geom = load_geo_data(buffer_m=1000)

if "points" not in st.session_state:
    st.session_state["points"] = []
if "route_gdf" not in st.session_state:
    st.session_state["route_gdf"] = None

col_map, col_ctrl = st.columns([3, 1])

# ---------------- MAP ----------------
with col_map:
    m = folium.Map(location=[7.1, 100.6], zoom_start=11)

    # buffer
    folium.GeoJson(buffer_geom, style_function=lambda x: {
        "color": "blue", "weight": 2, "fillColor": "lightblue", "fillOpacity": 0.2
    }).add_to(m)

    # ถนน (ลดขนาด geometry เพื่อความเร็ว)
    folium.GeoJson(edges_in_buffer[["geometry"]].simplify(0.0001),
                   style_function=lambda x: {"color": "gray", "weight": 2}).add_to(m)

    # หมุด
    for i, p in enumerate(st.session_state["points"]):
        folium.Marker([p["lat"], p["lng"]],
                      popup=f"Point {i+1}", tooltip=f"Point {i+1}").add_to(m)

    # เส้นทาง
    if st.session_state["route_gdf"] is not None:
        draw_route(m, st.session_state["route_gdf"])

    out = st_folium(m, width=900, height=600, key="main_map")


# ---------------- CONTROLS ----------------
with col_ctrl:
    st.subheader("⚙️ การควบคุม")

    if st.button("🗑 ล้างหมุดทั้งหมด"):
        st.session_state["points"] = []
        st.session_state["route_gdf"] = None
        st.rerun()

    # เมื่อคลิกบนแผนที่
    if out and out.get("last_clicked"):
        lat, lon = out["last_clicked"]["lat"], out["last_clicked"]["lng"]
        point = Point(lon, lat)

        # ✅ หาจุดบนถนนที่ใกล้ที่สุด
        dist = edges_in_buffer.distance(point)
        nearest_idx = dist.idxmin()
        nearest_geom = edges_in_buffer.loc[nearest_idx, "geometry"]
        nearest_point = nearest_geom.interpolate(nearest_geom.project(point))

        # ตรวจว่าอยู่ใน buffer
        if nearest_point.within(buffer_geom):
            new_point = {"lat": nearest_point.y, "lng": nearest_point.x}
            if new_point not in st.session_state["points"]:
                if len(st.session_state["points"]) < 2:
                    st.session_state["points"].append(new_point)
                else:
                    st.warning("ปักได้สูงสุด 2 จุดเท่านั้น")
                st.rerun()
        else:
            st.warning("⚠️ กรุณาปักหมุดภายใน buffer")

    # เมื่อมีหมุด 2 จุด -> หาเส้นทาง
    if len(st.session_state["points"]) == 2:
        try:
            G = build_graph_from_edges(edges_in_buffer)
            p1, p2 = st.session_state["points"]
            # หาโหนดต้นปลายที่ใกล้ที่สุดในกราฟ
            orig = min(G.nodes, key=lambda n: Point(n).distance(Point(p1["lng"], p1["lat"])))
            dest = min(G.nodes, key=lambda n: Point(n).distance(Point(p2["lng"], p2["lat"])))
            route_nodes = nx.shortest_path(G, orig, dest, weight="length")

            route_gdf = route_to_gdf(G, route_nodes)
            st.session_state["route_gdf"] = route_gdf

            total_len, total_risk = summarize_route(route_gdf)
            st.success(f"✅ ระยะทางสั้นสุด: {total_len/1000:.2f} km")
            st.metric("🛣️ ระยะทางรวม", f"{total_len:.0f} m")
            st.metric("⚠️ ความเสี่ยงอุบัติเหตุรวม (จำลอง)", f"{total_risk*100:.2f}%")
            st.rerun()

        except Exception as e:
            st.error(f"❌ ไม่สามารถหาเส้นทางได้: {e}")
