# 🚗 CarAccident — Road Accident Risk Analysis & Forecasting (Hat Yai / Songkhla)

> **ระบบวิเคราะห์และพยากรณ์ความเสี่ยงอุบัติเหตุทางถนน จังหวัดสงขลา (อ.หาดใหญ่)**  
> Road Accident Risk Analysis & Forecasting System for Songkhla Province, Thailand

---

## 📌 ภาพรวมโปรเจค / Project Overview

**ภาษาไทย:**  
โปรเจคนี้พัฒนาระบบ End-to-End สำหรับวิเคราะห์และพยากรณ์ความเสี่ยงอุบัติเหตุทางถนนในพื้นที่จังหวัดสงขลา (โดยเฉพาะอำเภอหาดใหญ่) โดยรวบรวมข้อมูลอุบัติเหตุระหว่างปี 2020–2025 มาผสานกับข้อมูลสภาพอากาศและปริมาณรถยนต์ จากนั้นใช้โมเดล Machine Learning (LSTM, XGBoost, SARIMAX) ในการพยากรณ์ความเสี่ยง พร้อม Dashboard แบบ Interactive สำหรับแสดงผล

**English:**  
This project builds an End-to-End pipeline to analyze and forecast road accident risk in Songkhla Province (Hat Yai district), Thailand. It integrates accident records from 2020–2025 with weather and traffic volume data, trains ML models (LSTM, XGBoost, SARIMAX) for risk forecasting, and presents results via an interactive Streamlit dashboard.

---

## 🗂️ โครงสร้างโปรเจค / Project Structure

```
CarAccident/
│
├── airflow/                    # ETL Pipeline (Apache Airflow + Spark)
│   ├── dags/                   # DAG definitions & task scripts
│   │   ├── pipeline.py
│   │   ├── tasks/              # extract, transform, load, forecast tasks
│   │   └── data/               # raw data input for DAGs
│   ├── model/                  # Trained models for Airflow pipeline
│   ├── spark/                  # PySpark jobs
│   ├── docker-compose.yml      # Docker setup (Airflow + PostgreSQL + Streamlit)
│   ├── Dockerfile
│   └── requirements.txt
│
├── app/                        # Streamlit Web Application
│   ├── app.py                  # Main app (route risk map)
│   ├── app_one.py              # Alternative version
│   ├── app_songkla.py          # Songkhla-specific version
│   ├── Dockerfile
│   └── requirements.txt
│
├── dataset/                    # Data preparation & EDA
│   ├── USE1claenallcar.py      # Step 1: Clean accident data
│   ├── USE1cleanser_acccar.py  # Step 1: Clean accident + vehicle data
│   ├── USE1cleanweather.py     # Step 1: Clean weather data
│   ├── USE2mergeaccandweather.py # Step 2: Merge accident & weather
│   ├── USE3addcardata.py       # Step 3: Add vehicle count data
│   ├── USE4finaldata.py        # Step 4: Finalize dataset
│   ├── USEeda_data.ipynb       # Exploratory Data Analysis
│   ├── aadt-*.csv              # Annual Average Daily Traffic (AADT) data
│   └── cleandaily-*.csv        # Cleaned daily datasets
│
├── lstm/                       # LSTM Model training
│   ├── lstm_model.py
│   └── lstm_train_model.ipynb
│
├── demo/                       # Demo & Inference
│   ├── app.py                  # Demo Streamlit app
│   ├── demo.ipynb              # Demo notebook
│   ├── arimaxtest.ipynb        # SARIMAX/ARIMAX experiments
│   ├── inference_next.py       # Inference script (LSTM Multi-Task)
│   └── model/                  # Trained demo models (.pth, .pkl, .json)
│
├── weather_report/             # Weather LSTM experiments
│   ├── lstm_weather.h5
│   └── test.ipynb
│
├── alldata-notclean/           # Raw accident data (2020–2025)
├── weather_scraping.py         # Weather data scraper (Selenium)
├── route_export.geojson        # Road network GeoJSON (Songkhla)
└── requirements.txt            # Root-level dependencies
```

---

## ⚙️ เทคโนโลยีที่ใช้ / Tech Stack

| Category | Tools |
|---|---|
| **Data Pipeline** | Apache Airflow 2.9, PySpark, PostgreSQL |
| **Machine Learning** | PyTorch (LSTM), TensorFlow/Keras, XGBoost, LightGBM, Scikit-learn |
| **Time Series** | SARIMAX / ARIMAX (statsmodels), Prophet |
| **Data Processing** | Pandas, NumPy |
| **Geospatial** | GeoPandas, OSMnx, NetworkX, Folium, Shapely |
| **Web Dashboard** | Streamlit, streamlit-folium |
| **Web Scraping** | Selenium, BeautifulSoup |
| **Infrastructure** | Docker, Docker Compose |
| **Visualization** | Matplotlib, Seaborn, Plotly |

---

## 🧠 โมเดล ML / ML Models

### 1. LSTM Multi-Task (PyTorch)
- **Input:** Sequence ของ features 24 ชั่วโมงย้อนหลัง (อุณหภูมิ, ความชื้น, ความเร็วลม, ฯลฯ)
- **Output:** 
  - Classification head → ความน่าจะเป็นของการเกิดอุบัติเหตุ (Binary)
  - Regression head → ค่าพยากรณ์เชิงปริมาณ 3 ตัว
- **Variants:** `lstm_best_model.pth`, `lstm_best_model_mt.pth`, `lstm_best_model_mt_6h.pth`

### 2. XGBoost
- ใช้ใน Airflow pipeline สำหรับพยากรณ์แบบ Near Real-Time
- Scaler: `Scaler_Round_2_Window=3.pkl`

### 3. SARIMAX / ARIMAX
- สำหรับพยากรณ์ Time Series รายวัน/รายสัปดาห์
- ทดสอบใน `demo/arimaxtest.ipynb`

---

## 🔄 Data Pipeline (ETL)

Pipeline ทำงานผ่าน Apache Airflow โดยมีขั้นตอนดังนี้:

```
[Scrape Weather Data] → [Extract CSV] → [Clean & Transform]
        ↓
[Merge Accident + Weather + Traffic Volume]
        ↓
[Spark Processing] → [Load to PostgreSQL]
        ↓
[Forecast Task] → [Streamlit Dashboard]
```

**DAG Tasks:**
- `extract_task.py` — ดึงข้อมูลจากไฟล์ CSV
- `transform_task.py` — ทำความสะอาดและแปลงข้อมูล
- `load_task.py` — โหลดเข้า PostgreSQL
- `forecast_task.py` — รันโมเดลพยากรณ์
- `check_file_task.py` — ตรวจสอบไฟล์ input

---

## 🗃️ ข้อมูลที่ใช้ / Datasets

| Dataset | ช่วงเวลา | รายละเอียด |
|---|---|---|
| ข้อมูลอุบัติเหตุ | 2020–2025 | จากกรมทางหลวง / แหล่งข้อมูลราชการ |
| ข้อมูลอากาศ Songkhla | 2020–2024 | อุณหภูมิ, ความชื้น, ความเร็วลม, ความกดอากาศ |
| AADT (ปริมาณรถ) | 2563–2567 | Annual Average Daily Traffic รายปี |
| Road Network | - | OSM (OpenStreetMap) ผ่าน OSMnx |

---

## 🚀 วิธีติดตั้งและรัน / Installation & Setup

### Prerequisites
- Docker & Docker Compose
- Python 3.10+

### 1. Clone repository

```bash
git clone https://github.com/sakkarin31/CarAccident.git
cd CarAccident
```

### 2. รัน Airflow + PostgreSQL + Streamlit ด้วย Docker

```bash
cd airflow

# Initialize Airflow (ครั้งแรก)
docker compose up airflow-init

# รัน services ทั้งหมด
docker compose up -d
```

| Service | URL |
|---|---|
| Airflow Web UI | http://localhost:8080 (admin / admin) |
| Streamlit Dashboard | http://localhost:8501 |
| PostgreSQL | localhost:5433 |

### 3. รัน Streamlit App แบบ Local (ไม่ใช้ Docker)

```bash
pip install -r app/requirements.txt
cd app
streamlit run app_songkla.py
```

### 4. เทรนโมเดล LSTM

```bash
pip install -r requirements.txt
cd lstm
jupyter notebook lstm_train_model.ipynb
```

### 5. รัน Inference

```bash
cd demo
python inference_next.py
```

---

## 📊 Features ของ Dashboard

- 🗺️ **Risk Map** — แสดงระดับความเสี่ยงบนเส้นทางถนนในหาดใหญ่แบบ Interactive (Folium)
- 📈 **Time Series Forecast** — พยากรณ์จำนวนอุบัติเหตุล่วงหน้ารายวัน/รายสัปดาห์
- 🌦️ **Weather Integration** — แสดงความสัมพันธ์ระหว่างสภาพอากาศกับอุบัติเหตุ
- 🛣️ **Route Analysis** — วิเคราะห์เส้นทางที่มีความเสี่ยงสูง

---

## 👥 ทีมพัฒนา / Team

**Team Caramujo**

---

## 📄 License

This project is for academic purposes.
