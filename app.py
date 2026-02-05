from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file
import gspread
import pandas as pd
from datetime import datetime
import json
import os
import io
from functools import wraps
import threading # <-- THÊM CÁI NÀY ĐỂ CHẠY NGẦM
import time
# Thư viện PDF & Barcode
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from barcode import Code128
from barcode.writer import ImageWriter
from languages import DICTIONARY as TRANSLATIONS

app = Flask(__name__)
app.secret_key = 'vinamilk_secret_key_2026'

# --- DANH SÁCH TÀI KHOẢN ---
USERS_CONFIG = {
    "admin": { # Tên đăng nhập
        "password": "123",         # Mật khẩu
        "role": "admin",           # Quyền
        "name": "Admin"      # Tên hiển thị
    },
    "user": {
        "password": "456",
        "role": "user",
        "name": "WH"
    }
    # Muốn thêm người nữa thì cứ copy paste dòng trên xuống
}

# --- CẤU HÌNH CACHE ---
data_cache = {
    "inventory": None,
    "production": None,
    "products": None # <-- Thêm cái này để đỡ phải load lại sheet Products liên tục
}

def clear_cache():
    data_cache["inventory"] = None
    data_cache["production"] = None
    data_cache["products"] = None


def connect_db(sheet_name):
    try:
        if "GOOGLE_SHEETS_JSON" in os.environ:
            creds_dict = json.loads(os.environ.get("GOOGLE_SHEETS_JSON"))
            gc = gspread.service_account_from_dict(creds_dict)
        else:
            gc = gspread.service_account(filename='credentials.json')
        sh = gc.open("KHO_DATA_2026")
        return sh.worksheet(sheet_name)
    except Exception as e:
        print(f"Lỗi DB: {e}")
        return None


# --- HÀM LƯU GOOGLE SHEET CHẠY NGẦM (KHÔNG ĐƠ UI) ---
def background_write(sheet_name, row_data):
    """Hàm này sẽ chạy âm thầm bên dưới, không làm đơ web"""
    try:
        ws = connect_db(sheet_name)
        if ws:
            ws.append_row(row_data)
            print(f"✅ [Background] Đã lưu xong vào {sheet_name}")
    except Exception as e:
        print(f"❌ [Background] Lỗi lưu sheet: {e}")


def update_local_cache(sheet_name, row_data):
    """Cập nhật dữ liệu vào RAM ngay lập tức để hiển thị"""
    if data_cache.get(sheet_name) is not None:
        try:
            # Tạo DataFrame từ dòng mới
            # Lưu ý: row_data đang là list, cần convert sang DataFrame có columns khớp với cache
            df_current = data_cache[sheet_name]
            new_row_df = pd.DataFrame([row_data], columns=df_current.columns)

            # Nối vào cache hiện tại
            data_cache[sheet_name] = pd.concat([df_current, new_row_df], ignore_index=True)
            print(f"⚡ [Cache] Đã update RAM cho {sheet_name}")
        except Exception as e:
            print(f"⚠️ Lỗi update cache local: {e}")
            data_cache[sheet_name] = None  # Nếu lỗi thì xóa cache để lần sau load lại cho chắc
# --- DECORATOR BẢO VỆ (LOGIN REQUIRED) ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # KIỂM TRA TÀI KHOẢN TỪ BIẾN CỐ ĐỊNH (KHÔNG GỌI GOOGLE SHEET)
        if username in USERS_CONFIG:
            user_data = USERS_CONFIG[username]
            if user_data['password'] == password:
                # Đăng nhập thành công
                session['user'] = user_data['name']
                session['role'] = user_data['role']
                session['logged_in'] = True

                # --- MẸO HAY: PRE-LOAD DỮ LIỆU ---
                # Ngay khi login đúng, ta âm thầm sai "thư ký" đi tải data Kho & SX
                # Để lát nữa bấm vào Dashboard là có ngay, không phải chờ.
                threading.Thread(target=preload_data).start()

                flash(f"Xin chào {user_data['name']}!", "success")
                return redirect(url_for('index'))

        flash("Sai tên đăng nhập hoặc mật khẩu!", "danger")
    return render_template('login.html')


# Hàm phụ để tải trước dữ liệu (Pre-load)
def preload_data():
    try:
        if data_cache["inventory"] is None:
            ws = connect_db("Inventory")
            if ws: data_cache["inventory"] = pd.DataFrame(ws.get_all_records())

        if data_cache["production"] is None:
            ws = connect_db("Production")
            if ws: data_cache["production"] = pd.DataFrame(ws.get_all_records())
        print("✅ [Background] Đã tải xong dữ liệu nền!")
    except:
        pass

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

# --- HÀM VẼ PDF LƯỚI (2 CỘT x 5 HÀNG) ---
def create_pdf(data_list):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    label_w = 100 * mm
    label_h = 50 * mm
    margin_left = 5 * mm
    margin_top = 10 * mm
    col_gap = 2 * mm
    row_gap = 4 * mm 
    
    all_labels = []
    for item in data_list:
        try: copies = int(item.get('Copies', 1))
        except: copies = 1
        for _ in range(copies):
            all_labels.append(item)
            
    for i, item in enumerate(all_labels):
        pos_in_page = i % 10
        col = pos_in_page % 2 
        row = pos_in_page // 2 
        x = margin_left + col * (label_w + col_gap)
        y = height - margin_top - (row + 1) * (label_h + row_gap)
        
        c.setLineWidth(1)
        c.rect(x, y, label_w, label_h)
        
        loai_tem = item.get('Type', 'PRODUCT')
        header_color = (0, 0, 0) if loai_tem == 'PRODUCT' else (0.6, 0, 0)
        c.setFillColorRGB(*header_color)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x + 5*mm, y + label_h - 8*mm, f"VNM {loai_tem} LABEL")
        
        c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(x + 5*mm, y + label_h - 15*mm, f"SKU: {item['SKU']}")
        c.setFont("Helvetica", 10)
        c.drawString(x + 5*mm, y + label_h - 22*mm, f"Batch: {item['Batch']}")
        c.drawString(x + 5*mm, y + label_h - 28*mm, f"Date: {item.get('HSD','')}")
        
        c.setFont("Helvetica-Bold", 16)
        c.drawString(x + label_w - 25*mm, y + label_h - 15*mm, f"{item['Qty']}")
        c.setFont("Helvetica", 8)
        c.drawString(x + label_w - 25*mm, y + label_h - 20*mm, "UNITS")

        try:
            rv = io.BytesIO()
            Code128(item['FullCode'], writer=ImageWriter()).write(rv, options={'write_text': False})
            rv.seek(0)
            img = ImageReader(rv)
            c.drawImage(img, x + 5*mm, y + 5*mm, width=label_w - 10*mm, height=15*mm)
            c.setFont("Helvetica", 8)
            c.drawCentredString(x + label_w/2, y + 2*mm, item['FullCode'])
        except: pass

        if (i + 1) % 10 == 0 and (i + 1) < len(all_labels):
            c.showPage()

    c.save()
    buffer.seek(0)
    return buffer

@app.route('/')
@login_required
def index():
    global data_cache
    records = []
    po_list_detailed = []  # <-- Cập nhật: Biến mới chứa thông tin chi tiết PO
    total_stock = 0
    sku_count = 0
    pending_po = 0

    try:
        # 1. LẤY DATA KHO
        df = None
        if data_cache["inventory"] is not None:
            df = data_cache["inventory"]
        else:
            ws = connect_db("Inventory")
            if ws:
                data = ws.get_all_records()
                df = pd.DataFrame(data)
                data_cache["inventory"] = df

        if df is not None and not df.empty:
            df['Qty'] = pd.to_numeric(df['Qty'], errors='coerce').fillna(0)
            total_stock = int(df['Qty'].sum())
            if 'FullCode' in df.columns:
                df['SKU'] = df['FullCode'].apply(lambda x: x.split('|')[0] if '|' in x else x)
                sku_count = df['SKU'].nunique()

            # Lấy 10 dòng nhật ký
            records = df.tail(10).to_dict('records')
            records.reverse()

        # 2. LẤY DATA SẢN XUẤT (PO) & TÍNH CHI TIẾT
        if data_cache["production"] is None:
            ws_po = connect_db("Production")
            if ws_po: data_cache["production"] = pd.DataFrame(ws_po.get_all_records())

        df_po = data_cache["production"]
        if df_po is not None and not df_po.empty:
            for index, row in df_po.iterrows():
                # Lấy ID PO (đề phòng file Excel tên cột khác nhau)
                po_id = row.get('PO_ID', row.get('PO', ''))
                status = row.get('Status', 'Pending')

                # Phân tích BOM JSON
                bom_detail = ""
                bom_count = 0
                try:
                    bom = json.loads(str(row.get('BOM_JSON', '{}')))
                    bom_count = len(bom)
                    bom_detail = ", ".join(bom.keys())
                except:
                    pass

                po_info = {
                    'PO': po_id,
                    'Product': row.get('Product', ''),
                    'Status': status,
                    'StartDate': row.get('StartDate', ''),
                    'Target': row.get('TargetQty', 0),
                    'BOM_Count': bom_count,
                    'BOM_Detail': bom_detail
                }
                po_list_detailed.append(po_info)

                if str(status) != 'Done':
                    pending_po += 1

    except Exception as e:
        print(f"Lỗi Dashboard: {e}")

    # Truyền biến po_list_detailed sang HTML
    return render_template('index.html',
                           records=records,
                           po_list_detailed=po_list_detailed,
                           total_stock=total_stock, sku_count=sku_count, pending_po=pending_po)


# --- HELPER 1: LẤY DỮ LIỆU SẢN XUẤT (PO) ---
def get_po_data(current_po):
    """Trả về danh sách PO và yêu cầu BOM của PO hiện tại"""
    po_list = []
    po_requirements = {}

    # Lazy Load Production
    if data_cache["production"] is None:
        ws = connect_db("Production")
        if ws: data_cache["production"] = pd.DataFrame(ws.get_all_records())

    df_po = data_cache["production"]
    if df_po is not None and not df_po.empty:
        # Chuẩn hóa tên cột để tránh lỗi
        po_col = 'PO_ID' if 'PO_ID' in df_po.columns else 'PO'
        po_list = df_po[po_col].astype(str).unique().tolist()

        if current_po:
            row = df_po[df_po[po_col] == current_po]
            if not row.empty:
                try:
                    po_requirements = json.loads(str(row.iloc[0].get('BOM_JSON', '{}')))
                except:
                    po_requirements = {}

    return po_list, po_requirements


# --- HELPER 2: TÍNH TOÁN TỒN KHO & TIẾN ĐỘ ---
def get_stock_status(current_po, po_requirements):
    """Tính toán tiến độ SX và Tồn kho khả dụng"""
    po_progress = {}
    sku_stock_info = {}
    sku_batch_options = {}

    # Lazy Load Inventory
    if data_cache["inventory"] is None:
        ws = connect_db("Inventory")
        if ws: data_cache["inventory"] = pd.DataFrame(ws.get_all_records())

    df_inv = data_cache["inventory"]

    if df_inv is not None and not df_inv.empty:
        try:
            # 1. Tính Tiến độ (Đã xuất bao nhiêu cho PO này)
            if current_po:
                if 'PO' in df_inv.columns:
                    mask = (df_inv['PO'] == current_po) & (df_inv['Action'].str.contains('EXPORT', na=False))
                else:
                    mask = df_inv['Action'].str.contains('EXPORT', na=False)

                df_prog = df_inv[mask].copy()
                if not df_prog.empty:
                    df_prog['SKU_Extract'] = df_prog['FullCode'].apply(lambda x: x.split('|')[0] if '|' in x else x)
                    df_prog['QtyAbs'] = pd.to_numeric(df_prog['Qty'], errors='coerce').abs()
                    po_progress = df_prog.groupby('SKU_Extract')['QtyAbs'].sum().to_dict()

            # 2. Tính Tồn Kho Tổng & Chi tiết Lô (FEFO)
            # Chuẩn bị dữ liệu
            df_inv['SKU_Extract'] = df_inv['FullCode'].apply(lambda x: x.split('|')[0] if '|' in x else x)
            df_inv['Batch_Extract'] = df_inv['FullCode'].apply(lambda x: x.split('|')[1] if '|' in x else 'N/A')
            df_inv['QtyNum'] = pd.to_numeric(df_inv['Qty'], errors='coerce').fillna(0)

            # Group by
            stock = df_inv.groupby(['SKU_Extract', 'Batch_Extract', 'HSD'])['QtyNum'].sum().reset_index()
            stock = stock[stock['QtyNum'] > 0]
            stock = stock.sort_values(by=['HSD'])  # Sắp xếp hạn sử dụng (FEFO)

            # Map vào từng SKU cần thiết
            for sku in po_requirements.keys():
                batches = stock[stock['SKU_Extract'] == sku].to_dict('records')
                sku_batch_options[sku] = batches

                total_stock = sum(b['QtyNum'] for b in batches)

                # Lấy thông tin lô cũ nhất
                oldest_batch = 'N/A'
                oldest_hsd = '-'
                if batches:
                    oldest_batch = batches[0]['Batch_Extract']
                    oldest_hsd = batches[0]['HSD']

                sku_stock_info[sku] = {
                    'stock': int(total_stock),
                    'oldest_batch': oldest_batch,
                    'oldest_hsd': oldest_hsd
                }
        except Exception as e:
            print(f"Lỗi tính toán tồn kho: {e}")

    return po_progress, sku_stock_info, sku_batch_options


@app.route('/xuat-kho', methods=['GET', 'POST'])
@login_required
def xuat_kho():
    # Khởi tạo session queue
    if 'export_queue' not in session: session['export_queue'] = []
    current_po = session.get('current_po', '')

    # --- BƯỚC 1: LẤY DỮ LIỆU (Dùng Helper Function) ---
    po_list, po_requirements = get_po_data(current_po)
    po_progress, sku_stock_info, sku_batch_options = get_stock_status(current_po, po_requirements)

    # --- BƯỚC 2: XỬ LÝ POST REQUEST ---
    if request.method == 'POST':
        action_type = request.form.get('action_type')

        # 2a. Đổi PO
        if action_type == 'LOAD_PO':
            session['current_po'] = request.form.get('po_select')
            session.pop('export_queue', None)  # Xóa lịch sử phiên cũ cho sạch
            return redirect(url_for('xuat_kho'))

        # 2b. Xóa Session Log
        elif action_type == 'CLEAR':
            session.pop('export_queue', None)
            return redirect(url_for('xuat_kho'))

        # 2c. XUẤT KHO (ADD)
        elif action_type == 'ADD':
            start_time = time.time()  # Bấm giờ

            # --- 1. LẤY DỮ LIỆU TỪ FORM ---
            barcode_input = request.form.get('barcode_input')
            qty_input = int(request.form.get('qty', 1))
            manual_batch = request.form.get('manual_batch_select', '')

            # Lấy thông tin loại xuất (Nghiệp vụ)
            export_type = request.form.get('export_type', 'PRODUCTION')
            reason = request.form.get('reason', '')

            # --- 2. XỬ LÝ "SMART INPUT" (TÁCH SKU & BATCH) ---
            # (Đây là đoạn tui vừa bảo ní thay thế để hỗ trợ quét mã xịn)
            sku = barcode_input.split('|')[0] if "|" in barcode_input else barcode_input
            batch = barcode_input.split('|')[1] if "|" in barcode_input else (manual_batch or "N/A")

            # --- 3. VALIDATION (KIỂM TRA LỖI) ---
            # Nếu là xuất cho Sản Xuất (PRODUCTION) thì phải check PO
            if export_type == 'PRODUCTION' and current_po:
                if sku not in po_requirements:
                    flash(f"⛔ SKU {sku} không thuộc PO này!", "danger")
                    return redirect(url_for('xuat_kho'))

                # Check định mức PO
                current_done = po_progress.get(sku, 0)
                req_qty = po_requirements.get(sku, 0)
                remaining = req_qty - current_done

                if remaining <= 0:
                    flash(f"✅ Đã xuất đủ {sku}!", "success")
                    return redirect(url_for('xuat_kho'))
                if qty_input > remaining:
                    flash(f"⚠️ Dư định mức! Chỉ còn thiếu {remaining}.", "warning")
                    return redirect(url_for('xuat_kho'))

            # Check Tồn kho (Luôn luôn phải check dù xuất kiểu gì)
            real_stock = sku_stock_info.get(sku, {}).get('stock', 0)
            if qty_input > real_stock:
                flash(f"⛔ Kho không đủ hàng! (Tồn: {real_stock})", "danger")
                return redirect(url_for('xuat_kho'))

            # --- 4. CHUẨN BỊ DỮ LIỆU ĐỂ LƯU (ĐOẠN TRONG HÌNH) ---
            try:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                user = session.get('user', 'Ken')
                full_code = f"{sku}|{batch}"

                # Tạo Action Code: EXPORT_PRODUCTION, EXPORT_SCRAP...
                action_code = f"EXPORT_{export_type}"

                # Cột cuối cùng: Nếu là SX thì lưu mã PO, nếu là Hủy/Khác thì lưu Lý do
                ref_value = current_po if export_type == 'PRODUCTION' else reason

                # [Timestamp, User, FullCode, Action, NSX, HSD, Location, Qty, Reference]
                row_data = [ts, user, full_code, action_code, "", "", "OUTBOUND", -abs(qty_input), ref_value]

                # --- 5. LƯU (CACHE + THREAD) ---
                update_local_cache("inventory", row_data)
                threading.Thread(target=background_write, args=("Inventory", row_data)).start()

                # Update Session UI
                new_item = {
                    'SKU': sku, 'Batch': batch, 'Qty': qty_input,
                    'Type': export_type,  # Lưu thêm loại xuất để hiện lên web cho rõ
                    'Timestamp': datetime.now().strftime("%H:%M:%S")
                }
                session['export_queue'].insert(0, new_item)
                session.modified = True

                elapsed = round(time.time() - start_time, 4)
                flash(f"⚡ Đã xuất ({export_type}): {sku} | ⏱️ {elapsed}s", "success")

            except Exception as e:
                flash(f"Lỗi hệ thống: {e}", "danger")

            return redirect(url_for('xuat_kho'))

    # --- BƯỚC 3: RENDER VIEW ---
    return render_template('xuat_kho.html',
                           po_list=po_list, current_po=current_po,
                           po_requirements=po_requirements, po_progress=po_progress,
                           sku_batch_options=sku_batch_options, sku_stock_info=sku_stock_info,
                           queue=session.get('export_queue', []))


# ==========================================
# 5. NHẬP KHO (TURBO MODE + TIMER)
# ==========================================
@app.route('/nhap-kho', methods=['GET', 'POST'])
@login_required
def nhap_kho():
    if 'import_queue' not in session: session['import_queue'] = []

    # Lazy Load Products
    if data_cache["products"] is None:
        try:
            ws_p = connect_db("Products")
            if ws_p: data_cache["products"] = pd.DataFrame(ws_p.get_all_records())
        except:
            pass

    product_list = []
    if data_cache["products"] is not None:
        product_list = data_cache["products"].to_dict('records')

    # Lazy Load Batches
    existing_batches = []
    try:
        if data_cache["inventory"] is None:
            ws_inv = connect_db("Inventory")
            if ws_inv: data_cache["inventory"] = pd.DataFrame(ws_inv.get_all_records())

        if data_cache["inventory"] is not None:
            df = data_cache["inventory"]
            df['Batch_Extract'] = df['FullCode'].apply(lambda x: x.split('|')[1] if '|' in x else '')
            existing_batches = df['Batch_Extract'].unique().tolist()
            existing_batches = [x for x in existing_batches if x]
    except:
        pass

    if request.method == 'POST':
        # ⏱️ BẮT ĐẦU ĐO GIỜ
        start_time = time.time()

        try:
            # 1. Lấy dữ liệu
            sku = request.form.get('sku')
            qty = int(request.form.get('qty', 0))
            batch = request.form.get('batch', 'LOT-UNKNOWN').upper()
            nsx = request.form.get('nsx')
            hsd = request.form.get('hsd')
            location = request.form.get('location')
            label_type = request.form.get('label_type', 'PRODUCT')
            copies = int(request.form.get('copies', 1))

            # 2. Xử lý
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            user = session.get('user', 'Ken')
            full_code = f"{sku}|{batch}"

            row_data = [ts, user, full_code, "IMPORT", nsx, hsd, location, qty, ""]

            # 3. Tăng tốc (Cache + Thread)
            update_local_cache("inventory", row_data)
            threading.Thread(target=background_write, args=("Inventory", row_data)).start()

            # 4. Update Session
            new_item = {
                'SKU': sku, 'Batch': batch, 'Qty': qty,
                'Type': label_type, 'Copies': copies,
                'Timestamp': datetime.now().strftime("%H:%M:%S")
            }
            current_q = session.get('import_queue', [])
            current_q.insert(0, new_item)
            session['import_queue'] = current_q

            # ⏱️ KẾT THÚC ĐO GIỜ
            end_time = time.time()
            elapsed = round(end_time - start_time, 4)

            # HIỂN THỊ KẾT QUẢ
            flash(f"⚡ Đã nhập: {sku} ({qty}) | ⏱️ Xử lý: {elapsed}s", "success")

        except Exception as e:
            flash(f"Lỗi: {e}", "danger")

        return redirect(url_for('nhap_kho'))

    return render_template('nhap_kho.html', product_list=product_list, queue=session.get('import_queue', []),
                           existing_batches=existing_batches)

# --- ROUTE PHỤ ---
@app.route('/download-label/<int:index>')
@login_required
def download_single(index):
    queue = session.get('print_queue', [])
    if 0 <= index < len(queue):
        pdf_buffer = create_pdf([queue[index]])
        return send_file(pdf_buffer, as_attachment=True, download_name=f"Tem_{index}.pdf", mimetype='application/pdf')
    return "Lỗi"

@app.route('/download-all-labels')
@login_required
def download_all():
    queue = session.get('print_queue', [])
    if queue:
        pdf_buffer = create_pdf(queue)
        return send_file(pdf_buffer, as_attachment=True, download_name="Batch_Print.pdf", mimetype='application/pdf')
    return redirect(url_for('nhap_kho'))

@app.route('/clear-queue')
@login_required
def clear_queue():
    session.pop('print_queue', None)
    return redirect(url_for('nhap_kho'))
# --- XỬ LÝ ĐA NGÔN NGỮ ---
@app.context_processor
def inject_language():
    lang_code = session.get('lang', 'vi')
    # Phải có 'current_lang' ở đây thì HTML mới hiểu để hiện cờ đúng
    return dict(T=TRANSLATIONS.get(lang_code, TRANSLATIONS['vi']), current_lang=lang_code)

@app.route('/change-lang/<lang_code>')
def change_lang(lang_code):
    """API để đổi ngôn ngữ"""
    if lang_code in TRANSLATIONS:
        session['lang'] = lang_code
    return redirect(request.referrer) # Load lại trang hiện tại

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)
