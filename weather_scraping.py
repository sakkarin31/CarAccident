import time
import csv
from datetime import date, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# Setup Chrome driver
options = webdriver.ChromeOptions()
options.add_argument("--headless=new")
options.add_argument("--disable-gpu")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options
)

# ⚠️ แก้ URL: ลบ space ต่อท้าย!
url = "https://www.wunderground.com/history/daily/th/mueang-songkhla/VTSH"
driver.get(url)

wait = WebDriverWait(driver, 15)

# เปิดไฟล์ CSV และเขียน header — มี wind_speed_kmh ตามต้องการ
with open("songkhla_weather_2024.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["date", "time", "temperature_F", "humidity_%", "wind_speed_kmh", "pressure_in", "condition"])

    # กำหนดช่วงวันที่ (ปรับได้ตามต้องการ)
    start = date(2024, 1, 1)
    end = date(2024, 12, 31)

    # 👇 สร้าง mapping สำหรับเดือนเป็นข้อความภาษาอังกฤษ
    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]

    while start <= end:
        print(f"Fetching: {start}")

        try:
            # รอและเลือกปี
            year_dropdown = wait.until(EC.presence_of_element_located((By.ID, "yearSelection")))
            Select(year_dropdown).select_by_visible_text(str(start.year))

            # รอและเลือกเดือน — ใช้ชื่อเดือนเต็ม เช่น "January"
            month_dropdown = wait.until(EC.presence_of_element_located((By.ID, "monthSelection")))
            Select(month_dropdown).select_by_visible_text(month_names[start.month - 1])  # 👈 แก้ตรงนี้!

            # รอและเลือกวัน
            day_dropdown = wait.until(EC.presence_of_element_located((By.ID, "daySelection")))
            Select(day_dropdown).select_by_visible_text(str(start.day))

            # รอและคลิกปุ่ม View
            view_button = wait.until(EC.element_to_be_clickable((By.ID, "dateSubmit")))
            view_button.click()

            # รอให้ตารางโหลด — รอแถวแรกใน tbody
            wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table tbody tr")))

            # ดึงข้อมูลจากตาราง
            rows = driver.find_elements(By.CSS_SELECTOR, "table tbody tr")
            if not rows:
                print(f"⚠️ ไม่พบข้อมูลสำหรับวันที่ {start}")
            else:
                for r in rows:
                    cols = r.find_elements(By.TAG_NAME, "td")
                    if len(cols) >= 10:
                        time_str = cols[0].text.strip()
                        temp = cols[1].text.replace(" °F", "").strip()
                        humidity = cols[3].text.replace(" %", "").strip()
                        # 👇 ลบหน่วย "km/h" และ "mph" ออก — เหลือแต่ตัวเลข
                        wind_speed = cols[5].text.replace(" km/h", "").replace(" mph", "").strip()
                        pressure = cols[7].text.replace(" in", "").strip()
                        condition = cols[9].text.strip()

                        # 👇 ใช้รูปแบบวันที่ M/D/YYYY สำหรับ Windows
                        formatted_date = f"{start.month}/{start.day}/{start.year}"

                        writer.writerow([
                            formatted_date,
                            time_str,
                            temp,
                            humidity,
                            wind_speed,
                            pressure,
                            condition
                        ])

                print(f"✅ บันทึกข้อมูลสำหรับวันที่ {start}")

        except (TimeoutException, NoSuchElementException) as e:
            print(f"❌ ข้อผิดพลาดขณะดึงข้อมูลวันที่ {start}: {str(e)}")

        # ไปวันถัดไป
        start += timedelta(days=1)
        time.sleep(1)  # รอ 1 วินาที ลดโหลดเซิร์ฟเวอร์

# ปิดเบราว์เซอร์
driver.quit()
print("✅ Done. Saved -> songkhla_weather_2024.csv")