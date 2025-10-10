import requests
from bs4 import BeautifulSoup
from datetime import datetime
import os

# 1. คำนวณปี พ.ศ. ปัจจุบัน และปีเป้าหมาย (ปีก่อนหน้า)
current_year_be = datetime.now().year + 543  # เช่น 2025 → 2568
target_year_be = current_year_be - 1        # → 2567

# 2. URL ของชุดข้อมูลอุบัติเหตุ
url = "https://datagov.mot.go.th/dataset/roadaccident"
response = requests.get(url)
response.raise_for_status()
soup = BeautifulSoup(response.text, 'html.parser')

# 3. ค้นหา resource ที่ตรงกับ "อุบัติเหตุทางถนน ปี{target_year_be}" และเป็น CSV
accident_url = None
for item in soup.find_all('li', class_='resource-item'):
    heading = item.find('a', class_='heading')
    if not heading:
        continue

    title = heading.get('title', '').strip()
    # ตรวจสอบรูปแบบ: "อุบัติเหตุทางถนน ปี2567" (ไม่มีช่องว่างหลัง "ปี")
    if f"อุบัติเหตุทางถนน ปี{target_year_be}" in title:
        if item.find('span', attrs={'data-format': 'csv'}):
            download_tag = item.find('a', class_='resource-url-analytics')
            if download_tag and download_tag.get('href'):
                accident_url = download_tag['href'].strip()
                break

# 4. ดาวน์โหลดไฟล์
if accident_url:
    filename = f"accident-{str(target_year_be)[-2:]}.csv"  # เช่น accident-67.csv
    try:
        print(f"กำลังดาวน์โหลดข้อมูลอุบัติเหตุปี {target_year_be}...")
        data = requests.get(accident_url).content
        with open(filename, 'wb') as f:
            f.write(data)
        print(f"✅ บันทึกไฟล์เรียบร้อย: {os.path.abspath(filename)}")
    except Exception as e:
        print(f"❌ ดาวน์โหลดล้มเหลว: {e}")
else:
    print(f"❌ ไม่พบข้อมูลอุบัติเหตุทางถนนปี {target_year_be} ในรูปแบบ CSV")