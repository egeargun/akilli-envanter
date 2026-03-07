from fastapi import FastAPI
from pydantic import BaseModel
import pymysql
import os
from dotenv import load_dotenv

# .env dosyasındaki gizli şifreleri sisteme yükle
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

# --- ŞABLONLAR (Kaan'dan Gelecek Veriler İçin) ---
class StokGuncelleme(BaseModel):
    yeni_stok: int

# YENİ: Kaan'ın formdan göndereceği yeni ürün paketinin şablonu
class YeniUrun(BaseModel):
    sku: str
    name: str
    category_id: int
    supplier_id: int
    unit_cost: float
    unit_price: float
    current_stock: int
    reorder_point: int
    abc_class: str

# --- MEVCUT UÇ NOKTALAR (Okuma ve Güncelleme) ---
@app.get("/")
def ana_sayfa():
    return {"mesaj": "AWS Veritabanı ile İletişim Köprüsü Kuruldu!"}

@app.get("/urunler")
def urunleri_getir():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM products")
            return {"data": cursor.fetchall()}
    finally:
        connection.close()

@app.get("/kritik-stok")
def kritik_stok_getir():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM products WHERE current_stock <= reorder_point")
            kritik = cursor.fetchall()
            return {"acil_durum_sayisi": len(kritik), "data": kritik}
    finally:
        connection.close()

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

# --- YENİ UÇ NOKTA: SIFIRDAN ÜRÜN EKLE (POST) ---
@app.post("/urun-ekle")
def urun_ekle(urun: YeniUrun):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # SQL'in INSERT INTO komutu ile yepyeni bir satır oluşturuyoruz
            sql = """
            INSERT INTO products 
            (sku, name, category_id, supplier_id, unit_cost, unit_price, current_stock, reorder_point, abc_class) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            # Pydantic modelimizden (Kaan'dan) gelen verileri SQL'e sırasıyla yerleştiriyoruz
            degerler = (urun.sku, urun.name, urun.category_id, urun.supplier_id, 
                        urun.unit_cost, urun.unit_price, urun.current_stock, 
                        urun.reorder_point, urun.abc_class)
            
            cursor.execute(sql, degerler)
            connection.commit() # Değişikliği AWS'ye kalıcı olarak kaydet!
            
            return {"mesaj": "Harika! Yeni ürün veritabanına başarıyla eklendi.", "eklenen_urun": urun.name}
    except Exception as e:
        return {"hata": f"Ürün eklenirken bir sorun oluştu: {str(e)}"}
    finally:
        connection.close()

        # --- ŞABLON: Stok Hareketi İçin ---
class StokHareketi(BaseModel):
    product_id: int
    quantity: int
    transaction_type: str # 'IN' (Giriş) veya 'OUT' (Çıkış)
    notes: str = None

# --- 5. YENİ UÇ NOKTA: STOK HAREKETİ KAYDET (POST) ---
@app.post("/stok-hareketi")
def stok_hareketi_kaydet(hareket: StokHareketi):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # 1. Hareketi 'inventory_transactions' tablosuna ekle
            sql_log = """
            INSERT INTO inventory_transactions 
            (product_id, quantity, transaction_type, notes) 
            VALUES (%s, %s, %s, %s)
            """
            cursor.execute(sql_log, (hareket.product_id, hareket.quantity, hareket.transaction_type, hareket.notes))
            
            # 2. Ürünün ana tablodaki (products) güncel stoğunu otomatik güncelle
            # Eğer girişse (IN) topla, çıkışsa (OUT) çıkar
            if hareket.transaction_type.upper() == "IN":
                sql_update = "UPDATE products SET current_stock = current_stock + %s WHERE product_id = %s"
            else:
                sql_update = "UPDATE products SET current_stock = current_stock - %s WHERE product_id = %s"
            
            cursor.execute(sql_update, (hareket.quantity, hareket.product_id))
            
            connection.commit() # İki işlemi de birden onayla
            return {"mesaj": "Stok hareketi işlendi ve ana stok güncellendi!"}
    except Exception as e:
        return {"hata": f"İşlem başarısız: {str(e)}"}
    finally:
        connection.close()

        # --- 6. YENİ UÇ NOKTA: DASHBOARD / YÖNETİCİ ÖZETİ (GET) ---
@app.get("/dashboard-ozet")
def dashboard_ozet():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # 1. Genel Stok ve Finansal Durum (SQL'in Matematik Gücü)
            cursor.execute("""
                SELECT 
                    COUNT(*) as toplam_urun_cesidi,
                    SUM(current_stock) as depodaki_toplam_urun_sayisi,
                    SUM(current_stock * unit_cost) as toplam_yatirim_maliyeti,
                    SUM(current_stock * unit_price) as beklenen_satis_geliri
                FROM products
            """)
            finans = cursor.fetchone()

            # 2. Kritik Stok Uyarısı (Reorder Point altındakiler)
            cursor.execute("SELECT COUNT(*) as acil_durum_sayisi FROM products WHERE current_stock <= reorder_point")
            kritik = cursor.fetchone()

            # 3. Son 5 Stok Hareketi (Kimin ne yaptığı - Tabloları Birleştiriyoruz)
            cursor.execute("""
                SELECT 
                    t.transaction_id, 
                    p.name as urun_adi, 
                    t.quantity, 
                    t.transaction_type, 
                    t.notes
                FROM inventory_transactions t
                JOIN products p ON t.product_id = p.product_id
                ORDER BY t.transaction_id DESC
                LIMIT 5
            """)
            son_hareketler = cursor.fetchall()

            # Bütün verileri tek bir paket yapıp Kaan'ın arayüzüne yolluyoruz
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