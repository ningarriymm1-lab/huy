import os
import io
import sqlite3
from datetime import datetime
from flask import Flask, flash, redirect, render_template_string, request, session, url_for, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps

app = Flask(__name__)
# ดึง SECRET_KEY จาก Environment Variable บน Render หรือใช้ค่าเริ่มต้น
app.secret_key = os.environ.get('SECRET_KEY', 'CLEAN_SHOP_SECRET_2026_RENDER_READY')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shop_photos")
ALLOWED_PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff", "ico", "ppm", "pgm", "pbm", "pnm", "avif", "jfif"}
MAX_PHOTO_SIZE = (1000, 700)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clean_shop.db")

# =========================================================
# DATABASE INITIALIZATION
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = sqlite3.connect(DB_PATH)

    # ตารางผู้ใช้งาน
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            team_group TEXT DEFAULT 'ทีมปฏิบัติการ'
        )
    """)

    # ตารางร้านค้า
    db.execute("""
        CREATE TABLE IF NOT EXISTS shops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            district TEXT,
            address TEXT,
            phone TEXT,
            created_by TEXT,
            original_owner TEXT,
            shop_condition TEXT DEFAULT 'เปิดปกติ',
            priority_order INTEGER DEFAULT 1,
            status TEXT DEFAULT 'ยังไม่เช็คอิน',
            checked_at TEXT,
            checked_by_user TEXT,
            photo_path TEXT
        )
    """)

    # ตารางระบบเพื่อน
    db.execute("""
        CREATE TABLE IF NOT EXISTS friends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            friend_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending'
        )
    """)

    # สิทธิ์ถ่ายรูป
    db.execute("""
        CREATE TABLE IF NOT EXISTS photo_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            allowed INTEGER DEFAULT 0,
            approved_by TEXT,
            approved_at TEXT
        )
    """)

    try:
        db.execute("ALTER TABLE shops ADD COLUMN photo_path TEXT")
    except sqlite3.OperationalError:
        pass

    # ตั้งค่าระบบรูปภาพส่วนกลาง
    db.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL
        )
    """)
    db.execute(
        "INSERT OR IGNORE INTO app_settings (setting_key, setting_value) VALUES (?, ?)",
        ("photo_system_enabled", "1")
    )

    # ตารางประวัติการโอนร้านส่ง
    db.execute("""
        CREATE TABLE IF NOT EXISTS shop_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            shop_name TEXT NOT NULL,
            from_user TEXT NOT NULL,
            to_user TEXT NOT NULL,
            transferred_at TEXT NOT NULL
        )
    """)

    # สร้างบัญชี Admin เริ่มต้น (admin / admin555) หากยังไม่มีในระบบ
    admin_row = db.execute("SELECT id FROM users WHERE username = ?", ("admin",)).fetchone()
    if not admin_row:
        admin_hash = generate_password_hash("admin555")
        db.execute(
            "INSERT INTO users (username, password, team_group) VALUES (?, ?, ?)",
            ("admin", admin_hash, "ผู้ดูแลระบบ")
        )

    db.commit()
    db.close()

init_db()

# =========================================================
# PHOTO & PERMISSION HELPERS
# =========================================================

def is_admin():
    return session.get("username") == "admin"

def photo_system_enabled():
    try:
        db = get_db()
        row = db.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key = ?",
            ("photo_system_enabled",)
        ).fetchone()
        db.close()
        return bool(row and row[0] == "1")
    except Exception:
        return True

def photo_allowed_for_current_user():
    if not photo_system_enabled():
        return False
    if is_admin():
        return True

    user_id = session.get("user_id")
    if not user_id:
        return False

    db = get_db()
    row = db.execute(
        "SELECT allowed FROM photo_permissions WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    db.close()
    return bool(row and row[0] == 1)

def save_resized_photo(file_storage, prefix="photo"):
    if not file_storage or not file_storage.filename:
        return None

    try:
        file_storage.stream.seek(0)
        image = Image.open(file_storage.stream)
        image.verify()
        file_storage.stream.seek(0)
        image = Image.open(file_storage.stream)
        image = ImageOps.exif_transpose(image)

        if getattr(image, "is_animated", False):
            image.seek(0)

        image.thumbnail(MAX_PHOTO_SIZE, Image.Resampling.LANCZOS)

        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            background = Image.new("RGB", image.size, "white")
            alpha = image.convert("RGBA")
            background.paste(alpha, mask=alpha.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")

        filename = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
        path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        image.save(path, "JPEG", quality=88, optimize=True)
        return filename
    except Exception as e:
        print("Save photo error:", e)
        return None

app.jinja_env.globals["photo_allowed_for_current_user"] = photo_allowed_for_current_user
app.jinja_env.globals["photo_system_enabled"] = photo_system_enabled

# =========================================================
# RESPONSIVE HTML TEMPLATE (ALL SCREENS SUPPORTED)
# =========================================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Clean Shop - ระบบติดตามและจัดการร้านค้า</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Prompt', sans-serif; }
        .search-history-dropdown {
            position: absolute; top: 100%; left: 0; right: 0;
            background: white; border: 1px solid #e2e8f0; border-radius: 0.75rem;
            box-shadow: 0 10px 25px -5px rgba(0,0,0,0.1); z-index: 50; max-height: 250px; overflow-y: auto; display: none; margin-top: 4px;
        }
        .history-item { padding: 10px 14px; cursor: pointer; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #f1f5f9; font-size: 0.875rem; }
        .history-item:hover { background-color: #f8fafc; color: #2563eb; }
        .flash-message { transition: opacity 0.3s ease, transform 0.3s ease; }
        .flash-message.hide { opacity: 0; transform: translateY(-10px); pointer-events: none; }
    </style>
</head>
<body class="bg-slate-50 min-h-screen flex flex-col text-slate-800 antialiased">

    {% if session.get('user_id') %}
    <!-- RESPONSIVE NAVBAR -->
    <nav class="bg-blue-600 text-white shadow-md sticky top-0 z-40">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div class="flex justify-between h-16 items-center">
                <div class="flex items-center space-x-3">
                    <a href="{{ url_for('home') }}" class="text-xl font-bold tracking-tight flex items-center gap-2">
                        🏪 Clean Shop
                    </a>
                    <div class="hidden lg:flex space-x-1 text-sm ml-4">
                        <a href="{{ url_for('home') }}" class="px-3 py-2 rounded-lg hover:bg-blue-700 transition">หน้าแรก</a>
                        <a href="{{ url_for('shops_list') }}" class="px-3 py-2 rounded-lg hover:bg-blue-700 transition">ร้านของฉัน</a>
                        <a href="{{ url_for('add_shop') }}" class="px-3 py-2 rounded-lg hover:bg-blue-700 transition">+ เพิ่มร้านค้า</a>
                        <a href="{{ url_for('friends_page') }}" class="px-3 py-2 rounded-lg hover:bg-blue-700 transition">ระบบเพื่อน</a>
                        <a href="{{ url_for('transfer_history') }}" class="px-3 py-2 rounded-lg hover:bg-blue-700 transition">ประวัติโอนร้านส่ง</a>
                        {% if session.get('username') == 'admin' %}
                            <a href="{{ url_for('manage_users') }}" class="px-3 py-2 rounded-lg hover:bg-blue-800 bg-blue-700/50 transition">🛡️ จัดการผู้ใช้</a>
                            <a href="{{ url_for('admin_change_password') }}" class="px-3 py-2 rounded-lg hover:bg-blue-800 bg-blue-700/50 transition">🔐 รหัส Admin</a>
                        {% endif %}
                    </div>
                </div>

                <div class="flex items-center space-x-2 sm:space-x-3">
                    <span class="text-xs sm:text-sm bg-blue-700/90 px-3 py-1.5 rounded-full font-medium border border-blue-500/40">
                        👤 {{ session.get('username') }}
                        {% if session.get('username') == 'admin' %}
                            <span class="bg-red-500 text-white text-[10px] px-1.5 py-0.5 rounded-full font-bold ml-1">Admin</span>
                        {% endif %}
                    </span>
                    <a href="{{ url_for('logout') }}" class="bg-red-500 hover:bg-red-600 text-white px-3 py-1.5 rounded-lg text-xs font-semibold transition">
                        ออก
                    </a>
                    <button onclick="toggleMobileMenu()" class="lg:hidden p-2 rounded-lg text-white hover:bg-blue-700 focus:outline-none text-xl leading-none">
                        ☰
                    </button>
                </div>
            </div>
        </div>

        <!-- Mobile Drawer Menu -->
        <div id="mobileMenu" class="hidden lg:hidden bg-blue-700 border-t border-blue-500/50 px-4 py-3 space-y-1.5 text-sm">
            <a href="{{ url_for('home') }}" class="block px-3 py-2 bg-blue-800 rounded-lg">🏠 หน้าแรก</a>
            <a href="{{ url_for('shops_list') }}" class="block px-3 py-2 bg-blue-800 rounded-lg">🏪 ร้านของฉัน</a>
            <a href="{{ url_for('add_shop') }}" class="block px-3 py-2 bg-blue-800 rounded-lg">➕ เพิ่มร้านค้า</a>
            <a href="{{ url_for('friends_page') }}" class="block px-3 py-2 bg-blue-800 rounded-lg">👥 ระบบเพื่อน</a>
            <a href="{{ url_for('transfer_history') }}" class="block px-3 py-2 bg-blue-800 rounded-lg">📦 ประวัติโอนร้านส่ง</a>
            {% if session.get('username') == 'admin' %}
                <a href="{{ url_for('manage_users') }}" class="block px-3 py-2 bg-blue-900 rounded-lg font-bold">🛡️ จัดการผู้ใช้</a>
                <a href="{{ url_for('admin_change_password') }}" class="block px-3 py-2 bg-blue-900 rounded-lg font-bold">🔐 รหัส Admin</a>
            {% endif %}
        </div>
    </nav>
    {% endif %}

    <!-- FLASH NOTIFICATIONS -->
    <div id="flash-container" class="max-w-4xl mx-auto w-full px-4 mt-4">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="flash-message {% if category == 'success' %}bg-emerald-100 border-emerald-500 text-emerald-800{% else %}bg-rose-100 border-rose-500 text-rose-800{% endif %} border-l-4 p-3.5 mb-2.5 rounded-xl shadow-sm text-xs sm:text-sm flex justify-between items-center" role="alert">
                        <p>{% if category == 'success' %}✅{% else %}⚠️{% endif %} {{ message }}</p>
                        <button type="button" onclick="this.parentElement.remove()" class="ml-4 font-bold text-lg leading-none">&times;</button>
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}
    </div>

    <!-- MAIN CONTENT -->
    <main class="flex-grow max-w-7xl mx-auto w-full px-4 sm:px-6 lg:px-8 py-6">

        {% if page == 'login' %}
        <div class="max-w-md mx-auto bg-white rounded-3xl shadow-xl overflow-hidden mt-8 p-6 sm:p-8 border border-slate-200">
            <div class="text-center mb-6">
                <div class="text-4xl mb-2">🏪</div>
                <h2 class="text-2xl font-bold text-slate-800">เข้าสู่ระบบ</h2>
                <p class="text-xs sm:text-sm text-slate-500 mt-1">ระบบติดตามขนส่งและจัดการร้านค้า Clean Shop</p>
            </div>
            <form method="POST" class="space-y-4">
                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">ชื่อผู้ใช้งาน</label>
                    <input type="text" name="username" required placeholder="admin หรือ username" class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl focus:ring-2 focus:ring-blue-500 text-sm">
                </div>
                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">รหัสผ่าน</label>
                    <input type="password" name="password" required placeholder="รหัสผ่านเริ่มต้น admin: admin555" class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl focus:ring-2 focus:ring-blue-500 text-sm">
                </div>
                <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-3 rounded-xl transition text-sm shadow">
                    เข้าสู่ระบบ
                </button>
            </form>
            <div class="text-center mt-6 text-xs sm:text-sm text-slate-600">
                ยังไม่มีบัญชีใช่ไหม? <a href="{{ url_for('register') }}" class="text-blue-600 font-semibold hover:underline">สมัครสมาชิก</a>
            </div>
        </div>

        {% elif page == 'register' %}
        <div class="max-w-md mx-auto bg-white rounded-3xl shadow-xl overflow-hidden mt-8 p-6 sm:p-8 border border-slate-200">
            <div class="text-center mb-6">
                <h2 class="text-2xl font-bold text-slate-800">สมัครสมาชิก</h2>
                <p class="text-xs sm:text-sm text-slate-500 mt-1">สร้างบัญชีผู้ใช้งานใหม่ในทีม</p>
            </div>
            <form method="POST" class="space-y-4">
                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">ชื่อผู้ใช้งาน (อย่างน้อย 3 ตัวอักษร)</label>
                    <input type="text" name="username" required class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl focus:ring-2 focus:ring-blue-500 text-sm">
                </div>
                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">รหัสผ่าน (อย่างน้อย 4 ตัวอักษร)</label>
                    <input type="password" name="password" required class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl focus:ring-2 focus:ring-blue-500 text-sm">
                </div>
                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">กลุ่มทีมปฏิบัติงาน</label>
                    <input type="text" name="team_group" value="ทีมปฏิบัติการ" class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl focus:ring-2 focus:ring-blue-500 text-sm">
                </div>
                <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-3 rounded-xl transition text-sm shadow">
                    สมัครสมาชิก
                </button>
            </form>
            <div class="text-center mt-6 text-xs sm:text-sm text-slate-600">
                มีบัญชีอยู่แล้ว? <a href="{{ url_for('login') }}" class="text-blue-600 font-semibold hover:underline">เข้าสู่ระบบ</a>
            </div>
        </div>

        {% elif page == 'home' %}
        <div class="space-y-6">
            <div class="bg-gradient-to-r from-blue-600 via-blue-700 to-indigo-800 rounded-3xl shadow-lg p-6 sm:p-10 text-white flex flex-col md:flex-row justify-between items-start md:items-center gap-6">
                <div>
                    <div class="inline-block bg-blue-500/30 px-3 py-1 rounded-full text-xs mb-2">เวอร์ชันรองรับทุกหน้าจอ & Render.com</div>
                    <h1 class="text-2xl sm:text-3xl md:text-4xl font-bold">ยินดีต้อนรับ, {{ session.get('username') }}!</h1>
                    <p class="text-blue-100 mt-2 text-xs sm:text-sm max-w-xl">ระบบจัดการและติดตามร้านค้า สะดวก รวดเร็ว บันทึกพิกัด ถ่ายรูปยืนยัน และส่งต่องานร่วมกันในทีม</p>
                </div>
                <div class="flex flex-wrap gap-3 w-full md:w-auto">
                    <a href="{{ url_for('add_shop') }}" class="flex-1 md:flex-initial text-center bg-white text-blue-700 hover:bg-blue-50 font-semibold px-4 py-2.5 rounded-xl shadow transition text-sm">+ เพิ่มร้านค้าใหม่</a>
                    <a href="{{ url_for('shops_list') }}" class="flex-1 md:flex-initial text-center bg-blue-800 hover:bg-blue-900 text-white font-semibold px-4 py-2.5 rounded-xl shadow transition text-sm">ร้านค้าของฉัน</a>
                </div>
            </div>

            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <a href="{{ url_for('shops_list') }}" class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200 hover:border-blue-300 transition flex items-center space-x-3.5">
                    <div class="p-3 bg-blue-50 text-blue-600 rounded-xl text-xl font-bold">🏪</div>
                    <div>
                        <p class="text-xs text-slate-400">ร้านค้า</p>
                        <p class="font-bold text-slate-800 text-sm sm:text-base">ร้านทั้งหมด →</p>
                    </div>
                </a>
                <a href="{{ url_for('friends_page') }}" class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200 hover:border-indigo-300 transition flex items-center space-x-3.5">
                    <div class="p-3 bg-indigo-50 text-indigo-600 rounded-xl text-xl font-bold">👥</div>
                    <div>
                        <p class="text-xs text-slate-400">ทีมงาน</p>
                        <p class="font-bold text-slate-800 text-sm sm:text-base">ดูเพื่อนร่วมงาน →</p>
                    </div>
                </a>
                <a href="{{ url_for('transfer_history') }}" class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200 hover:border-amber-300 transition flex items-center space-x-3.5">
                    <div class="p-3 bg-amber-50 text-amber-600 rounded-xl text-xl font-bold">📦</div>
                    <div>
                        <p class="text-xs text-slate-400">โอนส่ง</p>
                        <p class="font-bold text-slate-800 text-sm sm:text-base">ประวัติโอนส่ง →</p>
                    </div>
                </a>
                <a href="{{ url_for('reset_checkins') }}" class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200 hover:border-red-300 transition flex items-center space-x-3.5">
                    <div class="p-3 bg-red-50 text-red-600 rounded-xl text-xl font-bold">🔄</div>
                    <div>
                        <p class="text-xs text-slate-400">รอบงาน</p>
                        <p class="font-bold text-slate-800 text-sm sm:text-base">รีเซ็ตเช็คอิน →</p>
                    </div>
                </a>
            </div>
        </div>

        {% elif page == 'shops_list' %}
        <div class="space-y-6">
            <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 bg-white p-5 sm:p-6 rounded-2xl shadow-sm border border-slate-200">
                <div>
                    <h2 class="text-xl sm:text-2xl font-bold text-slate-900">🏪 ร้านค้าของฉัน</h2>
                    <p class="text-xs sm:text-sm text-slate-500 mt-0.5">จัดการและติดตามสถานะร้านค้าที่คุณรับผิดชอบ</p>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                    <a href="{{ url_for('shops_list', filter='all') }}" class="px-3 py-1.5 rounded-lg text-xs font-medium {% if current_filter == 'all' %}bg-blue-600 text-white font-bold{% else %}bg-slate-100 text-slate-700{% endif %}">ทั้งหมด</a>
                    <a href="{{ url_for('shops_list', filter='open') }}" class="px-3 py-1.5 rounded-lg text-xs font-medium {% if current_filter == 'open' %}bg-blue-600 text-white font-bold{% else %}bg-slate-100 text-slate-700{% endif %}">เปิดปกติ</a>
                    <a href="{{ url_for('shops_list', filter='closed') }}" class="px-3 py-1.5 rounded-lg text-xs font-medium {% if current_filter == 'closed' %}bg-blue-600 text-white font-bold{% else %}bg-slate-100 text-slate-700{% endif %}">ปิดกิจการ</a>
                    <a href="{{ url_for('add_shop') }}" class="px-3.5 py-1.5 bg-emerald-600 text-white rounded-lg text-xs font-semibold hover:bg-emerald-700 transition shadow">+ เพิ่มร้าน</a>
                </div>
            </div>

            {% if shops %}
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
                {% for shop in shops %}
                <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-5 flex flex-col justify-between hover:shadow-md transition">
                    <div>
                        <div class="flex justify-between items-start gap-2 mb-3">
                            <h3 class="font-bold text-base sm:text-lg text-slate-900 break-words">{{ shop.name }}</h3>
                            <span class="px-2.5 py-0.5 rounded-full text-xs font-semibold whitespace-nowrap {% if shop.shop_condition == 'เปิดปกติ' %}bg-emerald-100 text-emerald-800{% else %}bg-rose-100 text-rose-800{% endif %}">
                                {{ shop.shop_condition }}
                            </span>
                        </div>
                        <div class="space-y-1 text-xs text-slate-600 mb-3">
                            <p>📍 อำเภอ: <span class="font-medium text-slate-800">{{ shop.district or '-' }}</span></p>
                            <p>🏠 ที่อยู่: <span class="text-slate-700">{{ shop.address or '-' }}</span></p>
                            <p>📞 โทร: {% if shop.phone %}<a href="tel:{{ shop.phone }}" class="text-blue-600 font-semibold hover:underline">{{ shop.phone }}</a>{% else %}-{% endif %}</p>
                        </div>
                        <div class="bg-slate-50 p-3 rounded-xl mb-3 text-xs space-y-1 border border-slate-100">
                            <div class="flex justify-between">
                                <span class="text-slate-500">ลำดับความสำคัญ:</span>
                                <span class="font-bold text-blue-600">#{{ shop.priority_order }}</span>
                            </div>
                            <div class="flex justify-between">
                                <span class="text-slate-500">สถานะเช็คอิน:</span>
                                <span class="font-semibold {% if shop.status == 'ถึงร้านแล้ว' %}text-emerald-600 font-bold{% else %}text-slate-500{% endif %}">
                                    {{ shop.status }}
                                </span>
                            </div>
                            {% if shop.checked_at %}
                            <div class="flex justify-between text-[11px] pt-1 border-t border-slate-200">
                                <span class="text-slate-400">เวลาเช็คอิน:</span>
                                <span class="text-slate-700 font-mono">{{ shop.checked_at }}</span>
                            </div>
                            {% endif %}
                        </div>
                    </div>

                    {% if shop.photo_path and photo_system_enabled() %}
                    <div class="mb-3">
                        <img src="{{ url_for('uploaded_photo', filename=shop.photo_path) }}" class="w-full h-40 sm:h-48 object-cover rounded-xl border border-slate-200 bg-slate-100" alt="รูป {{ shop.name }}">
                    </div>
                    {% endif %}

                    <div class="space-y-2 pt-2 border-t border-slate-100">
                        {% if shop.status != 'ถึงร้านแล้ว' %}
                        <form action="{{ url_for('checkin_shop', shop_id=shop.id) }}" method="POST" enctype="multipart/form-data">
                            {% if session.get('username') == 'admin' or photo_allowed_for_current_user() %}
                            <div class="mb-2 p-2.5 rounded-xl bg-blue-50 border border-blue-200 text-xs">
                                <label class="block font-semibold text-blue-900 mb-1">📷 รูปยืนยันการเข้าร้าน</label>
                                <label class="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold cursor-pointer">
                                    📷 กดเพื่อถ่ายรูป
                                    <input type="file" name="checkin_camera_photo" accept="image/*" capture="environment" class="hidden" onchange="setCheckinPhoto(this)">
                                </label>
                                <span class="block text-[11px] text-slate-500 mt-1 checkin-photo-label">ยังไม่ได้ถ่ายรูป</span>
                            </div>
                            {% endif %}
                            <button type="submit" class="w-full py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl text-xs font-semibold shadow transition">
                                {% if session.get('username') == 'admin' or photo_allowed_for_current_user() %}
                                    📷✅ เช็คอิน + ยืนยันรูป
                                {% else %}
                                    ✅ เช็คอิน (ถึงร้านแล้ว)
                                {% endif %}
                            </button>
                        </form>
                        {% endif %}

                        <div class="flex gap-1.5 pt-1">
                            <a href="{{ url_for('edit_shop', shop_id=shop.id) }}" class="flex-1 py-1.5 bg-blue-50 text-blue-700 hover:bg-blue-100 text-center rounded-lg text-xs font-medium">✏️ แก้ไข</a>
                            <a href="{{ url_for('transfer_shop', shop_id=shop.id) }}" class="flex-1 py-1.5 bg-amber-50 text-amber-800 hover:bg-amber-100 text-center rounded-lg text-xs font-medium">📦 โอนส่ง</a>
                            <a href="{{ url_for('toggle_condition', shop_id=shop.id) }}" class="py-1.5 px-2.5 bg-slate-100 text-slate-700 hover:bg-slate-200 text-center rounded-lg text-xs">⚙️</a>
                            <a href="{{ url_for('delete_shop', shop_id=shop.id) }}" onclick="return confirm('ยืนยันการลบร้านนี้?')" class="py-1.5 px-2.5 bg-rose-50 text-rose-600 hover:bg-rose-100 text-center rounded-lg text-xs">🗑️</a>
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <div class="bg-white rounded-2xl p-12 text-center border border-slate-200">
                <p class="text-slate-400 text-base mb-3">ยังไม่มีข้อมูลร้านค้าในหมวดหมู่นี้</p>
                <a href="{{ url_for('add_shop') }}" class="inline-block px-4 py-2 bg-blue-600 text-white text-xs sm:text-sm font-semibold rounded-xl hover:bg-blue-700 shadow">+ เพิ่มร้านค้าใหม่</a>
            </div>
            {% endif %}
        </div>

        {% elif page == 'add_shop' or page == 'edit_shop' %}
        <div class="max-w-xl mx-auto bg-white rounded-3xl shadow-sm border border-slate-200 p-6 sm:p-8">
            <h2 class="text-xl font-bold text-slate-900 mb-6">
                {% if page == 'add_shop' %}➕ เพิ่มร้านค้าใหม่{% else %}✏️ แก้ไขข้อมูลร้านค้า{% endif %}
            </h2>
            <form method="POST" class="space-y-4">
                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">ชื่อร้านค้า *</label>
                    <input type="text" name="name" value="{{ shop.name if shop else '' }}" required class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-500">
                </div>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                    <div>
                        <label class="block text-xs font-semibold text-slate-700 mb-1">อำเภอ / เขต</label>
                        <input type="text" name="district" value="{{ shop.district if shop else '' }}" class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-500">
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-700 mb-1">เบอร์โทรศัพท์</label>
                        <input type="tel" name="phone" value="{{ shop.phone if shop else '' }}" class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-500">
                    </div>
                </div>
                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">ที่อยู่ร้านค้า</label>
                    <textarea name="address" rows="3" class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-500">{{ shop.address if shop else '' }}</textarea>
                </div>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
                    <div>
                        <label class="block text-xs font-semibold text-slate-700 mb-1">ลำดับความสำคัญ</label>
                        <input type="number" name="priority_order" value="{{ shop.priority_order if shop else 1 }}" class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-500">
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-700 mb-1">สภาพร้าน</label>
                        <select name="shop_condition" class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl text-sm focus:ring-2 focus:ring-blue-500">
                            <option value="เปิดปกติ" {% if shop and shop.shop_condition == 'เปิดปกติ' %}selected{% endif %}>เปิดปกติ</option>
                            <option value="ปิดกิจการ" {% if shop and shop.shop_condition == 'ปิดกิจการ' %}selected{% endif %}>ปิดกิจการ</option>
                        </select>
                    </div>
                </div>
                <div class="flex justify-end space-x-2 pt-4 border-t border-slate-100">
                    <a href="{{ url_for('shops_list') }}" class="px-4 py-2.5 border rounded-xl text-slate-600 hover:bg-slate-100 text-xs sm:text-sm font-medium">ยกเลิก</a>
                    <button type="submit" class="px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-xs sm:text-sm font-semibold shadow">บันทึกข้อมูล</button>
                </div>
            </form>
        </div>

        {% elif page == 'transfer_shop' %}
        <div class="max-w-md mx-auto bg-white rounded-3xl shadow-sm border border-slate-200 p-6 sm:p-8">
            <h2 class="text-xl font-bold text-slate-800 mb-1">📦 ระบบโอนร้านส่ง</h2>
            <p class="text-xs sm:text-sm text-slate-500 mb-4">โอนสิทธิ์ความรับผิดชอบร้าน "<span class="font-bold text-slate-800">{{ shop.name }}</span>"</p>
            <form method="POST" class="space-y-4">
                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">ชื่อผู้ใช้งานปลายทาง</label>
                    <input type="text" name="target_user" required placeholder="ระบุ username ปลายทาง" class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl text-sm focus:ring-2 focus:ring-amber-500">
                </div>
                <div class="flex justify-end space-x-2 pt-4 border-t border-slate-100">
                    <a href="{{ url_for('shops_list') }}" class="px-4 py-2.5 border rounded-xl text-slate-600 hover:bg-slate-100 text-xs sm:text-sm">ยกเลิก</a>
                    <button type="submit" class="px-5 py-2.5 bg-amber-600 hover:bg-amber-700 text-white rounded-xl text-xs sm:text-sm font-semibold shadow">📦 ยืนยันโอนร้านส่ง</button>
                </div>
            </form>
        </div>

        {% elif page == 'transfer_history' %}
        <div class="space-y-6">
            <div class="bg-white p-5 sm:p-6 rounded-2xl shadow-sm border border-slate-200 flex justify-between items-center">
                <div>
                    <h2 class="text-xl sm:text-2xl font-bold text-slate-900">📦 ประวัติการโอนร้านส่ง</h2>
                    <p class="text-xs sm:text-sm text-slate-500 mt-0.5">ประวัติการส่งต่องานระหว่างสมาชิกในทีม</p>
                </div>
                <a href="{{ url_for('shops_list') }}" class="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs sm:text-sm font-medium rounded-xl">← กลับหน้าร้านค้า</a>
            </div>
            {% if transfers %}
            <div class="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="bg-slate-50 text-slate-600 text-xs uppercase font-semibold border-b border-slate-200">
                                <th class="p-3.5">ลำดับ</th>
                                <th class="p-3.5">ชื่อร้านค้า</th>
                                <th class="p-3.5">ผู้ส่งมอบ</th>
                                <th class="p-3.5">ผู้รับมอบ</th>
                                <th class="p-3.5">วันเวลาที่โอน</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100 text-xs sm:text-sm">
                            {% for t in transfers %}
                            <tr>
                                <td class="p-3.5 text-slate-400 font-mono">{{ loop.index }}</td>
                                <td class="p-3.5 font-bold text-slate-900">{{ t.shop_name }}</td>
                                <td class="p-3.5 text-blue-600 font-medium">👤 {{ t.from_user }}</td>
                                <td class="p-3.5 text-emerald-600 font-medium">👤 {{ t.to_user }}</td>
                                <td class="p-3.5 text-slate-500 font-mono text-xs">{{ t.transferred_at }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
            {% else %}
            <div class="bg-white rounded-2xl p-12 text-center border border-slate-200">
                <p class="text-slate-400">ยังไม่มีประวัติการโอนร้านส่งในระบบ</p>
            </div>
            {% endif %}
        </div>

        {% elif page == 'reset_checkins' %}
        <div class="max-w-md mx-auto bg-white rounded-3xl shadow-sm border border-slate-200 p-6 sm:p-8">
            <h2 class="text-xl font-bold text-rose-600 mb-2">⚠️ รีเซ็ตสถานะเช็คอินทั้งหมด</h2>
            <p class="text-xs sm:text-sm text-slate-500 mb-4">การกระทำนี้จะเปลี่ยนสถานะร้านค้าของคุณกลับเป็น "ยังไม่เช็คอิน" และล้างเวลาเช็คอิน</p>
            <form method="POST" class="space-y-4">
                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">พิมพ์คำว่า <span class="text-rose-600 font-mono font-bold">RESET</span></label>
                    <input type="text" name="confirm_text" required placeholder="RESET" class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl font-mono text-sm focus:ring-2 focus:ring-rose-500">
                </div>
                <div class="flex justify-end space-x-2 pt-4 border-t border-slate-100">
                    <a href="{{ url_for('home') }}" class="px-4 py-2.5 border rounded-xl text-slate-600 hover:bg-slate-100 text-xs sm:text-sm">ยกเลิก</a>
                    <button type="submit" class="px-5 py-2.5 bg-rose-600 hover:bg-rose-700 text-white rounded-xl text-xs sm:text-sm font-semibold shadow">ยืนยันรีเซ็ต</button>
                </div>
            </form>
        </div>

        {% elif page == 'friends' %}
        <div class="space-y-6">
            <div class="bg-white p-5 sm:p-6 rounded-2xl shadow-sm border border-slate-200">
                <h2 class="text-xl sm:text-2xl font-bold text-slate-900 mb-3">👥 ระบบเพื่อนร่วมงาน</h2>
                <form method="GET" action="{{ url_for('friends_page') }}" class="relative">
                    <label class="block text-xs font-semibold text-slate-700 mb-1">ค้นหาเพื่อนร่วมงาน (Username)</label>
                    <div class="flex gap-2">
                        <input type="text" id="userSearchInput" name="search_user" value="{{ search_query }}" placeholder="พิมพ์ชื่อผู้ใช้งานเพื่อค้นหา..." autocomplete="off" class="flex-1 px-3.5 py-2.5 border border-slate-300 rounded-xl text-xs sm:text-sm focus:ring-2 focus:ring-blue-500">
                        <button type="submit" class="px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-xs sm:text-sm font-semibold shadow">ค้นหา</button>
                    </div>
                </form>
            </div>

            {% if search_query %}
            <div class="bg-white p-5 sm:p-6 rounded-2xl shadow-sm border border-slate-200">
                <h3 class="text-base font-bold text-slate-800 mb-3">ผลการค้นหาสำหรับ "{{ search_query }}"</h3>
                {% if search_results %}
                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                    {% for u in search_results %}
                    <div class="border border-slate-200 p-3.5 rounded-xl flex justify-between items-center bg-slate-50">
                        <div>
                            <p class="font-bold text-slate-800 text-sm">{{ u.username }}</p>
                            <p class="text-xs text-slate-500">{{ u.team_group }}</p>
                        </div>
                        <a href="{{ url_for('add_friend', friend_id=u.id) }}" class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-lg shadow-sm">+ เพิ่มเพื่อน</a>
                    </div>
                    {% endfor %}
                </div>
                {% else %}
                <p class="text-slate-500 text-xs">ไม่พบผู้ใช้งานที่ตรงกัน หรือเป็นเพื่อนกันอยู่แล้ว</p>
                {% endif %}
            </div>
            {% endif %}

            {% if pending_requests %}
            <div class="bg-white p-5 sm:p-6 rounded-2xl shadow-sm border border-amber-200">
                <h3 class="text-base font-bold text-slate-800 mb-3">📬 คำขอเป็นเพื่อนที่รอการตอบรับ</h3>
                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                    {% for req in pending_requests %}
                    <div class="border border-amber-200 bg-amber-50/60 p-3.5 rounded-xl flex justify-between items-center">
                        <div>
                            <p class="font-bold text-slate-800 text-sm">{{ req.username }}</p>
                            <p class="text-xs text-slate-500">{{ req.team_group }}</p>
                        </div>
                        <div class="flex gap-1.5">
                            <a href="{{ url_for('accept_friend', req_id=req.rel_id) }}" class="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold rounded-lg shadow-sm">ยอมรับ</a>
                            <a href="{{ url_for('remove_friend', rel_id=req.rel_id) }}" class="px-2.5 py-1.5 bg-rose-100 hover:bg-rose-200 text-rose-700 text-xs font-semibold rounded-lg">ปฏิเสธ</a>
                        </div>
                    </div>
                    {% endfor %}
                </div>
            </div>
            {% endif %}

            <div class="bg-white p-5 sm:p-6 rounded-2xl shadow-sm border border-slate-200">
                <h3 class="text-base font-bold text-slate-800 mb-3">👥 รายชื่อเพื่อนร่วมงานของฉัน</h3>
                {% if my_friends %}
                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                    {% for f in my_friends %}
                    <div class="border border-slate-200 p-3.5 rounded-xl flex justify-between items-center bg-white shadow-xs">
                        <div>
                            <p class="font-bold text-slate-800 text-sm">{{ f.username }}</p>
                            <p class="text-xs text-slate-500">{{ f.team_group }}</p>
                            <a href="{{ url_for('other_users_shops', user=f.username) }}" class="text-xs text-blue-600 hover:underline font-medium mt-1 inline-block">🔍 ดูร้านค้าของเพื่อน →</a>
                        </div>
                        <a href="{{ url_for('remove_friend', rel_id=f.rel_id) }}" onclick="return confirm('ยืนยันการลบเพื่อน?')" class="text-slate-400 hover:text-rose-600 p-2 text-sm">🗑️</a>
                    </div>
                    {% endfor %}
                </div>
                {% else %}
                <p class="text-slate-400 text-xs">ยังไม่มีรายชื่อเพื่อนในระบบ</p>
                {% endif %}
            </div>
        </div>

        {% elif page == 'other_users_shops' %}
        <div class="space-y-6">
            <div class="bg-white p-5 sm:p-6 rounded-2xl shadow-sm border border-slate-200 flex justify-between items-center">
                <div>
                    <h2 class="text-xl sm:text-2xl font-bold text-slate-900">🏪 ร้านค้าของเพื่อน: <span class="text-blue-600">{{ selected_user }}</span></h2>
                </div>
                <a href="{{ url_for('friends_page') }}" class="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs sm:text-sm font-medium rounded-xl">← กลับหน้าระบบเพื่อน</a>
            </div>
            {% if shops %}
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
                {% for shop in shops %}
                <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-5">
                    <h3 class="font-bold text-base text-slate-900 mb-2">{{ shop.name }}</h3>
                    <div class="text-xs text-slate-600 space-y-1 mb-3">
                        <p>📍 อำเภอ: {{ shop.district or '-' }}</p>
                        <p>🏠 ที่อยู่: {{ shop.address or '-' }}</p>
                        <p>📞 โทร: {{ shop.phone or '-' }}</p>
                    </div>
                    <div class="bg-slate-50 p-2.5 rounded-xl text-xs space-y-1">
                        <div class="flex justify-between"><span class="text-slate-500">ลำดับ:</span><span class="font-bold text-blue-600">#{{ shop.priority_order }}</span></div>
                        <div class="flex justify-between"><span class="text-slate-500">สถานะ:</span><span>{{ shop.status }}</span></div>
                    </div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <div class="bg-white rounded-2xl p-12 text-center border border-slate-200">
                <p class="text-slate-400">เพื่อนรายนี้ยังไม่มีร้านค้าในระบบ</p>
            </div>
            {% endif %}
        </div>

        {% elif page == 'manage_users' %}
        <div class="space-y-6">
            <div class="p-5 bg-white rounded-2xl shadow-sm border border-slate-200 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                <div>
                    <h3 class="font-bold text-slate-900">📷 ระบบรูปถ่ายลงงาน (สวิตช์หลัก)</h3>
                    <p class="text-xs sm:text-sm text-slate-500">Admin สามารถเปิดหรือปิดการถ่าย/อัปโหลดรูปของ User ได้ทั้งระบบ</p>
                </div>
                <div>
                    {% if photo_system_is_on %}
                    <a href="{{ url_for('admin_photo_system', action='off') }}" class="px-4 py-2 bg-rose-500 hover:bg-rose-600 text-white rounded-xl text-xs font-semibold">🔴 ปิดระบบถ่ายรูป</a>
                    {% else %}
                    <a href="{{ url_for('admin_photo_system', action='on') }}" class="px-4 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl text-xs font-semibold">🟢 เปิดระบบถ่ายรูป</a>
                    {% endif %}
                </div>
            </div>

            <div class="bg-white p-5 sm:p-6 rounded-2xl shadow-sm border border-slate-200">
                <h2 class="text-xl font-bold text-rose-600 mb-4">🛡️ จัดการผู้ใช้งานระบบ</h2>
                <div class="overflow-x-auto">
                    <table class="w-full text-left border-collapse">
                        <thead>
                            <tr class="bg-slate-50 text-slate-600 text-xs uppercase font-semibold border-b border-slate-200">
                                <th class="p-3">ID</th>
                                <th class="p-3">ชื่อผู้ใช้งาน</th>
                                <th class="p-3">กลุ่มทีม</th>
                                <th class="p-3 text-right">สิทธิ์ถ่ายรูป</th>
                                <th class="p-3 text-right">จัดการ</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100 text-xs sm:text-sm">
                            {% for u in users_list %}
                            <tr>
                                <td class="p-3 text-slate-400 font-mono">{{ u.id }}</td>
                                <td class="p-3 font-bold text-slate-900">{{ u.username }} {% if u.username == 'admin' %}<span class="text-[10px] bg-rose-100 text-rose-700 px-1.5 py-0.5 rounded">Admin</span>{% endif %}</td>
                                <td class="p-3 text-slate-600">{{ u.team_group }}</td>
                                <td class="p-3 text-right">
                                    {% if u.username != 'admin' %}
                                        <a href="{{ url_for('admin_photo_permission', user_id=u.id, action='allow') }}" class="px-2.5 py-1 bg-emerald-50 text-emerald-700 rounded-lg text-xs font-medium">เปิด</a>
                                        <a href="{{ url_for('admin_photo_permission', user_id=u.id, action='deny') }}" class="px-2.5 py-1 bg-slate-100 text-slate-600 rounded-lg text-xs font-medium ml-1">ปิด</a>
                                    {% else %}
                                        <span class="text-xs text-slate-400">✓ ถาวร</span>
                                    {% endif %}
                                </td>
                                <td class="p-3 text-right">
                                    {% if u.username != 'admin' %}
                                    <a href="{{ url_for('admin_reset_password', user_id=u.id) }}" class="px-2.5 py-1 bg-blue-50 text-blue-700 rounded-lg text-xs font-medium">เปลี่ยนรหัส</a>
                                    <a href="{{ url_for('delete_user', user_id=u.id) }}" onclick="return confirm('ยืนยันลบผู้ใช้?')" class="px-2.5 py-1 bg-rose-50 text-rose-600 rounded-lg text-xs font-medium ml-1">ลบ</a>
                                    {% else %}
                                    <span class="text-xs text-slate-400">🔒 บัญชีหลัก</span>
                                    {% endif %}
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        {% elif page == 'admin_reset_password' %}
        <div class="max-w-md mx-auto bg-white rounded-3xl shadow-sm border border-slate-200 p-6 sm:p-8">
            <h2 class="text-xl font-bold text-rose-600 mb-2">🔐 เปลี่ยนรหัสผ่านผู้ใช้งาน</h2>
            <p class="text-xs sm:text-sm text-slate-500 mb-4">ผู้ใช้งาน: <span class="font-bold text-slate-800">{{ target_user.username }}</span></p>
            <form method="POST" class="space-y-4">
                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">รหัสผ่านใหม่ (อย่างน้อย 4 ตัวอักษร)</label>
                    <input type="password" name="new_password" minlength="4" required class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl text-sm focus:ring-2 focus:ring-rose-500">
                </div>
                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">ยืนยันรหัสผ่านใหม่</label>
                    <input type="password" name="confirm_password" minlength="4" required class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl text-sm focus:ring-2 focus:ring-rose-500">
                </div>
                <div class="flex gap-2 pt-2">
                    <a href="{{ url_for('manage_users') }}" class="flex-1 text-center px-4 py-2.5 border rounded-xl text-slate-600 hover:bg-slate-100 text-xs sm:text-sm">ยกเลิก</a>
                    <button type="submit" class="flex-1 px-4 py-2.5 bg-rose-600 hover:bg-rose-700 text-white rounded-xl text-xs sm:text-sm font-semibold shadow">เปลี่ยนรหัสผ่าน</button>
                </div>
            </form>
        </div>

        {% elif page == 'admin_change_password' %}
        <div class="max-w-md mx-auto bg-white rounded-3xl shadow-sm border border-slate-200 p-6 sm:p-8">
            <h2 class="text-xl font-bold text-purple-600 mb-2">🔐 เปลี่ยนรหัสผ่าน Admin</h2>
            <p class="text-xs sm:text-sm text-slate-500 mb-4">เปลี่ยนรหัสผ่านของบัญชี admin โดยต้องยืนยันรหัสเดิม</p>
            <form method="POST" class="space-y-4">
                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">รหัสผ่านเดิม</label>
                    <input type="password" name="current_password" required class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl text-sm focus:ring-2 focus:ring-purple-500">
                </div>
                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">รหัสผ่านใหม่</label>
                    <input type="password" name="new_password" required minlength="4" class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl text-sm focus:ring-2 focus:ring-purple-500">
                </div>
                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">ยืนยันรหัสผ่านใหม่</label>
                    <input type="password" name="confirm_password" required minlength="4" class="w-full px-3.5 py-2.5 border border-slate-300 rounded-xl text-sm focus:ring-2 focus:ring-purple-500">
                </div>
                <div class="flex gap-2 pt-2">
                    <button type="submit" class="flex-1 bg-purple-600 hover:bg-purple-700 text-white font-semibold px-4 py-2.5 rounded-xl text-xs sm:text-sm shadow">บันทึกรหัสใหม่</button>
                    <a href="{{ url_for('home') }}" class="px-4 py-2.5 border rounded-xl text-slate-600 hover:bg-slate-100 text-xs sm:text-sm">ยกเลิก</a>
                </div>
            </form>
        </div>
        {% endif %}

    </main>

    <footer class="bg-white border-t border-slate-200 py-6 mt-12 text-center text-xs text-slate-500">
        <p>&copy; 2026 Clean Shop Management System. รองรับทุกหน้าจอ & Render.com</p>
    </footer>

    <script>
        function toggleMobileMenu() {
            var menu = document.getElementById('mobileMenu');
            if (menu) menu.classList.toggle('hidden');
        }
        function setCheckinPhoto(input) {
            var file = input.files && input.files[0];
            if (!file) return;
            var label = input.closest('form').querySelector('.checkin-photo-label');
            if (label) label.textContent = 'ถ่ายรูปแล้ว: ' + file.name;
        }
    </script>
</body>
</html>
"""

# =========================================================
# ROUTES CONTROLLER
# =========================================================

@app.route("/")
def home():
    if not session.get('user_id'):
        return redirect(url_for('login'))
    return render_template_string(HTML_TEMPLATE, page='home')

@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        db.close()

        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['team_group'] = user['team_group']
            flash("เข้าสู่ระบบเรียบร้อย", "success")
            return redirect(url_for('home'))
        else:
            flash("ชื่อผู้ใช้งานหรือรหัสผ่านไม่ถูกต้อง", "error")

    return render_template_string(HTML_TEMPLATE, page='login')

@app.route("/register", methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        team_group = request.form.get('team_group', 'ทีมปฏิบัติการ').strip()

        if len(username) < 3 or len(password) < 4:
            flash("กรุณากรอกข้อมูลให้ถูกต้อง (ชื่ออย่างน้อย 3 ตัว, รหัสอย่างน้อย 4 ตัว)", "error")
            return redirect(url_for('register'))

        try:
            db = get_db()
            hashed_pw = generate_password_hash(password)
            db.execute(
                "INSERT INTO users (username, password, team_group) VALUES (?, ?, ?)",
                (username, hashed_pw, team_group)
            )
            db.commit()
            db.close()
            flash("สมัครสมาชิกสำเร็จ! กรุณาเข้าสู่ระบบ", "success")
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash("ชื่อผู้ใช้งานนี้มีอยู่ในระบบแล้ว กรุณาใช้ชื่ออื่น", "error")
        except Exception as e:
            flash(f"เกิดข้อผิดพลาด: {e}", "error")

    return render_template_string(HTML_TEMPLATE, page='register')

@app.route("/logout")
def logout():
    session.clear()
    flash("ออกจากระบบเรียบร้อยแล้ว", "success")
    return redirect(url_for('login'))

@app.route("/add_shop", methods=['GET', 'POST'])
def add_shop():
    if not session.get('user_id'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        district = request.form.get('district', '').strip()
        address = request.form.get('address', '').strip()
        phone = request.form.get('phone', '').strip()
        priority_order = request.form.get('priority_order', 1)
        shop_condition = request.form.get('shop_condition', 'เปิดปกติ')
        created_by = session.get('username')

        try:
            priority_order = int(priority_order)
        except ValueError:
            priority_order = 1

        # กำหนดค่า photo_filename ป้องกัน NameError
        photo_filename = None

        db = get_db()
        db.execute(
            """
            INSERT INTO shops
            (name, district, address, phone, created_by, original_owner, shop_condition, priority_order, status, photo_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, district, address, phone, created_by, created_by, shop_condition, priority_order, 'ยังไม่เช็คอิน', photo_filename)
        )
        db.commit()
        db.close()

        flash("เพิ่มข้อมูลร้านค้าสำเร็จ", "success")
        return redirect(url_for('shops_list'))

    return render_template_string(HTML_TEMPLATE, page='add_shop')

@app.route("/edit_shop/<int:shop_id>", methods=['GET', 'POST'])
def edit_shop(shop_id):
    if not session.get('user_id'):
        return redirect(url_for('login'))

    db = get_db()
    shop = db.execute("SELECT * FROM shops WHERE id = ?", (shop_id,)).fetchone()

    if not shop:
        db.close()
        flash("ไม่พบข้อมูลร้านค้านี้", "error")
        return redirect(url_for('shops_list'))

    if shop['created_by'] != session.get('username') and not is_admin():
        db.close()
        flash("คุณไม่มีสิทธิ์แก้ไขร้านค้านี้", "error")
        return redirect(url_for('shops_list'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        district = request.form.get('district', '').strip()
        address = request.form.get('address', '').strip()
        phone = request.form.get('phone', '').strip()
        priority_order = request.form.get('priority_order', 1)
        shop_condition = request.form.get('shop_condition', 'เปิดปกติ')

        try:
            priority_order = int(priority_order)
        except ValueError:
            priority_order = 1

        db.execute(
            """
            UPDATE shops
            SET name = ?, district = ?, address = ?, phone = ?, priority_order = ?, shop_condition = ?
            WHERE id = ?
            """,
            (name, district, address, phone, priority_order, shop_condition, shop_id)
        )
        db.commit()
        db.close()

        flash("แก้ไขข้อมูลร้านค้าสำเร็จ", "success")
        return redirect(url_for('shops_list'))

    db.close()
    return render_template_string(HTML_TEMPLATE, page='edit_shop', shop=shop)

@app.route("/delete_shop/<int:shop_id>")
def delete_shop(shop_id):
    if not session.get('user_id'):
        return redirect(url_for('login'))

    db = get_db()
    shop = db.execute("SELECT * FROM shops WHERE id = ?", (shop_id,)).fetchone()

    if shop and (shop['created_by'] == session.get('username') or is_admin()):
        db.execute("DELETE FROM shops WHERE id = ?", (shop_id,))
        db.commit()
        flash("ลบร้านค้าเรียบร้อยแล้ว", "success")
    else:
        flash("คุณไม่มีสิทธิ์ลบร้านค้านี้", "error")

    db.close()
    return redirect(url_for('shops_list'))

@app.route("/transfer_shop/<int:shop_id>", methods=['GET', 'POST'])
def transfer_shop(shop_id):
    if not session.get('user_id'):
        return redirect(url_for('login'))

    db = get_db()
    shop = db.execute("SELECT * FROM shops WHERE id = ?", (shop_id,)).fetchone()

    if not shop:
        db.close()
        flash("ไม่พบข้อมูลร้านค้านี้", "error")
        return redirect(url_for('shops_list'))

    if request.method == 'POST':
        target_user = request.form.get('target_user', '').strip()
        target = db.execute("SELECT * FROM users WHERE username = ?", (target_user,)).fetchone()

        if not target:
            db.close()
            flash(f"ไม่พบชื่อผู้ใช้งาน '{target_user}' ในระบบ", "error")
            return redirect(url_for('transfer_shop', shop_id=shop_id))

        if target_user == session.get('username'):
            db.close()
            flash("ไม่สามารถโอนย้ายให้ตัวเองได้", "error")
            return redirect(url_for('transfer_shop', shop_id=shop_id))

        orig_owner = shop['original_owner'] if shop['original_owner'] else shop['created_by']
        from_user = session.get('username')
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        db.execute(
            "UPDATE shops SET created_by = ?, original_owner = ? WHERE id = ?",
            (target_user, orig_owner, shop_id)
        )
        db.execute(
            """
            INSERT INTO shop_transfers (shop_id, shop_name, from_user, to_user, transferred_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (shop_id, shop['name'], from_user, target_user, current_time)
        )
        db.commit()
        db.close()

        flash(f"📦 โอนร้านส่ง '{shop['name']}' ไปยังผู้ใช้ '{target_user}' สำเร็จ", "success")
        return redirect(url_for('shops_list'))

    db.close()
    return render_template_string(HTML_TEMPLATE, page='transfer_shop', shop=shop)

@app.route("/transfer_history")
def transfer_history():
    if not session.get('user_id'):
        return redirect(url_for('login'))

    db = get_db()
    transfers = db.execute("SELECT * FROM shop_transfers ORDER BY id DESC").fetchall()
    db.close()
    return render_template_string(HTML_TEMPLATE, page='transfer_history', transfers=transfers)

@app.route("/toggle_condition/<int:shop_id>")
def toggle_condition(shop_id):
    if not session.get('user_id'):
        return redirect(url_for('login'))

    db = get_db()
    shop = db.execute("SELECT * FROM shops WHERE id = ?", (shop_id,)).fetchone()
    if shop:
        new_cond = 'ปิดกิจการ' if shop['shop_condition'] == 'เปิดปกติ' else 'เปิดปกติ'
        db.execute("UPDATE shops SET shop_condition = ? WHERE id = ?", (new_cond, shop_id))
        db.commit()
        flash(f"เปลี่ยนสถานะร้านเป็น '{new_cond}' แล้ว", "success")
    db.close()
    return redirect(url_for('shops_list'))

@app.route("/checkin_shop/<int:shop_id>", methods=['POST'])
def checkin_shop(shop_id):
    if not session.get('user_id'):
        return redirect(url_for('login'))

    db = get_db()
    shop = db.execute("SELECT * FROM shops WHERE id = ?", (shop_id,)).fetchone()
    if not shop:
        db.close()
        flash("ไม่พบร้านค้านี้", "error")
        return redirect(url_for('shops_list'))

    username = session.get('username')
    photo = request.files.get("checkin_photo") or request.files.get("checkin_camera_photo")

    if photo_allowed_for_current_user():
        if not photo or not photo.filename:
            db.close()
            flash("บัญชีนี้ได้รับสิทธิ์ถ่ายรูปแล้ว กรุณาถ่ายรูปยืนยันก่อนเช็คอิน", "error")
            return redirect(url_for('shops_list'))

        unique_name = save_resized_photo(photo, f"checkin_{session.get('user_id')}_{shop_id}")
        if not unique_name:
            db.close()
            flash("ไฟล์นี้ไม่ใช่รูปที่ระบบรองรับ หรือรูปเสียหาย", "error")
            return redirect(url_for('shops_list'))

        db.execute("UPDATE shops SET photo_path = ? WHERE id = ?", (unique_name, shop_id))

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "UPDATE shops SET status = 'ถึงร้านแล้ว', checked_at = ?, checked_by_user = ? WHERE id = ?",
        (current_time, username, shop_id)
    )
    db.commit()
    db.close()

    flash(f"เช็คอินร้าน '{shop['name']}' สำเร็จ", "success")
    return redirect(url_for('shops_list'))

@app.route("/reset_checkins", methods=['GET', 'POST'])
def reset_checkins():
    if not session.get('user_id'):
        return redirect(url_for('login'))

    if request.method == 'POST':
        confirm_text = request.form.get('confirm_text', '').strip()
        if confirm_text == 'RESET':
            username = session.get('username')
            db = get_db()
            db.execute(
                "UPDATE shops SET status = 'ยังไม่เช็คอิน', checked_at = NULL, checked_by_user = NULL WHERE created_by = ?",
                (username,)
            )
            db.commit()
            db.close()
            flash("รีเซ็ตสถานะเช็คอินทั้งหมดของคุณเรียบร้อยแล้ว", "success")
            return redirect(url_for('home'))
        else:
            flash("พิมพ์คำว่า RESET ไม่ถูกต้อง กรุณาลองใหม่อีกครั้ง", "error")

    return render_template_string(HTML_TEMPLATE, page='reset_checkins')

@app.route("/shops")
def shops_list():
    if not session.get('user_id'):
        return redirect(url_for('login'))

    current_filter = request.args.get('filter', 'all')
    username = session.get('username')
    db = get_db()

    query = "SELECT * FROM shops WHERE created_by = ?"
    params = [username]

    if current_filter == 'open':
        query += " AND shop_condition = 'เปิดปกติ'"
    elif current_filter == 'closed':
        query += " AND shop_condition = 'ปิดกิจการ'"

    query += " ORDER BY priority_order ASC, id DESC"
    shops = db.execute(query, params).fetchall()
    db.close()

    return render_template_string(HTML_TEMPLATE, page='shops_list', shops=shops, current_filter=current_filter)

@app.route("/friends")
def friends_page():
    if not session.get('user_id'):
        return redirect(url_for('login'))

    user_id = session.get('user_id')
    search_query = request.args.get('search_user', '').strip()
    db = get_db()

    search_results = []
    if search_query:
        search_results = db.execute(
            """
            SELECT * FROM users
            WHERE username LIKE ? AND id != ?
            AND id NOT IN (
                SELECT friend_id FROM friends WHERE user_id = ?
                UNION
                SELECT user_id FROM friends WHERE friend_id = ?
            )
            """,
            (f"%{search_query}%", user_id, user_id, user_id)
        ).fetchall()

    pending_requests = db.execute(
        """
        SELECT f.id as rel_id, u.id as user_id, u.username, u.team_group
        FROM friends f JOIN users u ON f.user_id = u.id
        WHERE f.friend_id = ? AND f.status = 'pending'
        """,
        (user_id,)
    ).fetchall()

    my_friends = db.execute(
        """
        SELECT f.id as rel_id, u.id as user_id, u.username, u.team_group
        FROM friends f JOIN users u ON (f.friend_id = u.id OR f.user_id = u.id)
        WHERE (f.user_id = ? OR f.friend_id = ?) AND u.id != ? AND f.status = 'accepted'
        """,
        (user_id, user_id, user_id)
    ).fetchall()

    db.close()
    return render_template_string(
        HTML_TEMPLATE,
        page='friends',
        search_query=search_query,
        search_results=search_results,
        pending_requests=pending_requests,
        my_friends=my_friends
    )

@app.route("/add_friend/<int:friend_id>")
def add_friend(friend_id):
    if not session.get('user_id'):
        return redirect(url_for('login'))
    user_id = session.get('user_id')
    if user_id == friend_id:
        flash("ไม่สามารถเพิ่มตัวเองเป็นเพื่อนได้", "error")
        return redirect(url_for('friends_page'))

    db = get_db()
    existing = db.execute(
        "SELECT * FROM friends WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)",
        (user_id, friend_id, friend_id, user_id)
    ).fetchone()

    if not existing:
        db.execute("INSERT INTO friends (user_id, friend_id, status) VALUES (?, ?, 'pending')", (user_id, friend_id))
        db.commit()
        flash("ส่งคำขอเป็นเพื่อนเรียบร้อยแล้ว", "success")
    else:
        flash("มีสถานะความเป็นเพื่อนหรือคำขออยู่ในระบบแล้ว", "error")

    db.close()
    return redirect(url_for('friends_page'))

@app.route("/accept_friend/<int:req_id>")
def accept_friend(req_id):
    if not session.get('user_id'):
        return redirect(url_for('login'))
    db = get_db()
    db.execute("UPDATE friends SET status = 'accepted' WHERE id = ? AND friend_id = ?", (req_id, session.get('user_id')))
    db.commit()
    db.close()
    flash("ยอมรับคำขอเป็นเพื่อนแล้ว", "success")
    return redirect(url_for('friends_page'))

@app.route("/remove_friend/<int:rel_id>")
def remove_friend(rel_id):
    if not session.get('user_id'):
        return redirect(url_for('login'))
    user_id = session.get('user_id')
    db = get_db()
    db.execute("DELETE FROM friends WHERE id = ? AND (user_id = ? OR friend_id = ?)", (rel_id, user_id, user_id))
    db.commit()
    db.close()
    flash("ยกเลิกหรือลบเพื่อนเรียบร้อยแล้ว", "success")
    return redirect(url_for('friends_page'))

@app.route("/other_users_shops")
def other_users_shops():
    if not session.get('user_id'):
        return redirect(url_for('login'))
    selected_user = request.args.get('user', '').strip()
    shops = []
    if selected_user:
        db = get_db()
        shops = db.execute("SELECT * FROM shops WHERE created_by = ? ORDER BY priority_order ASC, id DESC", (selected_user,)).fetchall()
        db.close()
    return render_template_string(HTML_TEMPLATE, page='other_users_shops', selected_user=selected_user, shops=shops)

@app.route("/uploads/shop_photos/<path:filename>")
def uploaded_photo(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/admin/photo_permission/<int:user_id>/<action>")
def admin_photo_permission(user_id, action):
    if not session.get("user_id") or not is_admin():
        flash("ไม่มีสิทธิ์เข้าถึงส่วนนี้", "error")
        return redirect(url_for("home"))

    if action not in ("allow", "deny"):
        flash("คำสั่งไม่ถูกต้อง", "error")
        return redirect(url_for("manage_users"))

    db = get_db()
    user = db.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        db.close()
        flash("ไม่พบผู้ใช้งาน", "error")
        return redirect(url_for("manage_users"))

    allowed = 1 if action == "allow" else 0
    db.execute(
        """
        INSERT INTO photo_permissions (user_id, allowed, approved_by, approved_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET allowed=excluded.allowed, approved_by=excluded.approved_by, approved_at=excluded.approved_at
        """,
        (user_id, allowed, session.get("username"), datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    db.commit()
    db.close()
    flash(f"ตั้งค่าสิทธิ์ถ่ายรูปของ {user[0]} แล้ว", "success")
    return redirect(url_for("manage_users"))

@app.route("/admin/photo_system/<action>")
def admin_photo_system(action):
    if not session.get("user_id") or not is_admin():
        flash("เฉพาะ Admin เท่านั้น", "error")
        return redirect(url_for("home"))

    enabled = "1" if action == "on" else "0"
    db = get_db()
    db.execute(
        "INSERT INTO app_settings (setting_key, setting_value) VALUES (?, ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
        ("photo_system_enabled", enabled)
    )
    db.commit()
    db.close()
    flash("เปิดระบบถ่ายรูปแล้ว" if enabled == "1" else "ปิดระบบถ่ายรูปแล้ว", "success")
    return redirect(url_for("manage_users"))

@app.route("/manage_users")
def manage_users():
    if not session.get('user_id') or not is_admin():
        flash("เฉพาะ Admin เท่านั้น", "error")
        return redirect(url_for('home'))

    db = get_db()
    users_list = db.execute("SELECT * FROM users ORDER BY id ASC").fetchall()
    photo_setting = db.execute("SELECT setting_value FROM app_settings WHERE setting_key = ?", ("photo_system_enabled",)).fetchone()
    photo_system_is_on = bool(photo_setting and photo_setting[0] == "1")
    db.close()

    return render_template_string(HTML_TEMPLATE, page='manage_users', users_list=users_list, photo_system_is_on=photo_system_is_on)

@app.route("/admin_change_password", methods=["GET", "POST"])
def admin_change_password():
    if not session.get("user_id") or not is_admin():
        flash("เฉพาะ Admin เท่านั้น", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        db = get_db()
        admin = db.execute("SELECT * FROM users WHERE username = ?", ("admin",)).fetchone()
        if not admin or not check_password_hash(admin["password"], current_password):
            db.close()
            flash("รหัสผ่านเดิมไม่ถูกต้อง", "error")
            return redirect(url_for("admin_change_password"))

        if len(new_password) < 4 or new_password != confirm_password:
            db.close()
            flash("รหัสผ่านใหม่ต้องตรงกันและมีอย่างน้อย 4 ตัวอักษร", "error")
            return redirect(url_for("admin_change_password"))

        new_hash = generate_password_hash(new_password)
        db.execute("UPDATE users SET password = ? WHERE username = ?", (new_hash, "admin"))
        db.commit()
        db.close()

        flash("เปลี่ยนรหัสผ่าน Admin เรียบร้อยแล้ว", "success")
        return redirect(url_for("home"))

    return render_template_string(HTML_TEMPLATE, page="admin_change_password")

@app.route("/admin_reset_password/<int:user_id>", methods=["GET", "POST"])
def admin_reset_password(user_id):
    if not session.get("user_id") or not is_admin():
        flash("เฉพาะ Admin เท่านั้น", "error")
        return redirect(url_for("home"))

    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target or target["username"] == "admin":
        db.close()
        flash("ไม่สามารถเปลี่ยนรหัสผ่านของบัญชีนี้ได้", "error")
        return redirect(url_for("manage_users"))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(new_password) < 4 or new_password != confirm_password:
            db.close()
            flash("รหัสผ่านไม่ตรงกันหรือสั้นกว่า 4 ตัวอักษร", "error")
            return redirect(url_for("admin_reset_password", user_id=user_id))

        hashed_pw = generate_password_hash(new_password)
        db.execute("UPDATE users SET password = ? WHERE id = ?", (hashed_pw, user_id))
        db.commit()
        db.close()
        flash(f"เปลี่ยนรหัสผ่านของ '{target['username']}' สำเร็จ", "success")
        return redirect(url_for("manage_users"))

    db.close()
    return render_template_string(HTML_TEMPLATE, page="admin_reset_password", target_user=target)

@app.route("/delete_user/<int:user_id>")
def delete_user(user_id):
    if not session.get('user_id') or not is_admin():
        flash("เฉพาะ Admin เท่านั้น", "error")
        return redirect(url_for('home'))

    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if target and target['username'] != 'admin':
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.execute("DELETE FROM friends WHERE user_id = ? OR friend_id = ?", (user_id, user_id))
        db.commit()
        flash(f"ลบผู้ใช้งาน '{target['username']}' สำเร็จ", "success")
    else:
        flash("ไม่สามารถลบ admin หลักได้", "error")

    db.close()
    return redirect(url_for('manage_users'))

# =========================================================
# RUN SERVER (RENDER PORT BINDING)
# =========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
