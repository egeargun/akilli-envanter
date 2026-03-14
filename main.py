from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pymysql
import os
from typing import Optional
from dotenv import load_dotenv
from datetime import date, timedelta
from schemas import StokGuncelleme, ProductCreate, StockTransaction, TalepYaniti
import csv
import io
from fastapi.responses import StreamingResponse

# .env dosyasındaki gizli şifreleri sisteme yükle
load_dotenv()

app = FastAPI(title="Akıllı Kafe Envanter Sistemi API")

# --- CORS AYARLARI (Kaan'ın Arayüzüne İzin Veriyoruz) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],
)

# --- VERİTABANI BAĞLANTI AYARLARI ---
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
    # SKT opsiyonel (Termos için boş, Süt için dolu gelecek)
    expiration_date: Optional[str] = None 
    warehouse_location: str = "Ana Depo"

class StockTransaction(BaseModel):
    product_id: int
    quantity: int
    transaction_type: str # 'IN', 'OUT' veya 'ADJUST'
    notes: Optional[str] = None
    processed_by: str = "Admin" 

# ==========================================
# --- UÇ NOKTALAR (API ENDPOINTS) ---
# ==========================================

@app.get("/")
def ana_sayfa():
    return {"mesaj": "Yeni Nesil Kafe Envanter Sistemi AWS'de Çalışıyor! ☕️"}

# --- 1. TÜM ÜRÜNLERİ GETİR ---
@app.get("/urunler")
def urunleri_getir():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Kategorileri ve Tedarikçileri de ismen görebilmek için JOIN yapıyoruz
            cursor.execute("""
                SELECT p.*, c.name as category_name, s.name as supplier_name 
                FROM products p
                LEFT JOIN categories c ON p.category_id = c.category_id
                LEFT JOIN suppliers s ON p.supplier_id = s.supplier_id
            """)
            return {"data": cursor.fetchall()}
    finally:
        connection.close()

# --- 2. YENİ ÜRÜN EKLE ---
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
            return {"mesaj": f"{urun.name} başarıyla kafe envanterine eklendi!"}
    except Exception as e:
        return {"hata": f"Ürün eklenirken hata: {str(e)}"}
    finally:
        connection.close()

# --- 3. STOK HAREKETİ KAYDET (SATIŞ / GİRİŞ) ---
@app.post("/stok-hareketi")
def stok_hareketi_kaydet(hareket: StockTransaction):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            sql_log = """
            INSERT INTO inventory_transactions 
            (product_id, quantity, transaction_type, notes, processed_by) 
            VALUES (%s, %s, %s, %s, %s)
            """
            cursor.execute(sql_log, (hareket.product_id, hareket.quantity, hareket.transaction_type, hareket.notes, hareket.processed_by))
            
            if hareket.transaction_type.upper() == "IN":
                sql_update = "UPDATE products SET current_stock = current_stock + %s WHERE product_id = %s"
            else: # OUT (Satış veya Fire)
                sql_update = "UPDATE products SET current_stock = current_stock - %s WHERE product_id = %s"
            
            cursor.execute(sql_update, (hareket.quantity, hareket.product_id))
            connection.commit() 
            return {"mesaj": f"İşlem {hareket.processed_by} tarafından kaydedildi!"}
    except Exception as e:
        return {"hata": f"İşlem başarısız: {str(e)}"}
    finally:
        connection.close()

# --- 4. DASHBOARD ÖZETİ ---
@app.get("/dashboard-ozet")
def dashboard_ozet():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT 
                    COUNT(*) as toplam_urun_cesidi,
                    SUM(current_stock * unit_cost) as toplam_yatirim_maliyeti
                FROM products
            """)
            finans = cursor.fetchone()

            cursor.execute("SELECT COUNT(*) as acil_durum_sayisi FROM products WHERE current_stock <= reorder_point")
            kritik = cursor.fetchone()

            cursor.execute("""
                SELECT t.transaction_id, p.name as urun_adi, t.quantity, t.transaction_type, t.processed_by, t.transaction_date
                FROM inventory_transactions t
                JOIN products p ON t.product_id = p.product_id
                ORDER BY t.transaction_id DESC LIMIT 5
            """)
            son_hareketler = cursor.fetchall()

            return {
                "finansal_durum": finans,
                "kritik_stok_uyari_sayisi": kritik["acil_durum_sayisi"],
                "son_islemler": son_hareketler
            }
    finally:
        connection.close()

# --- 5. HOCANIN İSTEDİĞİ: SKT UYARI SİSTEMİ (YENİ Zeka) ---
@app.get("/skt-uyarisi")
def skt_uyarisi():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Sadece SKT'si olan (NULL olmayan) ve SKT'sine 30 günden az kalmış ürünleri getir
            cursor.execute("""
                SELECT product_id, name, expiration_date, current_stock, warehouse_location,
                DATEDIFF(expiration_date, CURDATE()) as kalan_gun
                FROM products
                WHERE expiration_date IS NOT NULL AND DATEDIFF(expiration_date, CURDATE()) <= 30
                ORDER BY kalan_gun ASC
            """)
            bozulacak_urunler = cursor.fetchall()

            if not bozulacak_urunler:
                return {"durum": "Güvenli", "mesaj": "Yakın zamanda SKT'si dolacak ürün yok."}

            return {
                "durum": "Kritik",
                "yaklasan_skt_sayisi": len(bozulacak_urunler),
                "riskli_urunler": bozulacak_urunler
            }
    finally:
        connection.close()

# --- 6. KRİTİK STOK UYARISI ---
@app.get("/kritik-stok")
def kritik_stok_uyarisi():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT name, current_stock, reorder_point, warehouse_location
                FROM products WHERE current_stock <= reorder_point
            """)
            return {"acil_siparis_listesi": cursor.fetchall()}
    finally:
        connection.close()

       # --- 7. DEPO ÇIKIŞ / SEVK TRENDİ VE GRAFİKLER (GET) ---
@app.get("/sevk-raporu")
def sevk_raporu():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # 1. GRAFİK İÇİN: Kafeye En Çok Sevk Edilen Ürünler (Pasta Grafiği - Tüketim Analizi)
            cursor.execute("""
                SELECT p.name, SUM(t.quantity) as toplam_sevk
                FROM inventory_transactions t
                JOIN products p ON t.product_id = p.product_id
                WHERE t.transaction_type = 'OUT'
                GROUP BY p.product_id, p.name
                ORDER BY toplam_sevk DESC
                LIMIT 5
            """)
            en_cok_sevk_edilenler = cursor.fetchall()

            # 2. GRAFİK İÇİN: Günlük Depo Çıkış Trendi (Çizgi Grafiği - Lojistik Hareketliliği)
            cursor.execute("""
                SELECT DATE(t.transaction_date) as tarih, SUM(t.quantity) as gunluk_cikis_adeti
                FROM inventory_transactions t
                WHERE t.transaction_type = 'OUT'
                GROUP BY DATE(t.transaction_date)
                ORDER BY tarih ASC
                LIMIT 7
            """)
            sevk_trendi = cursor.fetchall()

            if not en_cok_sevk_edilenler:
                return {"mesaj": "Henüz depodan kafeye hiç mal çıkışı (sevk) yapılmamış."}

            return {
                "grafik_1_pasta": {
                    "baslik": "Kafeye En Çok Sevk Edilen 5 Ürün",
                    "veriler": en_cok_sevk_edilenler
                },
                "grafik_2_cizgi": {
                    "baslik": "Son 7 Günlük Depo Çıkış (Lojistik) Trendi",
                    "veriler": sevk_trendi
                }
            }
    except Exception as e:
        return {"hata": f"Sevk raporu oluşturulamadı: {str(e)}"}
    finally:
        connection.close()

        # --- 3. STOK HAREKETİ KAYDET (VEYA TALEP OLUŞTUR) ---
@app.post("/stok-hareketi")
def stok_hareketi_kaydet(hareket: StockTransaction):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # 1. İşlemi (veya Talebi) veritabanına logla
            sql_log = """
            INSERT INTO inventory_transactions 
            (product_id, quantity, transaction_type, notes, processed_by, status) 
            VALUES (%s, %s, %s, %s, %s, %s)
            """
            cursor.execute(sql_log, (hareket.product_id, hareket.quantity, hareket.transaction_type, hareket.notes, hareket.processed_by, hareket.status.upper()))
            
            # 2. Zeka Devrede: EĞER İŞLEM "BEKLEMEDE" İSE STOĞU ELLEME! Sadece ONAYLANDI ise güncelle.
            if hareket.status.upper() == "ONAYLANDI":
                if hareket.transaction_type.upper() == "IN":
                    sql_update = "UPDATE products SET current_stock = current_stock + %s WHERE product_id = %s"
                else: 
                    sql_update = "UPDATE products SET current_stock = current_stock - %s WHERE product_id = %s"
                cursor.execute(sql_update, (hareket.quantity, hareket.product_id))
            
            connection.commit() 
            
            # Kaan'a doğru mesajı dönelim
            if hareket.status.upper() == "BEKLEMEDE":
                return {"mesaj": f"Talebiniz ({hareket.processed_by}) depo müdürü onayına gönderildi."}
            return {"mesaj": f"İşlem {hareket.processed_by} tarafından doğrudan onaylanıp kaydedildi!"}
    except Exception as e:
        return {"hata": f"İşlem başarısız: {str(e)}"}
    finally:
        connection.close()

        from schemas import TalepYaniti # Bunu dosyanın en üstündeki importların yanına eklemeyi unutma!

# --- 9. BEKLEYEN TALEPLERİ GETİR (DEPO MÜDÜRÜ EKRANI) ---
@app.get("/bekleyen-talepler")
def bekleyen_talepler():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT t.transaction_id, p.name as urun_adi, t.quantity, t.transaction_type, t.processed_by as talep_eden, t.transaction_date, t.notes
                FROM inventory_transactions t
                JOIN products p ON t.product_id = p.product_id
                WHERE t.status = 'BEKLEMEDE'
                ORDER BY t.transaction_date ASC
            """)
            talepler = cursor.fetchall()
            
            if not talepler:
                return {"mesaj": "Harika! Bekleyen hiçbir sevk talebi yok."}
            return {"bekleyen_talep_sayisi": len(talepler), "talepler": talepler}
    finally:
        connection.close()

# --- 10. TALEBİ ONAYLA VEYA REDDET (İŞ AKIŞI MOTORU) ---
@app.put("/talep-yanitla/{islem_id}")
def talep_yanitla(islem_id: int, yanit: TalepYaniti):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Önce talebin detaylarını bul (kaç adet, hangi ürün)
            cursor.execute("SELECT product_id, quantity, transaction_type, status FROM inventory_transactions WHERE transaction_id = %s", (islem_id,))
            talep = cursor.fetchone()

            if not talep:
                return {"hata": "Böyle bir işlem/talep bulunamadı."}
            if talep["status"] != "BEKLEMEDE":
                return {"hata": "Bu talep zaten yanıtlanmış!"}

            # Durumu güncelle (ONAYLANDI veya İPTAL)
            cursor.execute("UPDATE inventory_transactions SET status = %s, notes = CONCAT(notes, ' | Yanıtlayan: ', %s) WHERE transaction_id = %s", 
                           (yanit.yeni_durum.upper(), yanit.yanitlayan_kisi, islem_id))

            # Eğer Depo Müdürü ONAYLADIYSA, asıl stoğu ŞİMDİ düşür/artır
            if yanit.yeni_durum.upper() == "ONAYLANDI":
                if talep["transaction_type"].upper() == "IN":
                    sql_update = "UPDATE products SET current_stock = current_stock + %s WHERE product_id = %s"
                else:
                    sql_update = "UPDATE products SET current_stock = current_stock - %s WHERE product_id = %s"
                cursor.execute(sql_update, (talep["quantity"], talep["product_id"]))

            connection.commit()
            return {"mesaj": f"Talep başarıyla {yanit.yeni_durum} olarak güncellendi ve sistem işlendi."}
    except Exception as e:
        return {"hata": f"Talep yanıtlanırken hata oluştu: {str(e)}"}
    finally:
        connection.close()

        # --- 11. HOCANIN İSTEDİĞİ: EXCEL (CSV) RAPOR ÇIKTISI ALMA (GET) ---
@app.get("/export-envanter")
def export_envanter():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            # Depodaki tüm ürünlerin güncel durumunu çekiyoruz
            cursor.execute("""
                SELECT 
                    sku as 'Ürün Kodu', 
                    name as 'Ürün Adı', 
                    current_stock as 'Güncel Stok', 
                    reorder_point as 'Kritik Sınır', 
                    unit_cost as 'Birim Maliyet (TL)', 
                    (current_stock * unit_cost) as 'Toplam Değer (TL)',
                    warehouse_location as 'Depo Konumu'
                FROM products
                ORDER BY current_stock DESC
            """)
            urunler = cursor.fetchall()

            # Veri yoksa boş dönmesin
            if not urunler:
                return {"mesaj": "Depoda dışa aktarılacak ürün yok."}

            # Hafızada sanal bir dosya oluşturuyoruz
            stream = io.StringIO()
            # Türkçe karakterlerin Excel'de bozulmaması için utf-8-sig formatını Kaan front-end'de halledebilir veya biz BOM ekleyebiliriz.
            # CSV yazıcıyı ayarlıyoruz
            writer = csv.DictWriter(stream, fieldnames=urunler[0].keys())
            
            # Başlıkları yaz (Ürün Kodu, Ürün Adı vb.)
            writer.writeheader()
            
            # Bütün satırları yaz
            for urun in urunler:
                writer.writerow(urun)

            # Dosyanın başına dön (okunabilmesi için)
            stream.seek(0)

            # Dosyayı "envanter_raporu.csv" adıyla indirmeye zorluyoruz
            response = StreamingResponse(iter([stream.getvalue()]), media_type="text/csv")
            response.headers["Content-Disposition"] = "attachment; filename=envanter_raporu.csv"
            
            return response
    except Exception as e:
        return {"hata": f"Rapor oluşturulurken hata: {str(e)}"}
    finally:
        connection.close()