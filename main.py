from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pymysql
import os
from typing import Optional
from dotenv import load_dotenv

# .env dosyasındaki gizli şifreleri sisteme yükle
load_dotenv()

app = FastAPI()

# --- CORS AYARLARI (Kaan'ın Arayüzüne İzin Veriyoruz) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Geliştirme aşamasında her yerden gelen isteği kabul et
    allow_credentials=True,
    allow_methods=["*"], # GET, POST, PUT hepsine izin ver
    allow_headers=["*"],
)

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

# ==========================================
# --- ŞABLONLAR (PYDANTIC MODELLERİ) ---
# ==========================================

class StokGuncelleme(BaseModel):
    yeni_stok: int

# Hoca İstekleriyle Güncellenmiş Ürün Ekleme Şablonu
class ProductCreate(BaseModel):
    sku: str
    name: str
    description: Optional[str] = None
    category_id: int
    supplier_id: int
    unit_cost: float
    unit_price: float
    current_stock: int
    reorder_point: int
    abc_class: str
    # --- YENİ EKLENENLER ---
    expiration_date: Optional[str] = None # Örn: "2026-12-31" (SKT yoksa boş kalabilir)
    warehouse_location: str = "Ana Depo"  # Belirtilmezse Ana Depo'ya gitsin

# Hoca İstekleriyle Güncellenmiş Stok Hareketi Şablonu
class StockTransaction(BaseModel):
    product_id: int
    quantity: int
    transaction_type: str # 'IN' (Giriş) veya 'OUT' (Çıkış)
    notes: Optional[str] = None
    # --- YENİ EKLENEN ---
    processed_by: str = "Admin" # İşlemi kim yaptı? (Örn: "Ege", "Kaan")


# ==========================================
# --- UÇ NOKTALAR (API ENDPOINTS) ---
# ==========================================

@app.get("/")
def ana_sayfa():
    return {"mesaj": "AWS Veritabanı ile İletişim Köprüsü Kuruldu!"}

# --- 1. TÜM ÜRÜNLERİ GETİR (GET) ---
@app.get("/urunler")
def urunleri_getir():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM products")
            return {"data": cursor.fetchall()}
    finally:
        connection.close()

# --- 2. TEK ÜRÜN GETİR (GET) ---
@app.get("/urun/{urun_id}")
def tek_urun_getir(urun_id: int):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM products WHERE product_id = %s", (urun_id,))
            urun = cursor.fetchone()
            return {"data": urun} if urun else {"hata": "Ürün bulunamadı!"}
    finally:
        connection.close()

# --- 3. MANUEL STOK GÜNCELLE (PUT) ---
@app.put("/urun/{urun_id}/stok")
def stok_guncelle(urun_id: int, stok_bilgisi: StokGuncelleme):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM products WHERE product_id = %s", (urun_id,))
            if not cursor.fetchone():
                return {"hata": "Ürün bulunamadı!"}
            
            cursor.execute("UPDATE products SET current_stock = %s WHERE product_id = %s", (stok_bilgisi.yeni_stok, urun_id))
            connection.commit()
            return {"mesaj": "Stok başarıyla güncellendi!", "yeni_stok": stok_bilgisi.yeni_stok}
    finally:
        connection.close()

# --- 4. SIFIRDAN ÜRÜN EKLE (POST) [GÜNCELLENDİ] ---
@app.post("/urun-ekle")
def urun_ekle(urun: ProductCreate):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            sql = """
            INSERT INTO products 
            (sku, name, description, category_id, supplier_id, unit_cost, unit_price, current_stock, reorder_point, abc_class, expiration_date, warehouse_location) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            degerler = (urun.sku, urun.name, urun.description, urun.category_id, urun.supplier_id, 
                        urun.unit_cost, urun.unit_price, urun.current_stock, urun.reorder_point, 
                        urun.abc_class, urun.expiration_date, urun.warehouse_location)
            
            cursor.execute(sql, degerler)
            connection.commit()
            return {"mesaj": "Harika! Yeni ürün SKT ve Lokasyon bilgisiyle veritabanına eklendi.", "eklenen_urun": urun.name}
    except Exception as e:
        return {"hata": f"Ürün eklenirken bir sorun oluştu: {str(e)}"}
    finally:
        connection.close()

# --- 5. STOK HAREKETİ KAYDET (POST) [GÜNCELLENDİ] ---
@app.post("/stok-hareketi")
def stok_hareketi_kaydet(hareket: StockTransaction):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # 1. Hareketi kimin yaptığıyla (processed_by) logla
            sql_log = """
            INSERT INTO inventory_transactions 
            (product_id, quantity, transaction_type, notes, processed_by) 
            VALUES (%s, %s, %s, %s, %s)
            """
            cursor.execute(sql_log, (hareket.product_id, hareket.quantity, hareket.transaction_type, hareket.notes, hareket.processed_by))
            
            # 2. Ürünün ana tablodaki (products) güncel stoğunu otomatik güncelle
            if hareket.transaction_type.upper() == "IN":
                sql_update = "UPDATE products SET current_stock = current_stock + %s WHERE product_id = %s"
            else:
                sql_update = "UPDATE products SET current_stock = current_stock - %s WHERE product_id = %s"
            
            cursor.execute(sql_update, (hareket.quantity, hareket.product_id))
            
            connection.commit() 
            return {"mesaj": f"Stok hareketi {hareket.processed_by} tarafından işlendi ve ana stok güncellendi!"}
    except Exception as e:
        return {"hata": f"İşlem başarısız: {str(e)}"}
    finally:
        connection.close()

# --- 6. DASHBOARD / YÖNETİCİ ÖZETİ (GET) ---
@app.get("/dashboard-ozet")
def dashboard_ozet():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    COUNT(*) as toplam_urun_cesidi,
                    SUM(current_stock) as depodaki_toplam_urun_sayisi,
                    SUM(current_stock * unit_cost) as toplam_yatirim_maliyeti,
                    SUM(current_stock * unit_price) as beklenen_satis_geliri
                FROM products
            """)
            finans = cursor.fetchone()

            cursor.execute("SELECT COUNT(*) as acil_durum_sayisi FROM products WHERE current_stock <= reorder_point")
            kritik = cursor.fetchone()

            cursor.execute("""
                SELECT 
                    t.transaction_id, 
                    p.name as urun_adi, 
                    t.quantity, 
                    t.transaction_type, 
                    t.notes,
                    t.processed_by
                FROM inventory_transactions t
                JOIN products p ON t.product_id = p.product_id
                ORDER BY t.transaction_id DESC
                LIMIT 5
            """)
            son_hareketler = cursor.fetchall()

            return {
                "ozet_rapor": "Sistem Normal Çalışıyor",
                "finansal_durum": finans,
                "kritik_uyari_sayisi": kritik["acil_durum_sayisi"],
                "son_islemler": son_hareketler
            }
    except Exception as e:
        return {"hata": f"Dashboard verileri çekilemedi: {str(e)}"}
    finally:
        connection.close()

# --- 7. AKILLI ABC SINIFLANDIRMASI (GET) ---
@app.get("/abc-analizi")
def abc_analizi():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    product_id, 
                    name, 
                    current_stock, 
                    unit_cost, 
                    (current_stock * unit_cost) as toplam_deger 
                FROM products 
                WHERE current_stock > 0
                ORDER BY toplam_deger DESC
            """)
            urunler = cursor.fetchall()

            if not urunler:
                return {"mesaj": "Depoda analiz edilecek ürün yok."}

            genel_toplam_deger = sum(urun["toplam_deger"] for urun in urunler)
            kumulatif_deger = 0
            a_sinifi, b_sinifi, c_sinifi = [], [], []

            for urun in urunler:
                kumulatif_deger += urun["toplam_deger"]
                yuzde = (kumulatif_deger / genel_toplam_deger) * 100

                urun_verisi = {
                    "id": urun["product_id"],
                    "isim": urun["name"],
                    "stok": urun["current_stock"],
                    "toplam_deger": float(urun["toplam_deger"]),
                    "sinif": ""
                }

                if yuzde <= 80:
                    urun_verisi["sinif"] = "A"
                    a_sinifi.append(urun_verisi)
                elif yuzde <= 95:
                    urun_verisi["sinif"] = "B"
                    b_sinifi.append(urun_verisi)
                else:
                    urun_verisi["sinif"] = "C"
                    c_sinifi.append(urun_verisi)

            return {
                "ozet": {
                    "toplam_analiz_edilen_urun": len(urunler),
                    "A_sinifi_urun_sayisi": len(a_sinifi),
                    "B_sinifi_urun_sayisi": len(b_sinifi),
                    "C_sinifi_urun_sayisi": len(c_sinifi)
                },
                "detaylar": {
                    "A_Sinifi": a_sinifi,
                    "B_Sinifi": b_sinifi,
                    "C_Sinifi": c_sinifi
                }
            }
    except Exception as e:
        return {"hata": f"ABC Analizi yapılamadı: {str(e)}"}
    finally:
        connection.close()

# --- 8. KRİTİK STOK UYARISI (GET) ---
@app.get("/kritik-stok")
def kritik_stok_uyarisi():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    product_id, 
                    name, 
                    current_stock, 
                    reorder_point,
                    (reorder_point - current_stock) as eksik_miktar,
                    unit_cost,
                    ((reorder_point - current_stock) * unit_cost) as tahmini_siparis_maliyeti
                FROM products 
                WHERE current_stock <= reorder_point
                ORDER BY eksik_miktar DESC
            """)
            acil_urunler = cursor.fetchall()

            if not acil_urunler:
                return {
                    "durum": "Güvenli",
                    "mesaj": "Harika! Depoda kritik seviyeye düşen hiçbir ürün yok."
                }

            return {
                "durum": "Kritik",
                "toplam_acil_urun_sayisi": len(acil_urunler),
                "acil_siparis_listesi": acil_urunler
            }
    except Exception as e:
        return {"hata": f"Kritik stoklar çekilemedi: {str(e)}"}
    finally:
        connection.close()