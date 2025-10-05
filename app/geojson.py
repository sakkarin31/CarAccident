import osmnx as ox
import geopandas as gpd
import os

os.makedirs("data", exist_ok=True)

# โหลดถนนทั้งหมด
G = ox.graph_from_place(
    "Songkhla Province, Thailand",
    network_type="drive",
    simplify=True,
    custom_filter='["highway"~"motorway|trunk|primary|secondary|tertiary|unclassified|residential"]'
)
edges = ox.graph_to_gdfs(G, nodes=False, edges=True)

# กรองถนนสาย 4
edges["ref"] = edges["ref"].astype(str)
edges_4 = edges[edges["ref"].str.contains(r"(^|;)4($|;)", na=False)].copy()
if edges_4.empty:
    edges["name"] = edges["name"].astype(str)
    edges_4 = edges[edges["name"].str.contains("เพชรเกษม", na=False)].copy()

# สร้าง buffer รอบสาย 4 (1 km)
edges_4 = edges_4.to_crs(epsg=3857)
buffer_union = edges_4.buffer(1000).unary_union
buffer = gpd.GeoSeries([buffer_union], crs="EPSG:3857").to_crs(epsg=4326)

# บันทึกเป็นไฟล์ GeoJSON
edges.to_file("data/edges_all.geojson", driver="GeoJSON")
edges_4.to_file("data/edges_highway4.geojson", driver="GeoJSON")
buffer.to_file("data/buffer_1000m.geojson", driver="GeoJSON")

print("✅ สร้างไฟล์ GeoJSON เรียบร้อยแล้ว!")
