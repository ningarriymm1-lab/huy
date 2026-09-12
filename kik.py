import os
import io
import sqlite3
from datetime import datetime
from flask import Flask, flash, redirect, render_template_string, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from PIL import Image, ImageOps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'CLEAN_SHOP_SECRET_2026_CHANGE_ME')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

UPLOAD_FOLDER = "shop_photos"
ALLOWED_PHOTO_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tif", "tiff", "ico", "ppm", "pgm", "pbm", "pnm", "avif", "jfif"}
MAX_PHOTO_SIZE = (1000, 700)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# =========================================================
# DATABASE
# =========================================================

def init_db():
    db = sqlite3.connect("clean_shop.db")

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
            checked_by_user TEXT
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


    # สิทธิ์ถ่ายรูป: ต้องให้ Admin อนุญาตก่อน
    db.execute("""
        CREATE TABLE IF NOT EXISTS photo_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE NOT NULL,
            allowed INTEGER DEFAULT 0,
            approved_by TEXT,
            approved_at TEXT
        )
    """)

    # เพิ่มช่องเก็บชื่อไฟล์รูปให้ฐานข้อมูลเดิม
    try:
        db.execute("ALTER TABLE shops ADD COLUMN photo_path TEXT")
    except sqlite3.OperationalError:
        pass

    # ตั้งค่าระบบรูปภาพแบบเปิด/ปิดโดย Admin
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

    # ตารางประวัติการโอนร้าน
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

    # ไม่มีบัญชี admin อัตโนมัติ
    # ผู้ใช้ทุกคนสมัครและเข้าสู่ระบบด้วยบัญชีของตัวเอง

    db.commit()
    db.close()


init_db()

# =========================================================
# ONE-TIME USER ACCOUNT RESET
# =========================================================
# ล้างบัญชีเดิมทั้งหมดเพียงครั้งเดียวหลังติดตั้งเวอร์ชันนี้
# ไม่ล้างซ้ำเมื่อผู้ใช้สมัครบัญชีใหม่และเปิดโปรแกรมครั้งต่อไป
RESET_MARKER = ".clean_shop_accounts_reset_done"
if not os.path.exists(RESET_MARKER):
    try:
        db = sqlite3.connect("clean_shop.db")
        db.execute("DELETE FROM friends")
        db.execute("DELETE FROM photo_permissions")
        db.execute("DELETE FROM users")
        # สร้าง Admin ใหม่ทันทีหลังล้างบัญชี
        db.execute(
            "INSERT INTO users (username, password, team_group) VALUES (?, ?, ?)",
            ("admin", generate_password_hash("admin555"), "ผู้ดูแลระบบ")
        )
        db.commit()
        db.close()
        with open(RESET_MARKER, "w", encoding="utf-8") as f:
            f.write("done")
    except Exception:
        try:
            db.close()
        except Exception:
            pass

# =========================================================
# ENSURE ADMIN ACCOUNT
# =========================================================
# ถ้าไม่มี admin ให้สร้างด้วยรหัสเริ่มต้น admin555
# ถ้ามี admin อยู่แล้ว จะไม่เขียนทับรหัสผ่าน เพื่อให้ Admin เปลี่ยนรหัสเองได้
try:
    db = sqlite3.connect("clean_shop.db")
    row = db.execute("SELECT id FROM users WHERE username = ?", ("admin",)).fetchone()
    if not row:
        admin_hash = generate_password_hash("admin555")
        db.execute(
            "INSERT INTO users (username, password, team_group) VALUES (?, ?, ?)",
            ("admin", admin_hash, "ผู้ดูแลระบบ")
        )
        db.commit()
    else:
        db.execute(
            "UPDATE users SET team_group = ? WHERE username = ?",
            ("ผู้ดูแลระบบ", "admin")
        )
        db.commit()
    db.close()
except Exception as e:
    print("ไม่สามารถตรวจสอบ Admin:", e)


# =========================================================
# PHOTO PERMISSION HELPERS
# =========================================================

def is_admin():
    return session.get("username") == "admin"


def photo_system_enabled():
    try:
        db = sqlite3.connect("clean_shop.db")
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

    db = sqlite3.connect("clean_shop.db")
    row = db.execute(
        "SELECT allowed FROM photo_permissions WHERE user_id = ?",
        (user_id,)
    ).fetchone()
    db.close()

    return bool(row and row[0] == 1)


def allowed_photo(filename):
    # รองรับนามสกุลรูปทั่วไป และตรวจชนิดไฟล์จริงด้วย Pillow ตอนบันทึก
    return bool(filename) and "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_PHOTO_EXTENSIONS


def save_resized_photo(file_storage, prefix="photo"):
    """รับรูปจากมือถือ/คอมทุกฟอร์แมตที่ Pillow เปิดได้ แล้วแปลงเป็น JPG
    พร้อมหมุนตาม EXIF และย่อให้เหมาะกับการแสดงในแท็บรายการ
    """
    if not file_storage or not file_storage.filename:
        return None

    try:
        file_storage.stream.seek(0)
        image = Image.open(file_storage.stream)
        image.verify()
        file_storage.stream.seek(0)
        image = Image.open(file_storage.stream)
        image = ImageOps.exif_transpose(image)

        # GIF/ภาพที่มีหลายเฟรม ใช้เฟรมแรกเพื่อให้ไฟล์รายการมีขนาดเล็ก
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
    except Exception:
        return None


app.jinja_env.globals["photo_allowed_for_current_user"] = photo_allowed_for_current_user
app.jinja_env.globals["photo_system_enabled"] = photo_system_enabled



# =========================================================
# HTML TEMPLATE
# =========================================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="th">

<head>
      
    <meta charset="UTF-8">

    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">

    <title>ระบบติดตามขนส่ง Chok Shop</title>

    <script src="https://cdn.tailwindcss.com"></script>

    <style>

        @import url(
            'https://fonts.googleapis.com/css2?family=Prompt:wght@300;400;500;600;700&display=swap'
        );

        body {
            font-family: 'Prompt', sans-serif;
        }

        /* =====================================================
           SEARCH HISTORY
           ===================================================== */

        .search-container {
            position: relative;
        }

        .search-history-dropdown {
            position: absolute;
            top: 100%;
            left: 0;
            right: 0;
            background: white;
            border: 1px solid #e5e7eb;
            border-radius: 0.5rem;
            box-shadow:
                0 10px 15px -3px rgba(0,0,0,0.1),
                0 4px 6px -2px rgba(0,0,0,0.05);
            z-index: 50;
            max-height: 250px;
            overflow-y: auto;
            display: none;
            margin-top: 4px;
        }

        .history-item {
            padding: 10px 16px;
            cursor: pointer;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #f3f4f6;
            font-size: 0.9rem;
            color: #374151;
        }

        .history-item:hover {
            background-color: #f9fafb;
            color: #1d4ed8;
        }

        .history-item-delete {
            color: #9ca3af;
            font-size: 0.8rem;
            padding: 2px 6px;
            border-radius: 4px;
        }

        .history-item-delete:hover {
            background-color: #fee2e2;
            color: #dc2626;
        }

        .history-header {
            padding: 8px 16px;
            font-size: 0.75rem;
            font-weight: 600;
            color: #6b7280;
            background-color: #f9fafb;
            border-bottom: 1px solid #e5e7eb;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .history-chips-container {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 8px;
        }

        .history-chip {
            background-color: #eff6ff;
            color: #1e40af;
            border: 1px solid #dbeafe;
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 0.8rem;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            text-decoration: none;
            transition: all 0.2s;
        }

        .history-chip:hover {
            background-color: #dbeafe;
            border-color: #bfdbfe;
        }


        /* =====================================================
           FLASH MESSAGE
           ===================================================== */

        .flash-message {
            opacity: 1;
            transform: translateY(0);
            transition:
                opacity 0.4s ease,
                transform 0.4s ease;
        }

        .flash-message.hide {
            opacity: 0;
            transform: translateY(-10px);
            pointer-events: none;
        }

    </style>

</head>


<body class="bg-gray-50 min-h-screen flex flex-col">


    <!-- =====================================================
         NAVBAR
         ===================================================== -->

    {% if session.get('user_id') %}

    <nav class="bg-blue-600 text-white shadow-md">

        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">

            <div class="flex justify-between h-16 items-center">

                <div class="flex items-center space-x-4">

                    <a href="{{ url_for('home') }}"
                       class="text-xl font-bold tracking-wider flex items-center gap-2">

                        🏪 Clean Shop

                    </a>


                    <div class="hidden md:flex space-x-2 text-sm">

                        <a href="{{ url_for('home') }}"
                           class="px-3 py-2 rounded-md hover:bg-blue-700 transition">
                            หน้าแรก
                        </a>

                        <a href="{{ url_for('shops_list') }}"
                           class="px-3 py-2 rounded-md hover:bg-blue-700 transition">
                            ร้านของฉัน
                        </a>

                        <a href="{{ url_for('add_shop') }}"
                           class="px-3 py-2 rounded-md hover:bg-blue-700 transition">
                            เพิ่มร้านค้า
                        </a>

                        <a href="{{ url_for('friends_page') }}"
                           class="px-3 py-2 rounded-md hover:bg-blue-700 transition">
                            ระบบเพื่อน
                        </a>

                        <a href="{{ url_for('transfer_history') }}"
                           class="px-3 py-2 rounded-md hover:bg-blue-700 transition">
                            ประวัติโอนร้านส่ง
                        </a>

                        {% if session.get('username') == 'admin' %}
                            <a href="{{ url_for('manage_users') }}"
                               class="px-3 py-2 rounded-md hover:bg-blue-700 transition">
                                🛡️ จัดการผู้ใช้
                            </a>
                        {% endif %}

                        {% if session.get('username') == 'admin' %}
                            <a href="{{ url_for('admin_change_password') }}"
                               class="px-3 py-2 rounded-md hover:bg-blue-700 transition">
                                🔐 เปลี่ยนรหัส Admin
                            </a>
                        {% endif %}

                    </div>

                </div>


                <div class="flex items-center space-x-3">

                    <span class="text-sm bg-blue-700 px-3 py-1.5 rounded-full font-medium">

                        👤 {{ session.get('username') }}

                        {% if session.get('username') == 'admin' %}
                            (Admin)
                        {% endif %}

                    </span>

                    <a href="{{ url_for('logout') }}"
                       class="bg-red-500 hover:bg-red-600 text-white px-3 py-1.5 rounded-md text-sm transition">

                        ออกจากระบบ

                    </a>

                </div>

            </div>

        </div>


        <!-- Mobile -->

        <div class="md:hidden bg-blue-700 px-4 py-2 flex flex-wrap gap-2 text-xs">

            <a href="{{ url_for('home') }}"
               class="px-2.5 py-1 bg-blue-800 rounded">
                หน้าแรก
            </a>

            <a href="{{ url_for('shops_list') }}"
               class="px-2.5 py-1 bg-blue-800 rounded">
                ร้านของฉัน
            </a>

            <a href="{{ url_for('add_shop') }}"
               class="px-2.5 py-1 bg-blue-800 rounded">
                เพิ่มร้าน
            </a>

            <a href="{{ url_for('friends_page') }}"
               class="px-2.5 py-1 bg-blue-800 rounded">
                เพื่อน
            </a>

            <a href="{{ url_for('transfer_history') }}"
               class="px-2.5 py-1 bg-blue-800 rounded">
                ประวัติโอนร้านส่ง
            </a>

            {% if session.get('username') == 'admin' %}
                <a href="{{ url_for('manage_users') }}"
                   class="px-2.5 py-1 bg-red-600 rounded">
                    🛡️ จัดการผู้ใช้
                </a>
            {% endif %}

        </div>

    </nav>

    {% endif %}


    <!-- =====================================================
         FLASH MESSAGES
         ===================================================== -->

    <div id="flash-container"
         class="max-w-4xl mx-auto w-full px-4 mt-4">

        {% with messages = get_flashed_messages(with_categories=true) %}

            {% if messages %}

                {% for category, message in messages %}

                    {% if category == 'success' %}

                    <div class="flash-message
                                bg-green-100
                                border-l-4
                                border-green-500
                                text-green-700
                                p-4
                                mb-3
                                rounded
                                shadow-sm
                                text-sm
                                flex
                                justify-between
                                items-center"
                         role="alert">

                        <p>
                            ✅ {{ message }}
                        </p>

                        <button
                            type="button"
                            onclick="closeFlash(this)"
                            class="ml-4 text-green-700 hover:text-green-900 font-bold text-xl leading-none">

                            ×

                        </button>

                    </div>

                    {% else %}

                    <div class="flash-message
                                bg-red-100
                                border-l-4
                                border-red-500
                                text-red-700
                                p-4
                                mb-3
                                rounded
                                shadow-sm
                                text-sm
                                flex
                                justify-between
                                items-center"
                         role="alert">

                        <p>
                            ⚠️ {{ message }}
                        </p>

                        <button
                            type="button"
                            onclick="closeFlash(this)"
                            class="ml-4 text-red-700 hover:text-red-900 font-bold text-xl leading-none">

                            ×

                        </button>

                    </div>

                    {% endif %}

                {% endfor %}

            {% endif %}

        {% endwith %}

    </div>


    <!-- =====================================================
         MAIN
         ===================================================== -->

    <main class="flex-grow max-w-7xl mx-auto w-full px-4 sm:px-6 lg:px-8 py-6">


        {% if page == 'login' %}

        <div class="max-w-md mx-auto bg-white rounded-xl shadow-md overflow-hidden mt-12 p-8 border border-gray-100">

            <div class="text-center mb-6">

                <h2 class="text-2xl font-bold text-gray-800">
                    เข้าสู่ระบบ
                </h2>

                <p class="text-sm text-gray-500 mt-1">
                    ระบบจัดการร้านค้า Clean Shop
                </p>

            </div>


            <form method="POST" enctype="multipart/form-data" class="space-y-4">

                <div>

                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        ชื่อผู้ใช้งาน
                    </label>

                    <input
                        type="text"
                        name="username"
                        required
                        class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm">

                </div>


                <div>

                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        รหัสผ่าน
                    </label>

                    <input
                        type="password"
                        name="password"
                        required
                        class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm">

                </div>


                <button
                    type="submit"
                    class="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2.5 rounded-lg transition text-sm shadow">

                    เข้าสู่ระบบ

                </button>

            </form>


            <div class="text-center mt-6 text-sm text-gray-600">

                ยังไม่มีบัญชีใช่ไหม?

                <a href="{{ url_for('register') }}"
                   class="text-blue-600 font-medium hover:underline">

                    สมัครสมาชิก

                </a>

            </div>

        </div>


        {% elif page == 'register' %}

        <div class="max-w-md mx-auto bg-white rounded-xl shadow-md overflow-hidden mt-12 p-8 border border-gray-100">

            <div class="text-center mb-6">

                <h2 class="text-2xl font-bold text-gray-800">
                    สมัครสมาชิก
                </h2>

                <p class="text-sm text-gray-500 mt-1">
                    สร้างบัญชีผู้ใช้งานใหม่
                </p>

            </div>


            <form method="POST" class="space-y-4">

                <div>

                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        ชื่อผู้ใช้งาน
                    </label>

                    <input
                        type="text"
                        name="username"
                        required
                        class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm">

                </div>


                <div>

                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        รหัสผ่าน
                    </label>

                    <input
                        type="password"
                        name="password"
                        required
                        class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm">

                </div>


                <div>

                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        กลุ่มทีมปฏิบัติงาน
                    </label>

                    <input
                        type="text"
                        name="team_group"
                        value="ทีมปฏิบัติการ"
                        class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm">

                </div>


                <button
                    type="submit"
                    class="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-2.5 rounded-lg transition text-sm shadow">

                    สมัครสมาชิก

                </button>

            </form>


            <div class="text-center mt-6 text-sm text-gray-600">

                มีบัญชีอยู่แล้ว?

                <a href="{{ url_for('login') }}"
                   class="text-blue-600 font-medium hover:underline">

                    เข้าสู่ระบบ

                </a>

            </div>

        </div>


        {% elif page == 'home' %}

        <div class="space-y-6">

            <div class="bg-gradient-to-r from-blue-600 to-indigo-700 rounded-2xl shadow-lg p-6 sm:p-10 text-white flex flex-col md:flex-row justify-between items-center gap-6">

                <div>

                    <h1 class="text-2xl sm:text-3xl font-bold">
                        ยินดีต้อนรับ, {{ session.get('username') }}!
                    </h1>

                    <p class="text-blue-100 mt-2 text-sm sm:text-base">
                        ระบบจัดการและติดตามร้านค้า สะดวก รวดเร็ว และทำงานร่วมกันเป็นทีม
                    </p>

                </div>


                <div class="flex flex-wrap gap-3">

                    <a href="{{ url_for('add_shop') }}"
                       class="bg-white text-blue-700 hover:bg-blue-50 font-medium px-4 py-2.5 rounded-xl shadow transition text-sm">

                        + เพิ่มร้านค้าใหม่

                    </a>

                    <a href="{{ url_for('shops_list') }}"
                       class="bg-blue-800 hover:bg-blue-900 text-white font-medium px-4 py-2.5 rounded-xl shadow transition text-sm">

                        ร้านค้าของฉัน

                    </a>

                </div>

            </div>


            <div class="grid grid-cols-1 md:grid-cols-4 gap-6">

                <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100 flex items-center space-x-4">

                    <div class="p-3 bg-blue-50 text-blue-600 rounded-xl text-2xl font-bold">
                        🏪
                    </div>

                    <div>

                        <p class="text-sm text-gray-500 font-medium">
                            เมนูด่วน
                        </p>

                        <a href="{{ url_for('shops_list') }}"
                           class="text-blue-600 hover:underline font-semibold text-base">

                            จัดการร้านทั้งหมด →

                        </a>

                    </div>

                </div>


                <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100 flex items-center space-x-4">

                    <div class="p-3 bg-indigo-50 text-indigo-600 rounded-xl text-2xl font-bold">
                        👥
                    </div>

                    <div>

                        <p class="text-sm text-gray-500 font-medium">
                            เพื่อนร่วมงาน
                        </p>

                        <a href="{{ url_for('friends_page') }}"
                           class="text-indigo-600 hover:underline font-semibold text-base">

                            ดูรายชื่อเพื่อน →

                        </a>

                    </div>

                </div>


                <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100 flex items-center space-x-4">

                    <div class="p-3 bg-amber-50 text-amber-600 rounded-xl text-2xl font-bold">
                        📦
                    </div>

                    <div>

                        <p class="text-sm text-gray-500 font-medium">
                            โอนร้านส่ง
                        </p>

                        <a href="{{ url_for('transfer_history') }}"
                           class="text-amber-600 hover:underline font-semibold text-base">

                            ประวัติโอนส่ง →

                        </a>

                    </div>

                </div>


                <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100 flex items-center space-x-4">

                    <div class="p-3 bg-red-50 text-red-600 rounded-xl text-2xl font-bold">
                        🔄
                    </div>

                    <div>

                        <p class="text-sm text-gray-500 font-medium">
                            รีเซ็ตสถานะ
                        </p>

                        <a href="{{ url_for('reset_checkins') }}"
                           class="text-red-600 hover:underline font-semibold text-base">

                            รีเซ็ตเช็คอิน →

                        </a>

                    </div>

                </div>

            </div>

        </div>


        {% elif page == 'add_shop' or page == 'edit_shop' %}

        <div class="max-w-xl mx-auto bg-white rounded-2xl shadow-sm border border-gray-100 p-6 sm:p-8">

            <h2 class="text-xl font-bold text-gray-800 mb-6">

                {% if page == 'add_shop' %}
                    ➕ เพิ่มร้านค้าใหม่
                {% else %}
                    ✏️ แก้ไขข้อมูลร้านค้า
                {% endif %}

            </h2>


            <form method="POST" class="space-y-4">

                <div>

                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        ชื่อร้านค้า
                    </label>

                    <input
                        type="text"
                        name="name"
                        value="{{ shop.name if shop else '' }}"
                        required
                        class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm">

                </div>


                <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">

                    <div>

                        <label class="block text-sm font-medium text-gray-700 mb-1">
                            อำเภอ / เขต
                        </label>

                        <input
                            type="text"
                            name="district"
                            value="{{ shop.district if shop else '' }}"
                            class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm">

                    </div>


                    <div>

                        <label class="block text-sm font-medium text-gray-700 mb-1">
                            เบอร์โทรศัพท์
                        </label>

                        <input
                            type="text"
                            name="phone"
                            value="{{ shop.phone if shop else '' }}"
                            class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm">

                    </div>

                </div>


                <div>

                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        ที่อยู่ร้านค้า
                    </label>

                    <textarea
                        name="address"
                        rows="3"
                        class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm">{{ shop.address if shop else '' }}</textarea>

                </div>


                <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">

                    <div>

                        <label class="block text-sm font-medium text-gray-700 mb-1">
                            ลำดับความสำคัญ
                        </label>

                        <input
                            type="number"
                            name="priority_order"
                            value="{{ shop.priority_order if shop else 1 }}"
                            class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm">

                    </div>


                    <div>

                        <label class="block text-sm font-medium text-gray-700 mb-1">
                            สภาพร้าน
                        </label>

                        <select
                            name="shop_condition"
                            class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm">

                            <option value="เปิดปกติ"
                                {% if shop and shop.shop_condition == 'เปิดปกติ' %}
                                selected
                                {% endif %}>
                                เปิดปกติ
                            </option>

                            <option value="ปิดกิจการ"
                                {% if shop and shop.shop_condition == 'ปิดกิจการ' %}
                                selected
                                {% endif %}>
                                ปิดกิจการ
                            </option>

                        </select>

                    </div>

                </div>


                <div class="flex justify-end space-x-3 pt-4 border-t">

                    <a href="{{ url_for('shops_list') }}"
                       class="px-4 py-2 border rounded-lg text-gray-600 hover:bg-gray-100 text-sm transition">

                        ยกเลิก

                    </a>


                                    <button
                        type="submit"
                        class="px-5 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-medium shadow transition">

                        บันทึกข้อมูล

                    </button>

                </div>

            </form>

        </div>


        {% elif page == 'transfer_shop' %}

        <div class="max-w-md mx-auto bg-white rounded-2xl shadow-sm border border-gray-100 p-6 sm:p-8">

            <h2 class="text-xl font-bold text-gray-800 mb-2">
                📦 ระบบโอนร้านส่ง
            </h2>

            <p class="text-sm text-gray-500 mb-6">

                โอนสิทธิ์ความรับผิดชอบและส่งต่อร้าน
                <span class="font-semibold text-gray-800">
                    "{{ shop.name }}"
                </span>

            </p>


            <form method="POST" class="space-y-4">

                <div>

                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        ชื่อผู้ใช้งานปลายทาง
                    </label>

                    <input
                        type="text"
                        name="target_user"
                        required
                        placeholder="ระบุ username ปลายทาง"
                        class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm">

                </div>


                <div class="flex justify-end space-x-3 pt-4 border-t">

                    <a href="{{ url_for('shops_list') }}"
                       class="px-4 py-2 border rounded-lg text-gray-600 hover:bg-gray-100 text-sm transition">

                        ยกเลิก

                    </a>


                    <button
                        type="submit"
                        class="px-5 py-2 bg-amber-600 text-white rounded-lg hover:bg-amber-700 text-sm font-medium shadow transition">

                        📦 ยืนยันโอนร้านส่ง

                    </button>

                </div>

            </form>

        </div>


        {% elif page == 'transfer_history' %}

        <div class="space-y-6">

            <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">

                <div>

                    <h2 class="text-2xl font-bold text-gray-800">
                        📦 ประวัติการโอนร้านส่ง
                    </h2>

                    <p class="text-sm text-gray-500 mt-1">
                        ประวัติการส่งต่อและความรับผิดชอบร้านค้าระหว่างสมาชิก
                    </p>

                </div>


                <a href="{{ url_for('shops_list') }}"
                   class="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 text-sm font-medium rounded-xl transition">

                    ← กลับหน้าร้านค้า

                </a>

            </div>


            {% if transfers %}

            <div class="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">

                <div class="overflow-x-auto">

                    <table class="w-full text-left border-collapse">

                        <thead>

                            <tr class="bg-gray-100 text-gray-700 text-xs uppercase font-semibold">

                                <th class="p-4">
                                    ลำดับ
                                </th>

                                <th class="p-4">
                                    ชื่อร้านค้า
                                </th>

                                <th class="p-4">
                                    ผู้ส่งมอบ
                                </th>

                                <th class="p-4">
                                    ผู้รับมอบ
                                </th>

                                <th class="p-4">
                                    วันเวลาที่โอน
                                </th>

                            </tr>

                        </thead>


                        <tbody class="divide-y divide-gray-200 text-sm">

                            {% for t in transfers %}

                            <tr>

                                <td class="p-4 font-medium text-gray-500">
                                    {{ loop.index }}
                                </td>

                                <td class="p-4 font-bold text-gray-900">
                                    {{ t.shop_name }}
                                </td>

                                <td class="p-4 text-blue-600 font-medium">
                                    👤 {{ t.from_user }}
                                </td>

                                <td class="p-4 text-emerald-600 font-medium">
                                    👤 {{ t.to_user }}
                                </td>

                                <td class="p-4 text-gray-600 text-xs">
                                    {{ t.transferred_at }}
                                </td>

                            </tr>

                            {% endfor %}

                        </tbody>

                    </table>

                </div>

            </div>

            {% else %}

            <div class="bg-white rounded-2xl p-12 text-center border border-gray-100 shadow-sm">

                <p class="text-gray-400 text-lg">
                    ยังไม่มีประวัติการโอนร้านส่งในระบบ
                </p>

            </div>

            {% endif %}

        </div>


        {% elif page == 'reset_checkins' %}

        <div class="max-w-md mx-auto bg-white rounded-2xl shadow-sm border border-gray-100 p-6 sm:p-8">

            <h2 class="text-xl font-bold text-red-600 mb-2">
                ⚠️ รีเซ็ตสถานะเช็คอินทั้งหมด
            </h2>

            <p class="text-sm text-gray-500 mb-6">

                การกระทำนี้จะเปลี่ยนสถานะร้านค้าทั้งหมดของคุณกลับเป็น
                <span class="font-semibold">
                    "ยังไม่เช็คอิน"
                </span>

                และล้างเวลาเช็คอินทั้งหมด

            </p>


            <form method="POST" class="space-y-4">

                <div>

                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        พิมพ์คำว่า RESET
                    </label>

                    <input
                        type="text"
                        name="confirm_text"
                        required
                        placeholder="RESET"
                        class="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 text-sm">

                </div>


                <div class="flex justify-end space-x-3 pt-4 border-t">

                    <a href="{{ url_for('home') }}"
                       class="px-4 py-2 border rounded-lg text-gray-600 hover:bg-gray-100 text-sm transition">

                        ยกเลิก

                    </a>


                    <button
                        type="submit"
                        class="px-5 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 text-sm font-medium shadow transition">

                        ยืนยันรีเซ็ต

                    </button>

                </div>

            </form>

        </div>


        {% elif page == 'shops_list' %}

        <div class="space-y-6">

            <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 bg-white p-6 rounded-2xl shadow-sm border border-gray-100">

                <div>

                    <h2 class="text-2xl font-bold text-gray-800">
                        🏪 ร้านค้าของฉัน
                    </h2>

                    <p class="text-sm text-gray-500 mt-1">
                        จัดการและติดตามสถานะร้านค้าที่คุณรับผิดชอบ
                    </p>

                </div>


                <div class="flex flex-wrap gap-2">

                    <a href="{{ url_for('shops_list', filter='all') }}"
                       class="px-3.5 py-1.5 rounded-lg text-sm font-medium
                       {% if current_filter == 'all' %}
                       bg-blue-600 text-white
                       {% else %}
                       bg-gray-100 text-gray-700 hover:bg-gray-200
                       {% endif %}
                       transition">

                        ทั้งหมด

                    </a>


                    <a href="{{ url_for('shops_list', filter='open') }}"
                       class="px-3.5 py-1.5 rounded-lg text-sm font-medium
                       {% if current_filter == 'open' %}
                       bg-blue-600 text-white
                       {% else %}
                       bg-gray-100 text-gray-700 hover:bg-gray-200
                       {% endif %}
                       transition">

                        เปิดปกติ

                    </a>


                    <a href="{{ url_for('shops_list', filter='closed') }}"
                       class="px-3.5 py-1.5 rounded-lg text-sm font-medium
                       {% if current_filter == 'closed' %}
                       bg-blue-600 text-white
                       {% else %}
                       bg-gray-100 text-gray-700 hover:bg-gray-200
                       {% endif %}
                       transition">

                        ปิดกิจการ

                    </a>


                    <a href="{{ url_for('add_shop') }}"
                       class="px-4 py-1.5 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700 shadow transition">

                        + เพิ่มร้าน

                    </a>

                </div>

            </div>


            {% if shops %}

            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">

                {% for shop in shops %}

                <div class="bg-white rounded-2xl shadow-sm border border-gray-100 p-6 flex flex-col justify-between transition hover:shadow-md">

                    <div>

                        <div class="flex justify-between items-start gap-2 mb-3">

                            <h3 class="font-bold text-lg text-gray-900 leading-snug">
                                {{ shop.name }}
                            </h3>


                            <span class="px-2.5 py-1 rounded-full text-xs font-semibold whitespace-nowrap

                            {% if shop.shop_condition == 'เปิดปกติ' %}
                                bg-green-100 text-green-800
                            {% else %}
                                bg-red-100 text-red-800
                            {% endif %}">

                                {{ shop.shop_condition }}

                            </span>

                        </div>


                        <p class="text-xs text-gray-500 mb-1">
                            📍 อำเภอ:
                            <span class="text-gray-700 font-medium">
                                {{ shop.district or '-' }}
                            </span>
                        </p>


                        <p class="text-xs text-gray-500 mb-1">
                            🏠 ที่อยู่:
                            <span class="text-gray-700">
                                {{ shop.address or '-' }}
                            </span>
                        </p>


                        <p class="text-xs text-gray-500 mb-3">
                            📞 โทร:
                            <span class="text-gray-700 font-medium">
                                {{ shop.phone or '-' }}
                            </span>
                        </p>


                        <div class="bg-gray-50 p-3 rounded-xl mb-4 text-xs space-y-1">

                            <div class="flex justify-between">

                                <span class="text-gray-500">
                                    ลำดับความสำคัญ:
                                </span>

                                <span class="font-bold text-blue-600">
                                    #{{ shop.priority_order }}
                                </span>

                            </div>


                            <div class="flex justify-between">

                                <span class="text-gray-500">
                                    สถานะเช็คอิน:
                                </span>

                                <span class="font-semibold

                                {% if shop.status == 'ถึงร้านแล้ว' %}
                                    text-green-600
                                {% else %}
                                    text-gray-600
                                {% endif %}">

                                    {{ shop.status }}

                                </span>

                            </div>


                            {% if shop.checked_at %}

                            <div class="flex justify-between">

                                <span class="text-gray-500">
                                    เวลาเช็คอิน:
                                </span>

                                <span class="text-gray-700">
                                    {{ shop.checked_at }}
                                </span>

                            </div>

                            {% endif %}

                        </div>

                    </div>


                    {% if shop.photo_path and photo_system_enabled() %}
                    <div class="mb-3">
                        <img src="{{ url_for('uploaded_photo', filename=shop.photo_path) }}" class="w-full h-48 sm:h-56 object-cover rounded-xl border border-gray-200 bg-gray-100" alt="รูปงาน {{ shop.name }}">
                    </div>
                    {% endif %}

                    <div class="space-y-2 pt-3 border-t border-gray-100">

                        {% if shop.status != 'ถึงร้านแล้ว' %}

                        <form
                            action="{{ url_for('checkin_shop', shop_id=shop.id) }}"
                            method="POST"
                            enctype="multipart/form-data">

                            {% if session.get('username') == 'admin' or photo_allowed_for_current_user() %}
                            <div class="mb-2 p-3 rounded-lg bg-blue-50 border border-blue-100">
                                <label class="block text-xs font-semibold text-blue-800 mb-2">
                                    📷 รูปยืนยันการเข้าร้าน
                                </label>
                                <div>
                                    <label class="inline-flex items-center justify-center gap-1 px-3 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold cursor-pointer">
                                        📷 ถ่ายรูป
                                        <input type="file" name="checkin_camera_photo" accept="image/*" capture="environment" class="hidden" onchange="setCheckinPhoto(this)">
                                    </label>
                                </div>
                                <span id="checkin-photo-name" class="block text-[10px] text-gray-400 mt-1">ยังไม่ได้ถ่ายรูป</span>
                                <p class="text-[11px] text-gray-500 mt-1">กดปุ่ม 📷 เพื่อถ่ายรูปจากกล้องมือถือ</p>
                            </div>
                            {% else %}
                            <div class="mb-2 p-2 rounded-lg bg-amber-50 border border-amber-100 text-xs text-amber-700">
                                🔒 ยังไม่ได้รับอนุญาตใช้ระบบถ่ายรูปจาก Admin
                            </div>
                            {% endif %}

                            <button
                                type="submit"
                                class="w-full py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-xs font-medium shadow-sm transition">

                                {% if session.get('username') == 'admin' or photo_allowed_for_current_user() %}
                                    📷✅ เช็คอิน + ยืนยันรูป
                                {% else %}
                                    ✅ เช็คอิน (ถึงร้านแล้ว)
                                {% endif %}

                            </button>

                        </form>

                        {% endif %}


                        <div class="flex gap-2">

                            <a href="{{ url_for('edit_shop', shop_id=shop.id) }}"
                               class="flex-1 py-1.5 bg-blue-50 text-blue-700 hover:bg-blue-100 text-center rounded-lg text-xs font-medium transition">

                                ✏️ แก้ไข

                            </a>


                            <a href="{{ url_for('transfer_shop', shop_id=shop.id) }}"
                               class="flex-1 py-1.5 bg-amber-50 text-amber-700 hover:bg-amber-100 text-center rounded-lg text-xs font-medium transition">

                                📦 โอนร้านส่ง

                            </a>


                            <a href="{{ url_for('toggle_condition', shop_id=shop.id) }}"
                               class="py-1.5 px-3 bg-gray-100 text-gray-700 hover:bg-gray-200 text-center rounded-lg text-xs font-medium transition">

                                ⚙️

                            </a>


                            <a href="{{ url_for('delete_shop', shop_id=shop.id) }}"
                               onclick="return confirm('ยืนยันการลบร้านค้านี้?')"
                               class="py-1.5 px-3 bg-red-50 text-red-600 hover:bg-red-100 text-center rounded-lg text-xs font-medium transition">

                                🗑️

                            </a>

                        </div>

                    </div>

                </div>

                {% endfor %}

            </div>

            {% else %}

            <div class="bg-white rounded-2xl p-12 text-center border border-gray-100 shadow-sm">

                <p class="text-gray-400 text-lg mb-3">
                    ยังไม่มีข้อมูลร้านค้าในหมวดหมู่นี้
                </p>

                <a href="{{ url_for('add_shop') }}"
                   class="inline-block px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-xl hover:bg-blue-700 transition shadow">

                    + เพิ่มร้านค้าใหม่

                </a>

            </div>

            {% endif %}

        </div>


        {% elif page == 'friends' %}

        <div class="space-y-6">

            <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">

                <h2 class="text-2xl font-bold text-gray-800 mb-4">
                    👥 ระบบเพื่อนร่วมงาน
                </h2>


                <form
                    method="GET"
                    action="{{ url_for('friends_page') }}"
                    class="relative search-container">

                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        ค้นหาเพื่อนร่วมงาน (Username)
                    </label>


                    <div class="flex gap-2">

                        <input
                            type="text"
                            id="userSearchInput"
                            name="search_user"
                            value="{{ search_query }}"
                            placeholder="พิมพ์ชื่อผู้ใช้งานเพื่อค้นหา..."
                            autocomplete="off"
                            onfocus="showHistory()"
                            oninput="filterHistory()"
                            onblur="hideHistory()"
                            class="flex-1 px-4 py-2 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm">


                        <button
                            type="submit"
                            onclick="saveSearchHistory()"
                            class="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-xl text-sm transition shadow">

                            ค้นหา

                        </button>

                    </div>


                    <div id="searchHistoryDropdown"
                         class="search-history-dropdown">
                    </div>

                </form>


                <div id="quickHistoryPanel"
                     style="display:none;"
                     class="mt-3">

                    <div class="text-xs font-semibold text-gray-500 mb-1">
                        ประวัติการค้นหาล่าสุด:
                    </div>

                    <div id="quickChipsContainer"
                         class="history-chips-container">
                    </div>

                </div>

            </div>


            {% if search_query %}

            <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">

                <h3 class="text-lg font-bold text-gray-800 mb-4">

                    ผลการค้นหาสำหรับ
                    "{{ search_query }}"

                </h3>


                {% if search_results %}

                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">

                    {% for u in search_results %}

                    <div class="border border-gray-200 p-4 rounded-xl flex justify-between items-center bg-gray-50">

                        <div>

                            <p class="font-bold text-gray-800 text-sm">
                                {{ u.username }}
                            </p>

                            <p class="text-xs text-gray-500">
                                {{ u.team_group }}
                            </p>

                        </div>


                        <a href="{{ url_for('add_friend', friend_id=u.id) }}"
                           class="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium rounded-lg transition shadow-sm">

                            + เพิ่มเพื่อน

                        </a>

                    </div>

                    {% endfor %}

                </div>

                {% else %}

                <p class="text-gray-500 text-sm">
                    ไม่พบผู้ใช้งานที่ตรงกัน หรือเป็นเพื่อนกันอยู่แล้ว
                </p>

                {% endif %}

            </div>

            {% endif %}


            {% if pending_requests %}

            <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">

                <h3 class="text-lg font-bold text-gray-800 mb-4">
                    📬 คำขอเป็นเพื่อนที่รอการตอบรับ
                </h3>


                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">

                    {% for req in pending_requests %}

                    <div class="border border-yellow-200 bg-yellow-50 p-4 rounded-xl flex justify-between items-center">

                        <div>

                            <p class="font-bold text-gray-800 text-sm">
                                {{ req.username }}
                            </p>

                            <p class="text-xs text-gray-500">
                                {{ req.team_group }}
                            </p>

                        </div>


                        <div class="flex gap-2">

                            <a href="{{ url_for('accept_friend', req_id=req.rel_id) }}"
                               class="px-3 py-1.5 bg-green-600 hover:bg-green-700 text-white text-xs font-medium rounded-lg transition shadow-sm">

                                ยอมรับ

                            </a>


                            <a href="{{ url_for('remove_friend', rel_id=req.rel_id) }}"
                               class="px-3 py-1.5 bg-red-100 hover:bg-red-200 text-red-700 text-xs font-medium rounded-lg transition">

                                ปฏิเสธ

                            </a>

                        </div>

                    </div>

                    {% endfor %}

                </div>

            </div>

            {% endif %}


            <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">

                <h3 class="text-lg font-bold text-gray-800 mb-4">
                     รายชื่อเพื่อนร่วมงานของฉัน
                </h3>


                {% if my_friends %}

                <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">

                    {% for f in my_friends %}

                    <div class="border border-gray-200 p-4 rounded-xl flex justify-between items-center bg-white shadow-sm">

                        <div>

                            <p class="font-bold text-gray-800 text-sm">
                                {{ f.username }}
                            </p>

                            <p class="text-xs text-gray-500">
                                {{ f.team_group }}
                            </p>


                            <a href="{{ url_for('other_users_shops', user=f.username) }}"
                               class="text-xs text-blue-600 hover:underline font-medium mt-1 inline-block">

                                🔍 ดูร้านค้าของเพื่อน →

                            </a>

                        </div>


                        <a href="{{ url_for('remove_friend', rel_id=f.rel_id) }}"
                           onclick="return confirm('ยืนยันการลบเพื่อน?')"
                           class="text-gray-400 hover:text-red-600 p-2 text-sm transition">

                            🗑️

                        </a>

                    </div>

                    {% endfor %}

                </div>

                {% else %}

                <p class="text-gray-500 text-sm">
                    ยังไม่มีรายชื่อเพื่อนในระบบ
                </p>

                {% endif %}

            </div>

        </div>


        {% elif page == 'other_users_shops' %}

        <div class="space-y-6">

            <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">

                <div>

                    <h2 class="text-2xl font-bold text-gray-800">

                        🏪 ร้านค้าของเพื่อน:
                        <span class="text-blue-600">
                            {{ selected_user }}
                        </span>

                    </h2>

                    <p class="text-sm text-gray-500 mt-1">
                        ตรวจสอบรายชื่อร้านค้าที่เพื่อนร่วมงานรับผิดชอบ
                    </p>

                </div>


                <a href="{{ url_for('friends_page') }}"
                   class="px-4 py-2 bg-gray-100 hover:bg-gray-200 text-gray-700 text-sm font-medium rounded-xl transition">

                    ← กลับหน้าระบบเพื่อน

                </a>

            </div>


            {% if shops %}

            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">

                {% for shop in shops %}

                <div class="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">

                    <h3 class="font-bold text-lg text-gray-900 mb-3">
                        {{ shop.name }}
                    </h3>


                    <p class="text-xs text-gray-500 mb-1">
                        📍 อำเภอ:
                        <span class="text-gray-700">
                            {{ shop.district or '-' }}
                        </span>
                    </p>


                    <p class="text-xs text-gray-500 mb-1">
                        🏠 ที่อยู่:
                        <span class="text-gray-700">
                            {{ shop.address or '-' }}
                        </span>
                    </p>


                    <p class="text-xs text-gray-500 mb-3">
                        📞 โทร:
                        <span class="text-gray-700">
                            {{ shop.phone or '-' }}
                        </span>
                    </p>


                    <div class="bg-gray-50 p-3 rounded-xl text-xs space-y-1">

                        <div class="flex justify-between">

                            <span class="text-gray-500">
                                ลำดับความสำคัญ:
                            </span>

                            <span class="font-bold text-blue-600">
                                #{{ shop.priority_order }}
                            </span>

                        </div>


                        <div class="flex justify-between">

                            <span class="text-gray-500">
                                สถานะ:
                            </span>

                            <span>
                                {{ shop.status }}
                            </span>

                        </div>

                    </div>

                </div>

                {% endfor %}

            </div>

            {% else %}

            <div class="bg-white rounded-2xl p-12 text-center border border-gray-100 shadow-sm">

                <p class="text-gray-400 text-lg">
                    เพื่อนรายนี้ยังไม่มีร้านค้าในระบบ
                </p>

            </div>

            {% endif %}

        </div>


        {% elif page == 'admin_reset_password' %}

        <div class="max-w-md mx-auto bg-white rounded-2xl shadow-sm border border-gray-100 p-6 sm:p-8">

            <div class="text-center mb-6">

                <div class="text-4xl mb-3">🔐</div>

                <h2 class="text-2xl font-bold text-red-600">
                    เปลี่ยนรหัสผ่านผู้ใช้งาน
                </h2>

                <p class="text-sm text-gray-500 mt-2">
                    ผู้ใช้งาน:
                    <span class="font-bold text-gray-800">
                        {{ target_user.username }}
                    </span>
                </p>

                <p class="text-xs text-gray-400 mt-1">
                    Admin สามารถตั้งรหัสผ่านใหม่ให้บัญชีนี้ได้
                </p>

            </div>

            <form method="POST" class="space-y-4">

                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        รหัสผ่านใหม่
                    </label>

                    <input
                        type="password"
                        name="new_password"
                        minlength="4"
                        required
                        autocomplete="new-password"
                        placeholder="กรอกรหัสผ่านใหม่"
                        class="w-full px-4 py-2.5 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-red-500 text-sm">
                </div>

                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        ยืนยันรหัสผ่านใหม่
                    </label>

                    <input
                        type="password"
                        name="confirm_password"
                        minlength="4"
                        required
                        autocomplete="new-password"
                        placeholder="กรอกรหัสผ่านใหม่อีกครั้ง"
                        class="w-full px-4 py-2.5 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-red-500 text-sm">
                </div>

                <div class="bg-yellow-50 border border-yellow-200 text-yellow-800 rounded-xl p-3 text-xs">
                    ⚠️ หลังเปลี่ยนรหัสผ่าน ผู้ใช้งานจะต้องใช้รหัสผ่านใหม่นี้ในการเข้าสู่ระบบครั้งถัดไป
                </div>

                <div class="flex gap-2 pt-2">

                    <a
                        href="{{ url_for('manage_users') }}"
                        class="flex-1 text-center px-4 py-2.5 border border-gray-300 rounded-xl text-gray-700 hover:bg-gray-100 text-sm font-medium transition">
                        ยกเลิก
                    </a>

                    <button
                        type="submit"
                        onclick="return confirm('ยืนยันการเปลี่ยนรหัสผ่านของผู้ใช้งานนี้?')"
                        class="flex-1 px-4 py-2.5 bg-red-600 hover:bg-red-700 text-white rounded-xl text-sm font-medium shadow transition">
                        🔑 เปลี่ยนรหัสผ่าน
                    </button>

                </div>

            </form>

        </div>


        {% elif page == 'admin_change_password' %}

        <div class="max-w-xl mx-auto">
            <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
                <h2 class="text-2xl font-bold text-purple-600 mb-2">
                    🔐 เปลี่ยนรหัสผ่าน Admin
                </h2>
                <p class="text-sm text-gray-500 mb-6">
                    เปลี่ยนรหัสผ่านของบัญชี admin โดยต้องยืนยันรหัสผ่านเดิมก่อน
                </p>

                <form method="POST" class="space-y-4">
                    <div>
                        <label class="block text-sm font-semibold text-gray-700 mb-1">รหัสผ่านเดิม</label>
                        <input type="password" name="current_password" required autocomplete="current-password"
                               class="w-full border border-gray-300 rounded-xl px-4 py-3 focus:ring-2 focus:ring-purple-500 focus:outline-none">
                    </div>

                    <div>
                        <label class="block text-sm font-semibold text-gray-700 mb-1">รหัสผ่านใหม่</label>
                        <input type="password" name="new_password" required minlength="4" autocomplete="new-password"
                               class="w-full border border-gray-300 rounded-xl px-4 py-3 focus:ring-2 focus:ring-purple-500 focus:outline-none">
                    </div>

                    <div>
                        <label class="block text-sm font-semibold text-gray-700 mb-1">ยืนยันรหัสผ่านใหม่</label>
                        <input type="password" name="confirm_password" required minlength="4" autocomplete="new-password"
                               class="w-full border border-gray-300 rounded-xl px-4 py-3 focus:ring-2 focus:ring-purple-500 focus:outline-none">
                    </div>

                    <div class="flex gap-2 pt-2">
                        <button type="submit"
                                class="flex-1 bg-purple-600 hover:bg-purple-700 text-white font-semibold px-4 py-3 rounded-xl transition">
                            🔐 บันทึกรหัสใหม่
                        </button>
                        <a href="{{ url_for('home') }}"
                           class="px-5 py-3 bg-gray-100 hover:bg-gray-200 text-gray-700 rounded-xl transition">
                            ยกเลิก
                        </a>
                    </div>
                </form>
            </div>
        </div>

                {% elif page == 'manage_users' %}

                <div class="mb-6 p-5 bg-white rounded-2xl shadow-sm border border-gray-100">
            <div class="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
                <div>
                    <h3 class="font-bold text-gray-800">📷 ระบบรูปถ่ายลงงาน</h3>
                    <p class="text-sm text-gray-500 mt-1">Admin สามารถเปิดหรือปิดการถ่าย/อัปโหลดรูปของ User ได้ทั้งระบบ</p>
                </div>
                {% if photo_system_is_on %}
                <a href="{{ url_for('admin_photo_system', action='off') }}" class="px-4 py-2 bg-red-500 hover:bg-red-600 text-white rounded-lg text-sm font-medium text-center">🔴 ปิดระบบถ่ายรูป</a>
                {% else %}
                <a href="{{ url_for('admin_photo_system', action='on') }}" class="px-4 py-2 bg-emerald-500 hover:bg-emerald-600 text-white rounded-lg text-sm font-medium text-center">🟢 เปิดระบบถ่ายรูป</a>
                {% endif %}
            </div>
            <div class="mt-3 text-sm">สถานะ: <b class="{{ 'text-emerald-600' if photo_system_is_on else 'text-red-600' }}">{{ 'เปิดใช้งาน' if photo_system_is_on else 'ปิดใช้งาน' }}</b></div>
        </div>

<div class="space-y-6">

            <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">

                <h2 class="text-2xl font-bold text-red-600 mb-2">
                    🛡️ จัดการผู้ใช้งานระบบ
                </h2>

                <p class="text-sm text-gray-500 mb-6">
                    รายชื่อบัญชีผู้ใช้งานทั้งหมด
                </p>


                <div class="overflow-x-auto">

                    <table class="w-full text-left border-collapse">

                        <thead>

                            <tr class="bg-gray-100 text-gray-700 text-xs uppercase font-semibold">

                                <th class="p-3">
                                    ID
                                </th>

                                <th class="p-3">
                                    ชื่อผู้ใช้งาน
                                </th>

                                <th class="p-3">
                                    กลุ่มทีม
                                </th>

                                <th class="p-3 text-right">
                                    📷 สิทธิ์ถ่ายรูป
                                </th>
                                <th class="p-3 text-right">
                                    จัดการ
                                </th>

                            </tr>

                        </thead>


                        <tbody class="divide-y divide-gray-200 text-sm">

                            {% for u in users_list %}

                            <tr>

                                <td class="p-3">
                                    {{ u.id }}
                                </td>

                                <td class="p-3 font-bold">
                                    {{ u.username }}

                                    {% if u.username == 'admin' %}

                                    <span class="text-xs bg-red-100 text-red-700 px-2 py-0.5 rounded-full ml-1">
                                        Admin หลัก
                                    </span>

                                    {% endif %}

                                </td>

                                <td class="p-3">
                                    {{ u.team_group }}
                                </td>


                                <td class="p-3 text-right">

                                    {% if u.username != 'admin' %}

                                    <div class="flex flex-wrap justify-end gap-2">

                                        <a
                                            href="{{ url_for('admin_reset_password', user_id=u.id) }}"
                                            class="px-3 py-1.5 bg-blue-50 hover:bg-blue-100 text-blue-700 rounded-lg text-xs font-medium transition">

                                            🔑 เปลี่ยนรหัส

                                        </a>

                                        <a
                                            href="{{ url_for('delete_user', user_id=u.id) }}"
                                            onclick="return confirm('ยืนยันการลบบัญชีผู้ใช้นี้?')"
                                            class="px-3 py-1.5 bg-red-50 hover:bg-red-100 text-red-600 rounded-lg text-xs font-medium transition">

                                            🗑️ ลบบัญชี

                                        </a>

                                    </div>

                                    {% else %}

                                    <span class="text-xs text-gray-400">
                                        🔒 Admin หลัก
                                    </span>

                                    {% endif %}

                                </td>

                            </tr>

                            {% endfor %}

                        </tbody>

                    </table>

                </div>

            </div>

        </div>

        {% endif %}

    </main>


    <!-- =====================================================
         FOOTER
         ===================================================== -->

    <footer class="bg-white border-t border-gray-200 py-6 mt-12 text-center text-xs text-gray-500">

        <p>
            &copy; 2026 Clean Shop Management System.
            All rights reserved.
        </p>

    </footer>


    <!-- =====================================================
         JAVASCRIPT
         ===================================================== -->

    <script>

        /* =====================================================
           FLASH MESSAGE
           ===================================================== */

        function closeFlash(button) {

            const message = button.closest('.flash-message');

            if (!message) {
                return;
            }

            message.classList.add('hide');

            setTimeout(function () {

                if (message) {
                    message.remove();
                }

            }, 400);

        }


        document.addEventListener(
            'DOMContentLoaded',
            function () {

                const messages =
                    document.querySelectorAll('.flash-message');

                messages.forEach(function (message) {

                    /*
                     * แสดงข้อความ 3 วินาที
                     * แล้วค่อย ๆ หาย
                     */

                    setTimeout(function () {

                        message.classList.add('hide');

                        setTimeout(function () {

                            if (message) {
                                message.remove();
                            }

                        }, 400);

                    }, 3000);

                });

            }
        );


        /* =====================================================
           SEARCH HISTORY
           ===================================================== */

        const STORAGE_KEY =
            'clean_shop_search_history';


        function getHistory() {

            try {

                const data =
                    localStorage.getItem(STORAGE_KEY);

                return data
                    ? JSON.parse(data)
                    : [];

            } catch (e) {

                return [];

            }

        }


        function saveSearchHistory() {

            const input =
                document.getElementById('userSearchInput');

            const val =
                input
                ? input.value.trim()
                : '';

            if (!val) {
                return;
            }


            let history =
                getHistory();

            history =
                history.filter(
                    item => item !== val
                );

            history.unshift(val);


            if (history.length > 10) {

                history =
                    history.slice(0, 10);

            }


            try {

                localStorage.setItem(
                    STORAGE_KEY,
                    JSON.stringify(history)
                );

            } catch (err) {}

        }


        function removeHistoryItem(term, event) {

            if (event) {
                event.stopPropagation();
            }


            let history =
                getHistory();

            history =
                history.filter(
                    item => item !== term
                );


            try {

                localStorage.setItem(
                    STORAGE_KEY,
                    JSON.stringify(history)
                );

            } catch (err) {}


            showHistory();
            filterHistory();

        }


        function clearHistory(event) {

            if (event) {
                event.stopPropagation();
            }


            try {

                localStorage.removeItem(
                    STORAGE_KEY
                );

            } catch (err) {}


            showHistory();

        }


        function selectHistoryItem(term) {

            const input =
                document.getElementById(
                    'userSearchInput'
                );

            if (input) {

                input.value = term;

                hideHistory();

                if (input.form) {

                    input.form.submit();

                }

            }

        }


        function showHistory() {

            const history =
                getHistory();

            const dropdown =
                document.getElementById(
                    'searchHistoryDropdown'
                );

            const quickPanel =
                document.getElementById(
                    'quickHistoryPanel'
                );

            const quickChips =
                document.getElementById(
                    'quickChipsContainer'
                );


            if (!dropdown) {
                return;
            }


            if (quickPanel && quickChips) {

                if (history.length > 0) {

                    quickPanel.style.display =
                        'block';


                    quickChips.innerHTML =
                        history.map(
                            item => `

                                <a href="#"
                                   class="history-chip"
                                   onclick="selectHistoryItem('${item}'); return false;">

                                    👤 ${item}

                                </a>

                            `
                        ).join('');

                } else {

                    quickPanel.style.display =
                        'none';

                }

            }


            if (history.length > 0) {

                let html = `

                    <div class="history-header">

                        <span>
                            ประวัติการค้นหาล่าสุด
                        </span>

                        <span
                            style="cursor:pointer; color:#991b1b;"
                            onclick="clearHistory(event)">

                            ล้างทั้งหมด

                        </span>

                    </div>

                `;


                html +=
                    history.map(
                        item => `

                            <div
                                class="history-item"
                                onclick="selectHistoryItem('${item}')">

                                <span>
                                    🕒 ${item}
                                </span>

                                <span
                                    class="history-item-delete"
                                    onclick="removeHistoryItem('${item}', event)">

                                    ลบ

                                </span>

                            </div>

                        `
                    ).join('');


                dropdown.innerHTML =
                    html;

                dropdown.style.display =
                    'block';

            } else {

                dropdown.style.display =
                    'none';

            }

        }


        function hideHistory() {

            const dropdown =
                document.getElementById(
                    'searchHistoryDropdown'
                );


            if (dropdown) {

                setTimeout(
                    function () {

                        dropdown.style.display =
                            'none';

                    },
                    200
                );

            }

        }


        function filterHistory() {

            const input =
                document.getElementById(
                    'userSearchInput'
                );

            const filter =
                input
                ? input.value.toLowerCase()
                : '';


            const history =
                getHistory();

            const dropdown =
                document.getElementById(
                    'searchHistoryDropdown'
                );


            if (!dropdown) {
                return;
            }


            const filtered =
                history.filter(
                    item =>
                        item
                        .toLowerCase()
                        .includes(filter)
                );


            if (filtered.length > 0) {

                let html = `

                    <div class="history-header">

                        <span>
                            ประวัติการค้นหา
                        </span>

                        <span
                            style="cursor:pointer; color:#991b1b;"
                            onclick="clearHistory(event)">

                            ล้างทั้งหมด

                        </span>

                    </div>

                `;


                html +=
                    filtered.map(
                        item => `

                            <div
                                class="history-item"
                                onclick="selectHistoryItem('${item}')">

                                <span>
                                    🕒 ${item}
                                </span>

                                <span
                                    class="history-item-delete"
                                    onclick="removeHistoryItem('${item}', event)">

                                    ลบ

                                </span>

                            </div>

                        `
                    ).join('');


                dropdown.innerHTML =
                    html;

                dropdown.style.display =
                    'block';

            } else {

                dropdown.style.display =
                    'none';

            }

        }


        document.addEventListener(
            'click',
            function (e) {

                const container =
                    document.querySelector(
                        '.search-container'
                    );

                const dropdown =
                    document.getElementById(
                        'searchHistoryDropdown'
                    );


                if (
                    container &&
                    !container.contains(e.target)
                ) {

                    if (dropdown) {

                        dropdown.style.display =
                            'none';

                    }

                }

            }
        );


        document.addEventListener(
            'DOMContentLoaded',
            function () {

                const history =
                    getHistory();

                const quickPanel =
                    document.getElementById(
                        'quickHistoryPanel'
                    );

                const quickChips =
                    document.getElementById(
                        'quickChipsContainer'
                    );


                if (
                    quickPanel &&
                    quickChips &&
                    history.length > 0
                ) {

                    quickPanel.style.display =
                        'block';


                    quickChips.innerHTML =
                        history.map(
                            item => `

                                <a href="#"
                                   class="history-chip"
                                   onclick="selectHistoryItem('${item}'); return false;">

                                    👤 ${item}

                                </a>

                            `
                        ).join('');

                }

            }
        );

    </script>

<script>
function setCheckinPhoto(input) {
    const file = input.files && input.files[0];
    if (!file) return;
    const label = input.closest('form').querySelector('#checkin-photo-name');
    if (label) label.textContent = 'ถ่ายรูปแล้ว: ' + file.name;
}
</script>
</body>

</html>
"""


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    if not session.get('user_id'):
        return redirect(url_for('login'))

    return render_template_string(
        HTML_TEMPLATE,
        page='home'
    )


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=['GET', 'POST'])
def login():

    if request.method == 'POST':

        username = request.form.get(
            'username',
            ''
        ).strip()

        password = request.form.get(
            'password',
            ''
        )


        db = sqlite3.connect(
            "clean_shop.db"
        )

        db.row_factory = sqlite3.Row


        user = db.execute(
            """
            SELECT *
            FROM users
            WHERE username = ?
            """,
            (username,)
        ).fetchone()


        db.close()


        if user and check_password_hash(
            user['password'],
            password
        ):

            session['user_id'] = user['id']

            session['username'] = user['username']

            session['team_group'] = user['team_group']


            flash(
                "เข้าสู่ระบบเรียบร้อย",
                "success"
            )


            return redirect(
                url_for('home')
            )


        else:

            flash(
                "ชื่อผู้ใช้งานหรือรหัสผ่านไม่ถูกต้อง",
                "error"
            )


    return render_template_string(
        HTML_TEMPLATE,
        page='login'
    )


# =========================================================
# REGISTER
# =========================================================

@app.route("/register", methods=['GET', 'POST'])
def register():

    if request.method == 'POST':

        username = request.form.get(
            'username',
            ''
        ).strip()

        password = request.form.get(
            'password',
            ''
        )

        team_group = request.form.get(
            'team_group',
            'ทีมปฏิบัติการ'
        ).strip()


        if len(username) < 3 or len(password) < 4:

            flash(
                "กรุณากรอกข้อมูลให้ครบถ้วน",
                "error"
            )

            return redirect(
                url_for('register')
            )


        try:

            db = sqlite3.connect(
                "clean_shop.db"
            )


            hashed_pw = generate_password_hash(
                    password
                )


            db.execute(
                """
                INSERT INTO users
                (username, password, team_group)
                VALUES (?, ?, ?)
                """,
                (
                    username,
                    hashed_pw,
                    team_group
                )
            )


            db.commit()
            db.close()


            flash(
                "สมัครสมาชิกสำเร็จ! กรุณาเข้าสู่ระบบ",
                "success"
            )


            return redirect(
                url_for('login')
            )


        except sqlite3.IntegrityError:

            flash(
                "ชื่อผู้ใช้งานนี้มีอยู่ในระบบแล้ว กรุณาใช้ชื่ออื่น",
                "error"
            )


        except Exception as e:

            flash(
                f"เกิดข้อผิดพลาด: {e}",
                "error"
            )


    return render_template_string(
        HTML_TEMPLATE,
        page='register'
    )


# =========================================================
# LOGOUT
# =========================================================

@app.route("/logout")
def logout():

    session.clear()

    flash(
        "ออกจากระบบเรียบร้อยแล้ว",
        "success"
    )

    return redirect(
        url_for('login')
    )


# =========================================================
# ADD SHOP
# =========================================================

@app.route("/add_shop", methods=['GET', 'POST'])
def add_shop():

    if not session.get('user_id'):
        return redirect(url_for('login'))


    if request.method == 'POST':

        name = request.form.get(
            'name',
            ''
        ).strip()

        district = request.form.get(
            'district',
            ''
        ).strip()

        address = request.form.get(
            'address',
            ''
        ).strip()

        phone = request.form.get(
            'phone',
            ''
        ).strip()

        priority_order = request.form.get(
            'priority_order',
            1
        )

        shop_condition = request.form.get(
            'shop_condition',
            'เปิดปกติ'
        )

        created_by = session.get('username')

        try:

            priority_order = int(priority_order)

        except ValueError:

            priority_order = 1


        db = sqlite3.connect(
            "clean_shop.db"
        )


        db.execute(
            """
            INSERT INTO shops
            (
                name,
                district,
                address,
                phone,
                created_by,
                original_owner,
                shop_condition,
                priority_order,
                status,
                photo_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                district,
                address,
                phone,
                created_by,
                created_by,
                shop_condition,
                priority_order,
                'ยังไม่เช็คอิน',
                photo_filename
            )
        )


        db.commit()
        db.close()


        flash(
            "เพิ่มข้อมูลร้านค้าสำเร็จ",
            "success"
        )


        return redirect(
            url_for('shops_list')
        )


    return render_template_string(
        HTML_TEMPLATE,
        page='add_shop'
    )


# =========================================================
# EDIT SHOP
# =========================================================

@app.route("/edit_shop/<int:shop_id>", methods=['GET', 'POST'])
def edit_shop(shop_id):

    if not session.get('user_id'):
        return redirect(url_for('login'))


    db = sqlite3.connect(
        "clean_shop.db"
    )

    db.row_factory = sqlite3.Row


    shop = db.execute(
        """
        SELECT *
        FROM shops
        WHERE id = ?
        """,
        (shop_id,)
    ).fetchone()


    if not shop:

        db.close()

        flash(
            "ไม่พบข้อมูลร้านค้านี้",
            "error"
        )

        return redirect(
            url_for('shops_list')
        )


    if (
        shop['created_by'] != session.get('username')
        and session.get('username') != 'admin'
    ):

        db.close()

        flash(
            "คุณไม่มีสิทธิ์แก้ไขร้านค้านี้",
            "error"
        )

        return redirect(
            url_for('shops_list')
        )


    if request.method == 'POST':

        name = request.form.get(
            'name',
            ''
        ).strip()

        district = request.form.get(
            'district',
            ''
        ).strip()

        address = request.form.get(
            'address',
            ''
        ).strip()

        phone = request.form.get(
            'phone',
            ''
        ).strip()

        priority_order = request.form.get(
            'priority_order',
            1
        )

        shop_condition = request.form.get(
            'shop_condition',
            'เปิดปกติ'
        )


        try:

            priority_order = int(priority_order)

        except ValueError:

            priority_order = 1


        db.execute(
            """
            UPDATE shops
            SET
                name = ?,
                district = ?,
                address = ?,
                phone = ?,
                priority_order = ?,
                shop_condition = ?,
                photo_path = COALESCE(?, photo_path)
            WHERE id = ?
            """,
            (
                name,
                district,
                address,
                phone,
                priority_order,
                shop_condition,
                None,
                shop_id
            )
        )


        db.commit()
        db.close()


        flash(
            "แก้ไขข้อมูลร้านค้าสำเร็จ",
            "success"
        )


        return redirect(
            url_for('shops_list')
        )


    db.close()


    return render_template_string(
        HTML_TEMPLATE,
        page='edit_shop',
        shop=shop
    )


# =========================================================
# DELETE SHOP
# =========================================================

@app.route("/delete_shop/<int:shop_id>")
def delete_shop(shop_id):

    if not session.get('user_id'):
        return redirect(url_for('login'))


    db = sqlite3.connect(
        "clean_shop.db"
    )

    db.row_factory = sqlite3.Row


    shop = db.execute(
        """
        SELECT *
        FROM shops
        WHERE id = ?
        """,
        (shop_id,)
    ).fetchone()


    if shop and (
        shop['created_by'] == session.get('username')
        or session.get('username') == 'admin'
    ):

        db.execute(
            "DELETE FROM shops WHERE id = ?",
            (shop_id,)
        )

        db.commit()


        flash(
            "ลบร้านค้าเรียบร้อยแล้ว",
            "success"
        )


    else:

        flash(
            "คุณไม่มีสิทธิ์ลบร้านค้านี้",
            "error"
        )


    db.close()


    return redirect(
        url_for('shops_list')
    )


# =========================================================
# TRANSFER SHOP
# =========================================================

@app.route("/transfer_shop/<int:shop_id>", methods=['GET', 'POST'])
def transfer_shop(shop_id):

    if not session.get('user_id'):
        return redirect(url_for('login'))


    db = sqlite3.connect(
        "clean_shop.db"
    )

    db.row_factory = sqlite3.Row


    shop = db.execute(
        """
        SELECT *
        FROM shops
        WHERE id = ?
        """,
        (shop_id,)
    ).fetchone()


    if not shop:

        db.close()

        flash(
            "ไม่พบข้อมูลร้านค้านี้",
            "error"
        )

        return redirect(
            url_for('shops_list')
        )


    if request.method == 'POST':

        target_user = request.form.get(
            'target_user',
            ''
        ).strip()


        target = db.execute(
            """
            SELECT *
            FROM users
            WHERE username = ?
            """,
            (target_user,)
        ).fetchone()


        if not target:

            db.close()

            flash(
                f"ไม่พบชื่อผู้ใช้งาน '{target_user}' ในระบบ",
                "error"
            )

            return redirect(
                url_for(
                    'transfer_shop',
                    shop_id=shop_id
                )
            )


        if target_user == session.get('username'):

            db.close()

            flash(
                "ไม่สามารถโอนย้ายให้ตัวเองได้",
                "error"
            )

            return redirect(
                url_for(
                    'transfer_shop',
                    shop_id=shop_id
                )
            )


        orig_owner = (
            shop['original_owner']
            if shop['original_owner']
            else shop['created_by']
        )


        from_user = session.get('username')


        current_time = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )


        db.execute(
            """
            UPDATE shops
            SET
                created_by = ?,
                original_owner = ?
            WHERE id = ?
            """,
            (
                target_user,
                orig_owner,
                shop_id
            )
        )


        db.execute(
            """
            INSERT INTO shop_transfers
            (
                shop_id,
                shop_name,
                from_user,
                to_user,
                transferred_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                shop_id,
                shop['name'],
                from_user,
                target_user,
                current_time
            )
        )


        db.commit()
        db.close()


        flash(
            f"📦 โอนร้านส่ง '{shop['name']}' ไปยังผู้ใช้ '{target_user}' สำเร็จ",
            "success"
        )


        return redirect(
            url_for('shops_list')
        )


    db.close()


    return render_template_string(
        HTML_TEMPLATE,
        page='transfer_shop',
        shop=shop
    )


# =========================================================
# TRANSFER HISTORY
# =========================================================

@app.route("/transfer_history")
def transfer_history():

    if not session.get('user_id'):
        return redirect(url_for('login'))


    db = sqlite3.connect(
        "clean_shop.db"
    )

    db.row_factory = sqlite3.Row


    transfers = db.execute(
        """
        SELECT *
        FROM shop_transfers
        ORDER BY id DESC
        """
    ).fetchall()


    db.close()


    return render_template_string(
        HTML_TEMPLATE,
        page='transfer_history',
        transfers=transfers
    )


# =========================================================
# TOGGLE CONDITION
# =========================================================

@app.route("/toggle_condition/<int:shop_id>")
def toggle_condition(shop_id):

    if not session.get('user_id'):
        return redirect(url_for('login'))


    db = sqlite3.connect(
        "clean_shop.db"
    )

    db.row_factory = sqlite3.Row


    shop = db.execute(
        """
        SELECT *
        FROM shops
        WHERE id = ?
        """,
        (shop_id,)
    ).fetchone()


    if shop:

        new_cond = (
            'ปิดกิจการ'
            if shop['shop_condition'] == 'เปิดปกติ'
            else 'เปิดปกติ'
        )


        db.execute(
            """
            UPDATE shops
            SET shop_condition = ?
            WHERE id = ?
            """,
            (
                new_cond,
                shop_id
            )
        )


        db.commit()


        flash(
            f"เปลี่ยนสถานะร้านเป็น '{new_cond}' แล้ว",
            "success"
        )


    db.close()


    return redirect(
        url_for('shops_list')
    )


# =========================================================
# CHECK IN
# =========================================================

@app.route("/checkin_shop/<int:shop_id>", methods=['POST'])
def checkin_shop(shop_id):

    if not session.get('user_id'):
        return redirect(url_for('login'))

    db = sqlite3.connect("clean_shop.db")
    db.row_factory = sqlite3.Row

    shop = db.execute(
        """
        SELECT *
        FROM shops
        WHERE id = ?
        """,
        (shop_id,)
    ).fetchone()

    if not shop:
        db.close()
        flash("ไม่พบร้านค้านี้", "error")
        return redirect(url_for('shops_list'))

    username = session.get('username')
    photo = request.files.get("checkin_photo") or request.files.get("checkin_camera_photo")

    # ถ้าบัญชีได้รับสิทธิ์ถ่ายรูป ต้องมีรูปยืนยันตอนเช็คอิน
    if photo_allowed_for_current_user():
        if not photo or not photo.filename:
            db.close()
            flash("บัญชีนี้ได้รับสิทธิ์ถ่ายรูปแล้ว กรุณาถ่ายรูปยืนยันก่อนเช็คอิน", "error")
            return redirect(url_for('shops_list'))

        unique_name = save_resized_photo(
            photo,
            f"checkin_{session.get('user_id')}_{shop_id}"
        )
        if not unique_name:
            db.close()
            flash("ไฟล์นี้ไม่ใช่รูปที่ระบบรองรับ หรือรูปเสียหาย กรุณาเลือกรูปใหม่", "error")
            return redirect(url_for('shops_list'))

        # เก็บเฉพาะชื่อไฟล์ ไม่เก็บ path จากผู้ใช้
        db.execute(
            """
            UPDATE shops
            SET
                photo_path = ?
            WHERE id = ?
            """,
            (unique_name, shop_id)
        )

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db.execute(
        """
        UPDATE shops
        SET
            status = 'ถึงร้านแล้ว',
            checked_at = ?,
            checked_by_user = ?
        WHERE id = ?
        """,
        (
            current_time,
            username,
            shop_id
        )
    )

    db.commit()
    db.close()

    if photo_allowed_for_current_user():
        flash(f"เช็คอินร้าน '{shop['name']}' สำเร็จ พร้อมรูปยืนยัน", "success")
    else:
        flash(f"เช็คอินร้าน '{shop['name']}' สำเร็จ", "success")

    return redirect(url_for('shops_list'))


# =========================================================
# RESET CHECKINS
# =========================================================

@app.route("/reset_checkins", methods=['GET', 'POST'])
def reset_checkins():

    if not session.get('user_id'):
        return redirect(url_for('login'))


    if request.method == 'POST':

        confirm_text = request.form.get(
                'confirm_text',
                ''
            ).strip()


        if confirm_text == 'RESET':

            username = session.get('username')


            db = sqlite3.connect(
                "clean_shop.db"
            )


            db.execute(
                """
                UPDATE shops
                SET
                    status = 'ยังไม่เช็คอิน',
                    checked_at = NULL,
                    checked_by_user = NULL
                WHERE created_by = ?
                """,
                (username,)
            )


            db.commit()
            db.close()


            flash(
                "รีเซ็ตสถานะเช็คอินทั้งหมดของคุณเรียบร้อยแล้ว",
                "success"
            )


            return redirect(
                url_for('home')
            )


        else:

            flash(
                "พิมพ์คำว่า RESET ไม่ถูกต้อง กรุณาลองใหม่อีกครั้ง",
                "error"
            )


    return render_template_string(
        HTML_TEMPLATE,
        page='reset_checkins'
    )


# =========================================================
# SHOPS
# =========================================================

@app.route("/shops")
def shops_list():

    if not session.get('user_id'):
        return redirect(url_for('login'))


    current_filter = request.args.get(
            'filter',
            'all'
        )

    username = session.get('username')


    db = sqlite3.connect(
        "clean_shop.db"
    )

    db.row_factory = sqlite3.Row


    query = """
        SELECT *
        FROM shops
        WHERE created_by = ?
    """

    params = [
        username
    ]


    if current_filter == 'open':

        query += """
            AND shop_condition = 'เปิดปกติ'
        """


    elif current_filter == 'closed':

        query += """
            AND shop_condition = 'ปิดกิจการ'
        """


    query += """
        ORDER BY priority_order ASC, id DESC
    """


    shops = db.execute(
            query,
            params
        ).fetchall()


    db.close()


    return render_template_string(
        HTML_TEMPLATE,
        page='shops_list',
        shops=shops,
        current_filter=current_filter
    )


# =========================================================
# FRIENDS
# =========================================================

@app.route("/friends", methods=['GET'])
def friends_page():

    if not session.get('user_id'):
        return redirect(url_for('login'))


    user_id = session.get('user_id')


    search_query = request.args.get(
            'search_user',
            ''
        ).strip()


    db = sqlite3.connect(
        "clean_shop.db"
    )

    db.row_factory = sqlite3.Row


    search_results = []


    if search_query:

        search_results = db.execute(
                """
                SELECT *
                FROM users
                WHERE username LIKE ?
                AND id != ?
                AND id NOT IN (
                    SELECT friend_id
                    FROM friends
                    WHERE user_id = ?

                    UNION

                    SELECT user_id
                    FROM friends
                    WHERE friend_id = ?
                )
                """,
                (
                    f"%{search_query}%",
                    user_id,
                    user_id,
                    user_id
                )
            ).fetchall()


    pending_requests = db.execute(
            """
            SELECT
                f.id as rel_id,
                u.id as user_id,
                u.username,
                u.team_group
            FROM friends f
            JOIN users u
                ON f.user_id = u.id
            WHERE f.friend_id = ?
            AND f.status = 'pending'
            """,
            (user_id,)
        ).fetchall()


    my_friends = db.execute(
            """
            SELECT
                f.id as rel_id,
                u.id as user_id,
                u.username,
                u.team_group
            FROM friends f
            JOIN users u
                ON (
                    f.friend_id = u.id
                    OR f.user_id = u.id
                )
            WHERE (
                f.user_id = ?
                OR f.friend_id = ?
            )
            AND u.id != ?
            AND f.status = 'accepted'
            """,
            (
                user_id,
                user_id,
                user_id
            )
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


# =========================================================
# ADD FRIEND
# =========================================================

@app.route("/add_friend/<int:friend_id>")
def add_friend(friend_id):

    if not session.get('user_id'):
        return redirect(url_for('login'))


    user_id = session.get('user_id')


    if user_id == friend_id:

        flash(
            "ไม่สามารถเพิ่มตัวเองเป็นเพื่อนได้",
            "error"
        )

        return redirect(
            url_for('friends_page')
        )


    db = sqlite3.connect(
        "clean_shop.db"
    )


    existing = db.execute(
            """
            SELECT *
            FROM friends
            WHERE
                (
                    user_id = ?
                    AND friend_id = ?
                )
                OR
                (
                    user_id = ?
                    AND friend_id = ?
                )
            """,
            (
                user_id,
                friend_id,
                friend_id,
                user_id
            )
        ).fetchone()


    if not existing:

        db.execute(
            """
            INSERT INTO friends
            (user_id, friend_id, status)
            VALUES (?, ?, 'pending')
            """,
            (
                user_id,
                friend_id
            )
        )


        db.commit()


        flash(
            "ส่งคำขอเป็นเพื่อนเรียบร้อยแล้ว",
            "success"
        )


    else:

        flash(
            "มีสถานะความเป็นเพื่อนหรือคำขออยู่ในระบบแล้ว",
            "error"
        )


    db.close()


    return redirect(
        url_for('friends_page')
    )


# =========================================================
# ACCEPT FRIEND
# =========================================================

@app.route("/accept_friend/<int:req_id>")
def accept_friend(req_id):

    if not session.get('user_id'):
        return redirect(url_for('login'))


    db = sqlite3.connect(
        "clean_shop.db"
    )


    db.execute(
        """
        UPDATE friends
        SET status = 'accepted'
        WHERE id = ?
        AND friend_id = ?
        """,
        (
            req_id,
            session.get('user_id')
        )
    )


    db.commit()
    db.close()


    flash(
        "ยอมรับคำขอเป็นเพื่อนแล้ว",
        "success"
    )


    return redirect(
        url_for('friends_page')
    )


# =========================================================
# REMOVE FRIEND
# =========================================================

@app.route("/remove_friend/<int:rel_id>")
def remove_friend(rel_id):

    if not session.get('user_id'):
        return redirect(url_for('login'))


    user_id = session.get('user_id')


    db = sqlite3.connect(
        "clean_shop.db"
    )


    db.execute(
        """
        DELETE FROM friends
        WHERE id = ?
        AND (
            user_id = ?
            OR friend_id = ?
        )
        """,
        (
            rel_id,
            user_id,
            user_id
        )
    )


    db.commit()
    db.close()


    flash(
        "ยกเลิกหรือลบเพื่อนเรียบร้อยแล้ว",
        "success"
    )


    return redirect(
        url_for('friends_page')
    )


# =========================================================
# OTHER USERS SHOPS
# =========================================================

@app.route("/other_users_shops")
def other_users_shops():

    if not session.get('user_id'):
        return redirect(url_for('login'))


    selected_user = request.args.get(
            'user',
            ''
        ).strip()


    shops = []


    if selected_user:

        db = sqlite3.connect(
            "clean_shop.db"
        )

        db.row_factory = sqlite3.Row


        shops = db.execute(
                """
                SELECT *
                FROM shops
                WHERE created_by = ?
                ORDER BY priority_order ASC, id DESC
                """,
                (selected_user,)
            ).fetchall()


        db.close()


    return render_template_string(
        HTML_TEMPLATE,
        page='other_users_shops',
        selected_user=selected_user,
        shops=shops
    )



# =========================================================
# PHOTO FILES
# =========================================================

@app.route("/uploads/shop_photos/<path:filename>")
def uploaded_photo(filename):
    from flask import send_from_directory
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# =========================================================
# ADMIN: PHOTO PERMISSION
# =========================================================

@app.route("/admin/photo_permission/<int:user_id>/<action>")
def admin_photo_permission(user_id, action):

    if not session.get("user_id") or not is_admin():
        flash("ไม่มีสิทธิ์เข้าถึงส่วนนี้", "error")
        return redirect(url_for("home"))

    if action not in ("allow", "deny"):
        flash("คำสั่งไม่ถูกต้อง", "error")
        return redirect(url_for("manage_users"))

    db = sqlite3.connect("clean_shop.db")

    user = db.execute(
        "SELECT username FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not user:
        db.close()
        flash("ไม่พบผู้ใช้งาน", "error")
        return redirect(url_for("manage_users"))

    allowed = 1 if action == "allow" else 0

    db.execute(
        """
        INSERT INTO photo_permissions
        (user_id, allowed, approved_by, approved_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            allowed = excluded.allowed,
            approved_by = excluded.approved_by,
            approved_at = excluded.approved_at
        """,
        (
            user_id,
            allowed,
            session.get("username"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    )

    db.commit()
    db.close()

    if allowed:
        flash(f"อนุญาตให้ {user[0]} ใช้ระบบถ่ายรูปแล้ว", "success")
    else:
        flash(f"ปิดสิทธิ์ถ่ายรูปของ {user[0]} แล้ว", "success")

    return redirect(url_for("manage_users"))


# =========================================================
# ADMIN PHOTO SYSTEM TOGGLE
# =========================================================

@app.route("/admin/photo_system/<action>")
def admin_photo_system(action):
    if not session.get("user_id") or not is_admin():
        flash("เฉพาะ Admin เท่านั้นที่สามารถตั้งค่าระบบถ่ายรูปได้", "error")
        return redirect(url_for("home"))

    if action not in ("on", "off"):
        flash("คำสั่งไม่ถูกต้อง", "error")
        return redirect(url_for("manage_users"))

    enabled = "1" if action == "on" else "0"
    db = sqlite3.connect("clean_shop.db")
    db.execute(
        "INSERT INTO app_settings (setting_key, setting_value) VALUES (?, ?) "
        "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value",
        ("photo_system_enabled", enabled)
    )
    db.commit()
    db.close()

    flash("เปิดระบบถ่ายรูปแล้ว" if enabled == "1" else "ปิดระบบถ่ายรูปแล้ว", "success")
    return redirect(url_for("manage_users"))


# =========================================================
# MANAGE USERS
# =========================================================

@app.route("/manage_users")
def manage_users():

    if not session.get('user_id'):
        return redirect(url_for('login'))

    if not is_admin():
        flash("เฉพาะ Admin เท่านั้นที่สามารถจัดการผู้ใช้งานได้", "error")
        return redirect(url_for('home'))


    db = sqlite3.connect(
        "clean_shop.db"
    )

    db.row_factory = sqlite3.Row


    users_list = db.execute(
            """
            SELECT *
            FROM users
            ORDER BY id ASC
            """
        ).fetchall()
    photo_setting = db.execute(
        "SELECT setting_value FROM app_settings WHERE setting_key = ?",
        ("photo_system_enabled",)
    ).fetchone()
    photo_system_is_on = bool(photo_setting and photo_setting[0] == "1")


    db.close()


    return render_template_string(
        HTML_TEMPLATE,
        page='manage_users',
        users_list=users_list,
        photo_system_is_on=photo_system_is_on
    )


# =========================================================
# ADMIN CHANGE OWN PASSWORD
# =========================================================

@app.route("/admin_change_password", methods=["GET", "POST"])
def admin_change_password():
    if not session.get("user_id") or not is_admin():
        flash("เฉพาะ Admin เท่านั้นที่สามารถเปลี่ยนรหัส Admin ได้", "error")
        return redirect(url_for("login"))

    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        db = sqlite3.connect("clean_shop.db")
        db.row_factory = sqlite3.Row
        admin = db.execute(
            "SELECT * FROM users WHERE username = ?", ("admin",)
        ).fetchone()

        if not admin or not check_password_hash(admin["password"], current_password):
            db.close()
            flash("รหัสผ่านเดิมไม่ถูกต้อง", "error")
            return redirect(url_for("admin_change_password"))

        if len(new_password) < 4:
            db.close()
            flash("รหัสผ่านใหม่ต้องมีอย่างน้อย 4 ตัวอักษร", "error")
            return redirect(url_for("admin_change_password"))

        if new_password != confirm_password:
            db.close()
            flash("รหัสผ่านใหม่และการยืนยันรหัสผ่านไม่ตรงกัน", "error")
            return redirect(url_for("admin_change_password"))

        new_hash = generate_password_hash(new_password)
        db.execute(
            "UPDATE users SET password = ? WHERE username = ?",
            (new_hash, "admin")
        )
        db.commit()
        db.close()

        flash("เปลี่ยนรหัสผ่าน Admin เรียบร้อยแล้ว", "success")
        return redirect(url_for("home"))

    return render_template_string(HTML_TEMPLATE, page="admin_change_password")


# =========================================================
# ADMIN RESET USER PASSWORD
# =========================================================

@app.route("/admin_reset_password/<int:user_id>", methods=["GET", "POST"])
def admin_reset_password(user_id):
    # เฉพาะ admin เท่านั้น
    if not session.get("user_id") or not is_admin():
        flash(
            "เฉพาะ Admin เท่านั้นที่สามารถเปลี่ยนรหัสผ่านผู้ใช้งานได้",
            "error"
        )
        return redirect(url_for("home"))

    db = sqlite3.connect("clean_shop.db")
    db.row_factory = sqlite3.Row

    target = db.execute(
        """
        SELECT *
        FROM users
        WHERE id = ?
        """,
        (user_id,)
    ).fetchone()

    if not target:
        db.close()
        flash("ไม่พบผู้ใช้งานที่ต้องการเปลี่ยนรหัสผ่าน", "error")
        return redirect(url_for("manage_users"))

    # ป้องกันการเปลี่ยนรหัสผ่าน admin หลักผ่านหน้านี้
    if target["username"] == "admin":
        db.close()
        flash("ไม่สามารถเปลี่ยนรหัสผ่าน admin หลักจากเมนูนี้ได้", "error")
        return redirect(url_for("manage_users"))

    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if len(new_password) < 4:
            flash("รหัสผ่านใหม่ต้องมีอย่างน้อย 4 ตัวอักษร", "error")
            db.close()
            return redirect(url_for("admin_reset_password", user_id=user_id))

        if new_password != confirm_password:
            flash("รหัสผ่านใหม่และการยืนยันรหัสผ่านไม่ตรงกัน", "error")
            db.close()
            return redirect(url_for("admin_reset_password", user_id=user_id))

        hashed_pw = generate_password_hash(new_password)

        db.execute(
            """
            UPDATE users
            SET password = ?
            WHERE id = ?
            """,
            (hashed_pw, user_id)
        )
        db.commit()
        db.close()

        flash(
            f"เปลี่ยนรหัสผ่านของ '{target['username']}' เรียบร้อยแล้ว",
            "success"
        )
        return redirect(url_for("manage_users"))

    db.close()

    return render_template_string(
        HTML_TEMPLATE,
        page="admin_reset_password",
        target_user=target
    )


# =========================================================
# DELETE USER
# =========================================================

@app.route("/delete_user/<int:user_id>")
def delete_user(user_id):

    if not session.get('user_id') or not is_admin():

        flash(
            "เฉพาะ Admin เท่านั้นที่สามารถลบบัญชีผู้ใช้งานได้",
            "error"
        )

        return redirect(
            url_for('home')
        )


    db = sqlite3.connect(
        "clean_shop.db"
    )

    db.row_factory = sqlite3.Row


    target = db.execute(
            """
            SELECT *
            FROM users
            WHERE id = ?
            """,
            (user_id,)
        ).fetchone()


    if target:

        if target['username'] == 'admin':

            flash(
                "ไม่สามารถลบบัญชีผู้ดูแลระบบ (admin) หลักได้",
                "error"
            )

        else:

            db.execute(
                "DELETE FROM users WHERE id = ?",
                (user_id,)
            )

            db.execute(
                """
                DELETE FROM friends
                WHERE user_id = ?
                OR friend_id = ?
                """,
                (
                    user_id,
                    user_id
                )
            )


            db.commit()


            flash(
                f"ลบผู้ใช้งาน '{target['username']}' สำเร็จ",
                "success"
            )


    db.close()


    return redirect(
        url_for('manage_users')
    )


# =========================================================
# RUN SERVER
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
