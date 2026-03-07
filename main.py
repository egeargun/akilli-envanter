from fastapi import FastAPI
from pydantic import BaseModel # YENİ EKLENDİ: Kaan'dan gelen veriyi okumak için
import pymysql

app = FastAPI()

from fastapi import FastAPI
from pydantic import BaseModel
import pymysql
import os # YENİ: İşletim sistemi yollarını okumak için
from dotenv import load_dotenv # YENİ: .env dosyasını yüklemek için

load_dotenv()

app = FastAPI()

# --- VERİTABANI BAĞLANTI AYARLARI (.env'den çekiliyor) ---
DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor
    )


# Kaan'ın bize göndereceği "Stok Güncelleme" paketinin formatını belirliyoruz
class StokGuncelleme(BaseModel):
    yeni_stok: int

@app.get("/")
def ana_sayfa():
    return {"mesaj": "AWS Veritabanı ile İletişim Köprüsü Kuruldu!"}

@app.get("/urunler")
def urunleri_getir():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            sql = "SELECT * FROM products"
            cursor.execute(sql)
            urunler = cursor.fetchall()
            return {"data": urunler}
    finally:
        connection.close()

@app.get("/kritik-stok")
def kritik_stok_getir():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            sql = "SELECT * FROM products WHERE current_stock <= reorder_point"
            cursor.execute(sql)
            kritik_urunler = cursor.fetchall()
            return {
                "acil_durum_sayisi": len(kritik_urunler),
                "data": kritik_urunler
            }
    finally:
        connection.close()

@app.get("/urun/{urun_id}")
def tek_urun_getir(urun_id: int):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            sql = "SELECT * FROM products WHERE product_id = %s"
            cursor.execute(sql, (urun_id,))
            urun = cursor.fetchone()
            if urun:
                return {"data": urun}
            else:
                return {"hata": "Böyle bir ürün bulunamadı!"}
    finally:
        connection.close()

# --- 3. YENİ UÇ NOKTA: STOK GÜNCELLEME (YAZMA İŞLEMİ) ---
@app.put("/urun/{urun_id}/stok")
def stok_guncelle(urun_id: int, stok_bilgisi: StokGuncelleme):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # 1. Önce böyle bir ürün gerçekten var mı diye bakalım
            cursor.execute("SELECT * FROM products WHERE product_id = %s", (urun_id,))
            if not cursor.fetchone():
                return {"hata": "Ürün bulunamadı!"}

            # 2. Ürün varsa stoğunu AWS'de güncelle
            sql = "UPDATE products SET current_stock = %s WHERE product_id = %s"
            cursor.execute(sql, (stok_bilgisi.yeni_stok, urun_id))
            
            # 3. ÇOK ÖNEMLİ: Değişikliği kalıcı olarak kaydet (Commit)
            connection.commit()
            
            return {"mesaj": "Stok başarıyla güncellendi!", "yeni_stok": stok_bilgisi.yeni_stok}
    finally:
        connection.close()