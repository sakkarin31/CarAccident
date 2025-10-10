import requests
from bs4 import BeautifulSoup
from datetime import datetime
import os

# 1. คำนวณปีเป้าหมาย (ปีปัจจุบัน - 1 ใน พ.ศ.)
target_year = datetime.now().year + 543 - 1

# 2. ดึงหน้าเว็บ
url = "https://datagov.mot.go.th/en/dataset/traf62"
response = requests.get(url)
response.raise_for_status()
soup = BeautifulSoup(response.text, 'html.parser')

# 3. ค้นหาลิงก์ CSV สำหรับปีเป้าหมาย
csv_url = None
for item in soup.find_all('li', class_='resource-item'):
    title = item.find('a', class_='heading').get('title', '')
    if f"ปี {target_year}" in title:
        if item.find('span', attrs={'data-format': 'csv'}):
            csv_link_tag = item.find('a', class_='resource-url-analytics')
            if csv_link_tag:
                csv_url = csv_link_tag['href'].strip()
                break

# 4. ดาวน์โหลดถ้าเจอ
if csv_url:
    filename = f"aadt-{str(target_year)[-2:]}.csv"
    with open(filename, 'wb') as f:
        f.write(requests.get(csv_url).content)
    print(f"✅ ดาวน์โหลดและบันทึก: {os.path.abspath(filename)}")
else:
    print(f"❌ ไม่พบไฟล์ CSV สำหรับปี {target_year}")