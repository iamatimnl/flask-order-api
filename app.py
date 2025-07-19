from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
)
from flask_socketio import SocketIO
from sqlalchemy import text
import eventlet
eventlet.monkey_patch()
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os
import json
import random
import string
from flask_migrate import Migrate
from urllib.parse import quote
import uuid
from flask import send_file
from werkzeug.utils import secure_filename
from io import BytesIO
import pandas as pd
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Table, TableStyle
from reportlab.lib import colors



from sqlalchemy import func

import traceback
from flask import Flask, send_from_directory, render_template

app = Flask(__name__, static_folder='static', template_folder='templates')


# 初始化 Flask
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = "replace-this"
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["UPLOAD_FOLDER"] = os.path.join(app.static_folder, "uploads")
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
print(repr(os.getenv("DATABASE_URL")))


db = SQLAlchemy(app)
migrate = Migrate(app, db)
with app.app_context():
    db.create_all()
    try:
        inspector = db.inspect(db.engine)
        cols = {c["name"] for c in inspector.get_columns("orders")}
        if "opmerking" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE orders ADD COLUMN opmerking TEXT"))
        if "is_completed" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE orders ADD COLUMN is_completed BOOLEAN DEFAULT FALSE"))
        if "is_cancelled" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE orders ADD COLUMN is_cancelled BOOLEAN DEFAULT FALSE"))
        if "tijdslot_display" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE orders ADD COLUMN tijdslot_display TEXT"))
        cols = {c["name"] for c in inspector.get_columns("reviews")}
        if "rating" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE reviews ADD COLUMN rating INTEGER"))
        if "reply" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE reviews ADD COLUMN reply TEXT"))
        idx_names = [i["name"] for i in inspector.get_indexes("orders")]
        if "idx_orders_created_at" not in idx_names:
            with db.engine.begin() as conn:
                conn.execute(text("CREATE INDEX idx_orders_created_at ON orders (created_at)"))
    except Exception as e:
        print(f"DB init error: {e}")

UTC = timezone.utc
NL_TZ = ZoneInfo("Europe/Amsterdam")
# 默认首页 index.html
@app.route("/")
def serve_index():
    return send_from_directory(".", "index.html")

# 英文版首页 indexEN.html
@app.route("/indexEN.html")
def serve_index_en():
    return send_from_directory(".", "indexEN.html")
def to_nl(dt: datetime) -> datetime:
    """Convert naive UTC datetime to Europe/Amsterdam timezone."""
    if dt is None:
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(NL_TZ)
def generate_excel_today(include_cancelled: bool = False):
    today = datetime.now(NL_TZ).date()
    start_local = datetime.combine(today, datetime.min.time(), tzinfo=NL_TZ)
    start = start_local.astimezone(UTC).replace(tzinfo=None)

    q = Order.query.filter(Order.created_at >= start)
    if not include_cancelled:
        q = q.filter(Order.is_cancelled == False)
    orders = q.order_by(Order.created_at.desc()).all()
    data = []
    for o in orders:
        try:
            items = json.loads(o.items or "{}")
        except Exception:
            items = {}

        summary = ", ".join(f"{k} x {v.get('qty')}" for k, v in items.items())
        status = "Geannuleerd" if o.is_cancelled else ("Voltooid" if o.is_completed else "Open")
        data.append({
            "Datum": to_nl(o.created_at).strftime("%Y-%m-%d"),
            "Tijd": to_nl(o.created_at).strftime("%H:%M"),
            "Naam": o.customer_name,
            "Telefoon": o.phone,
            "Email": o.email,
            "Adres": f"{o.street} {o.house_number}, {o.postcode} {o.city}",
            "Betaalwijze": o.payment_method,
            "Totaal": f"€{o.totaal:.2f}",
            "Items": summary,
            "Status": status,
        })

    df = pd.DataFrame(data)
    output = BytesIO()
    df.to_excel(output, index=False, engine='xlsxwriter')
    output.seek(0)
    return output

@app.route('/api/orders/<order_number>', methods=['GET'])
def get_order_by_number(order_number):
    order = Order.query.filter_by(order_number=order_number).first()
    if order:
        return jsonify(order_to_dict(order)), 200
    else:
        return jsonify({"error": "Order not found"}), 404

def order_to_dict(order):
    try:
        items = json.loads(order.items or '{}')
    except Exception:
        try:
            import ast
            items = ast.literal_eval(order.items)
        except Exception:
            items = {}

    return {
        "order_number": order.order_number,
        "customer_name": order.customer_name,
        "phone": order.phone,
        "email": order.email,
        "payment_method": order.payment_method,
        "totaal": order.totaal,
        "items": items,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "is_completed": order.is_completed,
        "is_cancelled": order.is_cancelled
    }

def generate_pdf_today(include_cancelled: bool = False):
    today = datetime.now(NL_TZ).date()
    start_local = datetime.combine(today, datetime.min.time(), tzinfo=NL_TZ)
    start = start_local.astimezone(UTC).replace(tzinfo=None)

    q = Order.query.filter(Order.created_at >= start)
    if not include_cancelled:
        q = q.filter(Order.is_cancelled == False)
    orders = q.order_by(Order.created_at.desc()).all()

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = []

    data = [["Datum", "Tijd", "Naam", "Totaal", "Items", "Status"]]
    for o in orders:
        try:
            items = json.loads(o.items or "{}")
        except Exception:
            items = {}

        summary = ", ".join(f"{k} x {v.get('qty')}" for k, v in items.items())
        status = "Geannuleerd" if o.is_cancelled else ("Voltooid" if o.is_completed else "Open")
        data.append([
            to_nl(o.created_at).strftime("%Y-%m-%d"),
            to_nl(o.created_at).strftime("%H:%M"),
            o.customer_name,
            f"€{o.totaal:.2f}",
            summary,
            status
        ])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightblue),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(table)
    doc.build(elements)
    buffer.seek(0)
    return buffer

@app.route("/admin/orders/download/pdf")
@login_required
def download_pdf():
    include_cancelled = request.args.get('include_cancelled') == '1'
    output = generate_pdf_today(include_cancelled)
    return send_file(
        output,
        mimetype='application/pdf',
        as_attachment=True,
        download_name='bestellingen_vandaag.pdf'
    )
@app.route("/admin/orders/download/excel")
@login_required
def download_excel():
    include_cancelled = request.args.get('include_cancelled') == '1'
    date = request.args.get('date')
    start = request.args.get('start')
    end = request.args.get('end')

    if date:
        output = generate_excel_by_date(date, include_cancelled)
    elif start and end:
        output = generate_excel_by_range(start, end, include_cancelled)
    else:
        output = generate_excel_today(include_cancelled)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='bestellingen.xlsx'
    )
def generate_excel_by_date(date, include_cancelled=False):
    query = Order.query.filter(func.date(Order.created_at) == date)
    if not include_cancelled:
        query = query.filter(Order.is_cancelled == False)
    orders = query.order_by(Order.created_at.asc()).all()
    return build_excel(orders)
def generate_excel_by_range(start, end, include_cancelled=False):
    query = Order.query.filter(
        func.date(Order.created_at) >= start,
        func.date(Order.created_at) <= end
    )
    if not include_cancelled:
        query = query.filter(Order.is_cancelled == False)
    orders = query.order_by(Order.created_at.asc()).all()
    return build_excel(orders)
def build_excel(orders):
    order_dicts = orders_to_dicts(orders)

    if not order_dicts:
        df = pd.DataFrame([{"Melding": "Geen bestellingen gevonden."}])
    else:
        df = pd.DataFrame(order_dicts)

    # Omzet overzicht berekenen
    total = sum(float(o['totaal']) for o in order_dicts if not o['is_cancelled'])
    pin = sum(float(o['totaal']) for o in order_dicts if 'pin' in (o['payment_method'] or '').lower() and not o['is_cancelled'])
    online = sum(float(o['totaal']) for o in order_dicts if 'online' in (o['payment_method'] or '').lower() and not o['is_cancelled'])
    contant = sum(float(o['totaal']) for o in order_dicts if 'contant' in (o['payment_method'] or '').lower() and not o['is_cancelled'])
    credit = sum(float(o['totaal']) for o in order_dicts if 'rekening' in (o['payment_method'] or '').lower() and not o['is_cancelled'])

    omzet_data = {
        'Omschrijving': ['Totale omzet', 'Pin betaling', 'Online betaling', 'Contant', 'Op rekening'],
        'Bedrag': [total, pin, online, contant, credit]
    }
    omzet_df = pd.DataFrame(omzet_data)

    # Excel schrijven
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Bestellingen')
        omzet_df.to_excel(writer, index=False, sheet_name='Omzet Overzicht')
    output.seek(0)

    return output






def build_maps_link(street: str, house_number: str, postcode: str, city: str) -> str | None:
    """Create a Google Maps search URL for the given address."""
    if not all([street, house_number, postcode, city]):
        return None
    address = f"{street} {house_number}, {postcode} {city}"
    return f"https://www.google.com/maps?q={quote(address)}"
def orders_to_dicts(orders):
    result = []
    for o in orders:
        try:
            o.items_dict = json.loads(o.items or '{}')
        except Exception:
            try:
                import ast
                o.items_dict = ast.literal_eval(o.items)
            except Exception:
                o.items_dict = {}
        totaal = o.totaal or 0
        result.append({
            "id": o.id,
            "order_type": o.order_type,
            "customer_name": o.customer_name,
            "phone": o.phone,
            "email": o.email,
            "payment_method": o.payment_method,
            "pickup_time": o.pickup_time,
            "delivery_time": o.delivery_time,
            "tijdslot_display": o.tijdslot_display,
            "pickupTime": o.pickup_time,
            "deliveryTime": o.delivery_time,
            "postcode": o.postcode,
            "house_number": o.house_number,
            "street": o.street,
            "city": o.city,
            "maps_link": build_maps_link(o.street, o.house_number, o.postcode, o.city),
            "opmerking": o.opmerking,
            "created_date": to_nl(o.created_at).strftime("%Y-%m-%d"),
            "created_at": to_nl(o.created_at).strftime("%H:%M"),
            "items": o.items_dict,
            "total": totaal,
            "totaal": totaal,
            "fooi": o.fooi or 0,
            "order_number": o.order_number,
            "is_completed": o.is_completed,
            "is_cancelled": o.is_cancelled
        })
    return result


def get_bubble_options_dict():
    opts = {'base': [], 'smaak': [], 'topping': []}
    for o in BubbleOption.query.order_by(BubbleOption.id).all():
        opts[o.category].append({'id': o.id, 'name': o.name, 'price': o.price})
    return opts


def get_xbento_options_dict():
    opts = {'main': [], 'side': [], 'rice': [], 'groente': []}
    for o in XbentoOption.query.order_by(XbentoOption.id).all():
        opts[o.category].append({'id': o.id, 'name': o.name, 'price': o.price})
    return opts


def _parse_minutes(t: str) -> int:
    h, m = [int(x) for x in t.split(':')]
    return h * 60 + m


def _in_range(start: str, end: str, now_min: int) -> bool:
    s = _parse_minutes(start)
    e = _parse_minutes(end)
    if s <= e:
        return s <= now_min < e
    return now_min >= s or now_min < e


def order_type_open(order_type: str) -> bool:
    settings = {s.key: s.value for s in Setting.query.all()}
    website_on = settings.get('is_open', 'true') != 'false'
    if not website_on:
        return False
    closed_days = [d for d in (settings.get('closed_days') or '').split(',') if d]
    day_name = datetime.now(NL_TZ).strftime('%A')
    if day_name in closed_days:
        return False
    if order_type == 'afhalen':
        if settings.get('pickup_enabled', 'true') == 'false':
            return False
        start = settings.get('pickup_start', '00:00')
        end = settings.get('pickup_end', '00:00')
    else:
        if settings.get('delivery_enabled', 'true') == 'false':
            return False
        start = settings.get('delivery_start', '00:00')
        end = settings.get('delivery_end', '00:00')
    now = datetime.now(NL_TZ)
    now_min = now.hour * 60 + now.minute
    return _in_range(start, end, now_min)


# Socket.IO for real-time updates
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")




# 设置登录管理
login_manager = LoginManager(app)
login_manager.login_view = "login"

# 数据模型
class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20))
    order_type = db.Column(db.String(20))
    customer_name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    pickup_time = db.Column(db.String(20))
    delivery_time = db.Column(db.String(20))
    tijdslot_display = db.Column(db.String(20))
    payment_method = db.Column(db.String(20))
    postcode = db.Column(db.String(10))
    house_number = db.Column(db.String(10))
    street = db.Column(db.String(100))
    city = db.Column(db.String(100))
    opmerking = db.Column(db.Text)
    items = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    totaal = db.Column(db.Float)
    fooi = db.Column(db.Float, default=0.0)
    discount_code = db.Column(db.String(50))  # ✅ 新增
    discount_amount = db.Column(db.Float, default=0.0)  # ✅ 新增
    is_completed = db.Column(db.Boolean, default=False)
    is_cancelled = db.Column(db.Boolean, default=False)



class Setting(db.Model):
    __tablename__ = 'settings'
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(200))

class Review(db.Model):
    __tablename__ = 'reviews'
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(20), db.ForeignKey('orders.order_number'), unique=True, nullable=False)
    customer_name = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    rating = db.Column(db.Integer, default=0)
    reply = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class DiscountCode(db.Model):
    __tablename__ = 'discount_codes'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)
    discount_percentage = db.Column(db.Float, default=3.0)
    is_used = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    customer_email = db.Column(db.String(120))
    discount_amount = db.Column(db.Float, default=0.0)  # ✅ 必须加这个


class MenuSection(db.Model):
    __tablename__ = 'menu_sections'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)


class MenuItem(db.Model):
    __tablename__ = 'menu_items'
    id = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey('menu_sections.id'))
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, default=0.0)
    image = db.Column(db.String(200))
    section = db.relationship('MenuSection', backref=db.backref('items', lazy=True))


class BubbleOption(db.Model):
    __tablename__ = 'bubble_options'
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(20))  # base, smaak, topping
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, default=0.0)


class XbentoOption(db.Model):
    __tablename__ = 'xbento_options'
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(20))  # main, side, rice, groente
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, default=0.0)


with app.app_context():
    db.create_all()
    defaults = {
        "is_open": "true",
        "open_time": "11:00",
        "close_time": "21:00",
        "closed_days": "",
        "pickup_enabled": "true",
        "delivery_enabled": "true",
        "pickup_start": "11:00",
        "pickup_end": "21:00",
        "delivery_start": "11:00",
        "delivery_end": "21:00",
        "time_interval": "15",
        "milktea_soldout": "false",
        "milktea_price": "5",
        "price_zalm_crispy_rice_sandwich": "7",
        "price_spicytuna_crispy_rice_sandwich": "7",
        "price_ebi_crispy_rice_sandwich": "7",
        "price_beef_crispy_rice_sandwich": "7.5",
        "price_california_crispy_rice_sandwich": "7.5",
        "price_chicken_crispy_rice_sandwich": "7",
        "soldout_japans_chicken_bento": "false",
        "soldout_korean_chicken_bento": "false",
        "soldout_meatlover_bento": "false",
        "soldout_zalm_lover_bento": "false",
        "soldout_ebi_lover_bento": "false",
        "soldout_surf_turf_bento": "false",
        "soldout_dimsum_bento": "false",
        "soldout_lamskotelet_bento": "false",
        "soldout_unagi_bento": "false",
        "soldout_veggie_bento": "false",
        "soldout_sushi_bento": "false",
        "soldout_salmon_roll": "false",
        "soldout_dragon_roll": "false",
        "soldout_beef_roll": "false",
        "soldout_chicken_roll": "false",
        "soldout_nigiri_box": "false",
        "soldout_salmon_sashimi": "false",
        "soldout_flamed_salmon_sashimi": "false",
        "soldout_tonijn_sashimi": "false",
        "soldout_flamed_tonijn_sashimi": "false",
        "soldout_beef_sashimi": "false",
        "soldout_zalm_crispy_rice_sandwich": "false",
        "soldout_spicytuna_crispy_rice_sandwich": "false",
        "soldout_ebi_crispy_rice_sandwich": "false",
        "soldout_beef_crispy_rice_sandwich": "false",
        "soldout_california_crispy_rice_sandwich": "false",
        "soldout_chicken_crispy_rice_sandwich": "false",
        "soldout_xbento": "false",
        "soldout_zalm_bowl": "false",
        "soldout_tuna_bowl": "false",
        "soldout_ebi_fry_bowl": "false",
        "soldout_chicken_karaage_bowl": "false",
        "soldout_spicy_chicken_bowl": "false",
        "soldout_teriyaki_chicken_bowl": "false",
        "soldout_teriyaki_beef_bowl": "false",
        "soldout_california_bowl": "false",
        "soldout_vega_bowl": "false",
        "soldout_meatlover_bowl": "false",
        "soldout_rainbow_bowl": "false",
        "soldout_spicy_tuna_bowl": "false",
        "soldout_flamed_zalm_bowl": "false",
        "soldout_flamed_tuna_bowl": "false",
        "soldout_x_bowl": "false",
        "soldout_ebi_ramen": "false",
        "soldout_chicken_ramen": "false",
        "soldout_beef_ramen": "false",
        "soldout_ribeye_ramen": "false",
        "soldout_chasiu_ramen": "false",
        "soldout_karaage": "false",
        "soldout_ebi_fry": "false",
        "soldout_spicy_crispy_chicken": "false",
        "soldout_chicken_loempia": "false",
        "soldout_gyoza": "false",
        "soldout_inktvis_ringen": "false",
        "soldout_sesambal": "false",
        "soldout_yakitori": "false",
        "soldout_mini_loempia": "false",
        "soldout_edamame": "false",
        "soldout_kimchi_komkommer": "false",
        "soldout_kimchi_kool": "false",
        "soldout_zeewiersalade": "false",
        "soldout_mochi_mango": "false",
        "soldout_mochi_aardbei": "false",
        "soldout_mochi_matcha": "false",
        "soldout_mochi_pistachio": "false",
        "soldout_cola": "false",
        "soldout_cola_zero": "false",
        "soldout_spa_blauw": "false",
        "soldout_spa_rood": "false",
        "soldout_red_bull": "false",
    }
    for k, v in defaults.items():
        if not Setting.query.filter_by(key=k).first():
            db.session.add(Setting(key=k, value=v))
    db.session.commit()

    if BubbleOption.query.count() == 0:
        defaults_base = ['Green Tea', 'Milk Tea', 'Milkshake']
        defaults_smaak = ['Mango', 'Appel', 'Matcha', 'Brown Sugar']
        defaults_topping = ['Appel Popping', 'Perzik Popping', 'Tapioca']
        for n in defaults_base:
            db.session.add(BubbleOption(category='base', name=n, price=0.0))
        for n in defaults_smaak:
            db.session.add(BubbleOption(category='smaak', name=n, price=0.0))
        for n in defaults_topping:
            db.session.add(BubbleOption(category='topping', name=n, price=0.0))
        db.session.commit()


class User(UserMixin):
    def __init__(self, user_id: str):
        self.id = user_id

@login_manager.user_loader
def load_user(user_id: str):
    return User("admin") if user_id == "admin" else None

# 首页
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/review-list')
def review_list_page():
    return render_template('review-list.html')
# Review submission page
@app.route('/review')
def review_page():
    order_number = request.args.get('order') or ''
    return render_template('review.html', order_number=order_number)

# Payment success page
@app.route('/payment-success')
def payment_success_page():
    return render_template('payment_success.html')

# POS
@app.route('/pos', methods=["GET", "POST"])
@login_required
def pos():
    if request.method == "POST":
        data = request.get_json() or {}
        order_number = data.get("order_number") or data.get("orderNumber")

        order = Order(
            order_type=data.get("order_type") or data.get("orderType"),
            customer_name=data.get("customer_name") or data.get("name"),
            phone=data.get("phone"),
            email=data.get("email") or data.get("customerEmail"),
            pickup_time=data.get("pickup_time") or data.get("pickupTime"),
            delivery_time=data.get("delivery_time") or data.get("deliveryTime"),
            payment_method=data.get("payment_method") or data.get("paymentMethod"),
            postcode=data.get("postcode"),
            house_number=data.get("house_number"),
            street=data.get("street"),
            city=data.get("city"),
            opmerking=data.get("opmerking") or data.get("remark"),
            items=json.dumps(data.get("items", {})),
            order_number=order_number
        )   
        db.session.add(order)
        db.session.commit()


        resp = {"success": True}
        if str(order.payment_method).lower() == "online":
            url = os.getenv("TIKKIE_URL")
            if url:
                resp["paymentLink"] = url

        return jsonify(resp)

    # 之前会在此向 POS 页面推送今日订单信息，现已不再需要
    return render_template("pos.html")


# 接收前端订单提交
@app.route('/api/orders', methods=["POST"])
def api_orders():
    try:
        data = request.get_json() or {}
        order_number = data.get("order_number") or data.get("orderNumber")

        # ===== 新时间判断逻辑开始 =====
        from datetime import datetime

        order_type = data.get("orderType") or data.get("order_type")
        settings = {s.key: s.value for s in Setting.query.all()}
        now = datetime.now(NL_TZ)

        if order_type == 'afhalen':
            start_str = settings.get('pickup_start', '00:00')
            end_str = settings.get('pickup_end', '23:59')
            gekozen = data.get("pickup_time") or data.get("pickupTime") or ""
            gesloten_message = "Afhalen is gesloten voor vandaag."
        else:
            start_str = settings.get('delivery_start', '00:00')
            end_str = settings.get('delivery_end', '23:59')
            gekozen = data.get("delivery_time") or data.get("deliveryTime") or ""
            gesloten_message = "Bezorging is gesloten voor vandaag."

        start_hour, start_minute = map(int, start_str.split(':'))
        end_hour, end_minute = map(int, end_str.split(':'))

        start_today = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        end_today = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)

        chosen_dt = None
        if gekozen:
            try:
                ch, cm = map(int, gekozen.split(':'))
                chosen_dt = now.replace(hour=ch, minute=cm, second=0, microsecond=0)
            except Exception:
                pass

        if now < start_today:
            if chosen_dt:
                return jsonify({"status": "fail", "error": "U bestelt buiten openingstijden. Vooruitbestellen is mogelijk."}), 403
            else:
                return jsonify({"status": "fail", "error": "Vandaag is gesloten, morgen kan niet vooraf besteld worden."}), 403

        if now > end_today:
            return jsonify({"status": "fail", "error": gesloten_message}), 403
        # ===== 新时间判断逻辑结束 =====

        # 1. 构造订单对象（初始字段）
        order = Order(
            order_type=order_type,
            customer_name=data.get("name") or data.get("customer_name"),
            phone=data.get("phone"),
            email=data.get("customerEmail") or data.get("email"),
            pickup_time=data.get("pickup_time") or data.get("pickupTime"),
            delivery_time=data.get("delivery_time") or data.get("deliveryTime"),
            tijdslot_display=data.get("tijdslot_display"),
            payment_method=data.get("paymentMethod") or data.get("payment_method"),
            postcode=data.get("postcode"),
            house_number=data.get("house_number"),
            street=data.get("street"),
            city=data.get("city"),
            opmerking=data.get("opmerking") or data.get("remark"),
            items=json.dumps(data.get("items", {})),
            order_number=order_number,
            fooi=float(data.get("tip") or data.get("fooi") or 0),
            discount_code=data.get("discount_code") or data.get("discountCode"),
            discount_amount=data.get("discount_amount")
        )

        # 2. 计算 subtotal / totaal
        items = json.loads(order.items or "{}")
        subtotal = sum(
            float(i.get("price", 0)) * int(i.get("qty", 0))
            for i in items.values()
        )
        order.totaal = float(data.get("totaal") or subtotal)

        # 3. 保存订单到数据库
        db.session.add(order)
        db.session.commit()

        # 4. 如有折扣码，记录到 discount_codes 表
        discount_code = data.get("discount_code") or data.get("discountCode")
        customer_email = (
            data.get("customer_email")
            or data.get("customerEmail")
            or order.email
        )
        discount_amount = data.get("discount_amount") or 0

        if discount_code and customer_email:
            disc = DiscountCode(
                code=discount_code,
                customer_email=customer_email,
                discount_percentage=3.0,
                discount_amount=discount_amount,
                is_used=False,
            )
            db.session.add(disc)
            db.session.commit()
            print(f"✅ 折扣码保存成功: {discount_code} for {customer_email} met korting {discount_amount}")

        print("✅ 接收到订单:", data)

        # 6. 返回响应
        resp = {"status": "ok"}
        if str(order.payment_method).lower() == "online":
            pay_url = os.getenv("TIKKIE_URL")
            if pay_url:
                resp["paymentLink"] = pay_url

        return jsonify(resp), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "fail", "error": str(e)}), 500


@app.route('/submit_order', methods=["POST"])
def submit_order():
    # 兼容旧接口，转发数据到现有逻辑
    return api_orders()




@app.route("/api/discounts/validate", methods=["POST"])
def validate_discount():
    try:
        data = request.get_json()
        code = data.get("code")
        order_total = float(data.get("order_total") or 0)

        disc = DiscountCode.query.filter_by(code=code, is_used=False).first()
        if not disc:
            return jsonify({"valid": False, "error": "Invalid or used code"}), 400

        if order_total < 20:
            return jsonify({"valid": False, "error": "Minimum order total not met"}), 400

        # ✅ 改成使用数据库折扣金额
        discount_amount = disc.discount_amount

        new_total = max(0, order_total - discount_amount)

        disc.is_used = True
        db.session.commit()

        return jsonify({
            "valid": True,
            "discount_amount": discount_amount,
            "new_total": new_total
        }), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 获取设置
@app.route('/api/settings/<key>')
def get_setting(key):
    s = Setting.query.filter_by(key=key).first()
    return jsonify({key: s.value if s else None})

@app.route('/api/settings')
def get_all_settings():
    settings = {s.key: s.value for s in Setting.query.all()}
    return jsonify(settings)

# ----- Menu API -----
@app.route('/api/menu')
def api_menu():
    items = MenuItem.query.all()
    data = [
        {
            'id': i.id,
            'name': i.name,
            'price': i.price,
            'section': i.section.name if i.section else None,
            'image': i.image,
        }
        for i in items
    ]
    return jsonify(data)


@app.route('/api/update_item', methods=['POST'])
def api_update_item():
    data = request.get_json() or {}
    item_id = data.get('id')
    item = MenuItem.query.get(item_id)
    if not item:
        return jsonify({'success': False, 'error': 'not_found'}), 404
    if 'price' in data:
        try:
            item.price = float(data['price'])
        except (TypeError, ValueError):
            pass
    soldout_key = data.get('soldout_key')
    if soldout_key is not None and 'sold_out' in data:
        val = 'true' if data.get('sold_out') else 'false'
        s = Setting.query.filter_by(key=soldout_key).first()
        if not s:
            s = Setting(key=soldout_key, value=val)
            db.session.add(s)
        else:
            s.value = val
    db.session.commit()
    items = [
        {
            'id': i.id,
            'name': i.name,
            'price': i.price,
            'section': i.section.name if i.section else None,
            'image': i.image,
        } for i in MenuItem.query.all()
    ]
    socketio.emit('menu_update', items)
    settings = {s.key: s.value for s in Setting.query.all()}
    socketio.emit('setting_update', settings)
    return jsonify({'success': True})

@app.route('/api/bubble_options')
def api_bubble_options():
    return jsonify(get_bubble_options_dict())

@app.route('/api/xbento_options')
def api_xbento_options():
    return jsonify(get_xbento_options_dict())

@app.route('/api/orders/<int:order_id>/status', methods=['POST'])
@login_required
def update_order_status(order_id: int):
    data = request.get_json() or {}
    order = Order.query.get_or_404(order_id)
    if 'is_completed' in data:
        order.is_completed = bool(data['is_completed'])
    if 'is_cancelled' in data:
        order.is_cancelled = bool(data['is_cancelled'])
    db.session.commit()
    return jsonify({'success': True, 'is_completed': order.is_completed, 'is_cancelled': order.is_cancelled})

@app.route('/api/orders/<int:order_id>', methods=['PUT'])
@login_required
def edit_order(order_id: int):
    order = Order.query.get_or_404(order_id)
    data = request.get_json() or {}
    allowed = [
        'customer_name', 'phone', 'email', 'street', 'house_number', 'postcode',
        'city', 'pickup_time', 'delivery_time', 'order_type', 'items',
        'payment_method', 'totaal', 'fooi'
    ]
    for f in allowed:
        if f not in data:
            continue
        val = data[f]
        if f == 'items':
            if not isinstance(val, str):
                order.items = json.dumps(val)
            else:
                order.items = val
        elif f in ('totaal', 'fooi'):
            try:
                setattr(order, f, float(val))
            except (TypeError, ValueError):
                setattr(order, f, 0.0)
        else:
            setattr(order, f, val)
    if 'tip' in data:
        try:
            order.fooi = float(data['tip'])
        except (TypeError, ValueError):
            order.fooi = 0.0
    db.session.commit()
    return jsonify({'success': True})

# ----- Review API -----
@app.route('/api/reviews', methods=['GET', 'POST'])
def reviews_api():
    if request.method == 'POST':
        data = request.json
        if not data:
            return jsonify({'error': 'Geen data ontvangen.'}), 400

        name = data.get('customer_name')
        content = data.get('content')
        rating = data.get('rating')
        order_number = data.get('order_number')

        if not (name and content and order_number and isinstance(rating, int)):
            return jsonify({'error': 'Ongeldige data.'}), 400

        # 检查订单号是否已存在评论，防止重复
        existing_review = Review.query.filter_by(order_number=order_number).first()
        if existing_review:
            return jsonify({'error': 'U heeft al een review ingediend voor dit ordernummer.'}), 400

        new_review = Review(
            customer_name=name,
            content=content,
            rating=rating,
            order_number=order_number
        )
        db.session.add(new_review)
        db.session.commit()

        # SocketIO 实时推送（如果你已启用）
        socketio.emit('new_review', {
            'customer_name': new_review.customer_name,
            'content': new_review.content,
            'rating': new_review.rating
        })

        return jsonify({'message': 'Review succesvol ontvangen.'}), 200

    # GET 请求：获取评论，支持分页
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 5))
    except ValueError:
        page = 1
        per_page = 5

    query = Review.query.order_by(Review.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    reviews = [
        {
            'id': r.id,
            'customer_name': r.customer_name,
            'content': r.content,
            'rating': r.rating,
            'reply': r.reply,
            'created_at': r.created_at.isoformat()
        }
        for r in pagination.items
    ]

    return jsonify({
        'reviews': reviews,
        'total': pagination.total,
        'page': page,
        'per_page': per_page
    })


@app.route('/api/reviews/<int:review_id>/reply', methods=['POST'])
@login_required
def review_reply(review_id: int):
    data = request.get_json() or {}
    reply_text = data.get('reply', '')
    rev = Review.query.get_or_404(review_id)
    rev.reply = reply_text
    db.session.commit()
    socketio.emit('review_reply', {'id': rev.id, 'reply': rev.reply})
    return jsonify({'success': True, 'reply': rev.reply})


@app.route('/api/reviews/<int:review_id>', methods=['DELETE'])
@login_required
def delete_review(review_id: int):
    rev = Review.query.get_or_404(review_id)
    db.session.delete(rev)
    db.session.commit()
    socketio.emit('delete_review', {'id': review_id})
    return jsonify({'success': True})



# Mijn Nova Asia 管理后台
@app.route('/dashboard')
@login_required
def dashboard():
    def get_value(key, default):
        s = Setting.query.filter_by(key=key).first()
        return s.value if s else default

    sections = MenuSection.query.all()
    bases = BubbleOption.query.filter_by(category='base').all()
    smaken = BubbleOption.query.filter_by(category='smaak').all()
    toppings = BubbleOption.query.filter_by(category='topping').all()
    xbento_main = XbentoOption.query.filter_by(category='main').all()
    xbento_side = XbentoOption.query.filter_by(category='side').all()
    xbento_rice = XbentoOption.query.filter_by(category='rice').all()
    xbento_groente = XbentoOption.query.filter_by(category='groente').all()
    return render_template(
        'dashboard.html',
        is_open=get_value('is_open', 'true'),
        open_time=get_value('open_time', '11:00'),
        close_time=get_value('close_time', '21:00'),
        closed_days=get_value('closed_days', ''),
        pickup_enabled=get_value('pickup_enabled', 'true'),
        delivery_enabled=get_value('delivery_enabled', 'true'),
        pickup_start=get_value('pickup_start', '11:00'),
        pickup_end=get_value('pickup_end', '21:00'),
        pickup_address=get_value('pickup_address', ''),
        delivery_start=get_value('delivery_start', '11:00'),
        delivery_end=get_value('delivery_end', '21:00'),
        delivery_postcodes=get_value('delivery_postcodes', ''),
        time_interval=get_value('time_interval', '15'),
        milktea_soldout=get_value('milktea_soldout', 'false'),
        milktea_price=get_value('milktea_price', '5'),
        price_zalm_crispy_rice_sandwich=get_value('price_zalm_crispy_rice_sandwich', '7'),
        price_spicytuna_crispy_rice_sandwich=get_value('price_spicytuna_crispy_rice_sandwich', '7'),
        price_ebi_crispy_rice_sandwich=get_value('price_ebi_crispy_rice_sandwich', '7'),
        price_beef_crispy_rice_sandwich=get_value('price_beef_crispy_rice_sandwich', '7.5'),
        price_california_crispy_rice_sandwich=get_value('price_california_crispy_rice_sandwich', '7.5'),
        price_chicken_crispy_rice_sandwich=get_value('price_chicken_crispy_rice_sandwich', '7'),
        soldout_japans_chicken_bento=get_value('soldout_japans_chicken_bento', 'false'),
        soldout_korean_chicken_bento=get_value('soldout_korean_chicken_bento', 'false'),
        soldout_meatlover_bento=get_value('soldout_meatlover_bento', 'false'),
        soldout_zalm_lover_bento=get_value('soldout_zalm_lover_bento', 'false'),
        soldout_ebi_lover_bento=get_value('soldout_ebi_lover_bento', 'false'),
        soldout_surf_turf_bento=get_value('soldout_surf_turf_bento', 'false'),
        soldout_dimsum_bento=get_value('soldout_dimsum_bento', 'false'),
        soldout_lamskotelet_bento=get_value('soldout_lamskotelet_bento', 'false'),
        soldout_unagi_bento=get_value('soldout_unagi_bento', 'false'),
        soldout_veggie_bento=get_value('soldout_veggie_bento', 'false'),
        soldout_sushi_bento=get_value('soldout_sushi_bento', 'false'),
        soldout_salmon_roll=get_value('soldout_salmon_roll', 'false'),
        soldout_dragon_roll=get_value('soldout_dragon_roll', 'false'),
        soldout_beef_roll=get_value('soldout_beef_roll', 'false'),
        soldout_chicken_roll=get_value('soldout_chicken_roll', 'false'),
        soldout_nigiri_box=get_value('soldout_nigiri_box', 'false'),
        soldout_salmon_sashimi=get_value('soldout_salmon_sashimi', 'false'),
        soldout_flamed_salmon_sashimi=get_value('soldout_flamed_salmon_sashimi', 'false'),
        soldout_tonijn_sashimi=get_value('soldout_tonijn_sashimi', 'false'),
        soldout_flamed_tonijn_sashimi=get_value('soldout_flamed_tonijn_sashimi', 'false'),
        soldout_beef_sashimi=get_value('soldout_beef_sashimi', 'false'),
        soldout_zalm_crispy_rice_sandwich=get_value('soldout_zalm_crispy_rice_sandwich', 'false'),
        soldout_spicytuna_crispy_rice_sandwich=get_value('soldout_spicytuna_crispy_rice_sandwich', 'false'),
        soldout_ebi_crispy_rice_sandwich=get_value('soldout_ebi_crispy_rice_sandwich', 'false'),
        soldout_beef_crispy_rice_sandwich=get_value('soldout_beef_crispy_rice_sandwich', 'false'),
        soldout_california_crispy_rice_sandwich=get_value('soldout_california_crispy_rice_sandwich', 'false'),
        soldout_chicken_crispy_rice_sandwich=get_value('soldout_chicken_crispy_rice_sandwich', 'false'),
        soldout_xbento=get_value('soldout_xbento', 'false'),
        soldout_zalm_bowl=get_value('soldout_zalm_bowl', 'false'),
        soldout_tuna_bowl=get_value('soldout_tuna_bowl', 'false'),
        soldout_ebi_fry_bowl=get_value('soldout_ebi_fry_bowl', 'false'),
        soldout_chicken_karaage_bowl=get_value('soldout_chicken_karaage_bowl', 'false'),
        soldout_spicy_chicken_bowl=get_value('soldout_spicy_chicken_bowl', 'false'),
        soldout_teriyaki_chicken_bowl=get_value('soldout_teriyaki_chicken_bowl', 'false'),
        soldout_teriyaki_beef_bowl=get_value('soldout_teriyaki_beef_bowl', 'false'),
        soldout_california_bowl=get_value('soldout_california_bowl', 'false'),
        soldout_vega_bowl=get_value('soldout_vega_bowl', 'false'),
        soldout_meatlover_bowl=get_value('soldout_meatlover_bowl', 'false'),
        soldout_rainbow_bowl=get_value('soldout_rainbow_bowl', 'false'),
        soldout_spicy_tuna_bowl=get_value('soldout_spicy_tuna_bowl', 'false'),
        soldout_flamed_zalm_bowl=get_value('soldout_flamed_zalm_bowl', 'false'),
        soldout_flamed_tuna_bowl=get_value('soldout_flamed_tuna_bowl', 'false'),
        soldout_x_bowl=get_value('soldout_x_bowl', 'false'),
        soldout_ebi_ramen=get_value('soldout_ebi_ramen', 'false'),
        soldout_chicken_ramen=get_value('soldout_chicken_ramen', 'false'),
        soldout_beef_ramen=get_value('soldout_beef_ramen', 'false'),
        soldout_ribeye_ramen=get_value('soldout_ribeye_ramen', 'false'),
        soldout_chasiu_ramen=get_value('soldout_chasiu_ramen', 'false'),
        soldout_karaage=get_value('soldout_karaage', 'false'),
        soldout_ebi_fry=get_value('soldout_ebi_fry', 'false'),
        soldout_spicy_crispy_chicken=get_value('soldout_spicy_crispy_chicken', 'false'),
        soldout_chicken_loempia=get_value('soldout_chicken_loempia', 'false'),
        soldout_gyoza=get_value('soldout_gyoza', 'false'),
        soldout_inktvis_ringen=get_value('soldout_inktvis_ringen', 'false'),
        soldout_sesambal=get_value('soldout_sesambal', 'false'),
        soldout_yakitori=get_value('soldout_yakitori', 'false'),
        soldout_mini_loempia=get_value('soldout_mini_loempia', 'false'),
        soldout_edamame=get_value('soldout_edamame', 'false'),
        soldout_kimchi_komkommer=get_value('soldout_kimchi_komkommer', 'false'),
        soldout_kimchi_kool=get_value('soldout_kimchi_kool', 'false'),
        soldout_zeewiersalade=get_value('soldout_zeewiersalade', 'false'),
        soldout_mochi_mango=get_value('soldout_mochi_mango', 'false'),
        soldout_mochi_aardbei=get_value('soldout_mochi_aardbei', 'false'),
        soldout_mochi_matcha=get_value('soldout_mochi_matcha', 'false'),
        soldout_mochi_pistachio=get_value('soldout_mochi_pistachio', 'false'),
        soldout_cola=get_value('soldout_cola', 'false'),
        soldout_cola_zero=get_value('soldout_cola_zero', 'false'),
        soldout_spa_blauw=get_value('soldout_spa_blauw', 'false'),
        soldout_spa_rood=get_value('soldout_spa_rood', 'false'),
        soldout_red_bull=get_value('soldout_red_bull', 'false'),
        sections=sections,
        base_options=bases,
        smaak_options=smaken,
        topping_options=toppings,
        xbento_main=xbento_main,
        xbento_side=xbento_side,
        xbento_rice=xbento_rice,
        xbento_groente=xbento_groente,
    )


@app.route('/dashboard/update', methods=['POST'])
@login_required
def update_setting():
    data = request.get_json()
    is_open_val = data.get('is_open', 'true')
    open_time_val = data.get('open_time', '11:00')
    close_time_val = data.get('close_time', '21:00')
    closed_days_val = data.get('closed_days', '')
    pickup_enabled_val = data.get('pickup_enabled', 'true')
    delivery_enabled_val = data.get('delivery_enabled', 'true')
    pickup_start_val = data.get('pickup_start', '11:00')
    pickup_end_val = data.get('pickup_end', '21:00')
    pickup_address_val = data.get('pickup_address', '')
    delivery_start_val = data.get('delivery_start', '11:00')
    delivery_end_val = data.get('delivery_end', '21:00')
    delivery_postcodes_val = data.get('delivery_postcodes', '')
    time_interval_val = data.get('time_interval', '15')
    milktea_soldout_val = data.get('milktea_soldout', 'false')
    soldout_japans_chicken_bento_val = data.get('soldout_japans_chicken_bento', 'false')
    soldout_korean_chicken_bento_val = data.get('soldout_korean_chicken_bento', 'false')
    soldout_meatlover_bento_val = data.get('soldout_meatlover_bento', 'false')
    soldout_zalm_lover_bento_val = data.get('soldout_zalm_lover_bento', 'false')
    soldout_ebi_lover_bento_val = data.get('soldout_ebi_lover_bento', 'false')
    soldout_surf_turf_bento_val = data.get('soldout_surf_turf_bento', 'false')
    soldout_dimsum_bento_val = data.get('soldout_dimsum_bento', 'false')
    soldout_lamskotelet_bento_val = data.get('soldout_lamskotelet_bento', 'false')
    soldout_unagi_bento_val = data.get('soldout_unagi_bento', 'false')
    soldout_veggie_bento_val = data.get('soldout_veggie_bento', 'false')
    soldout_sushi_bento_val = data.get('soldout_sushi_bento', 'false')
    soldout_salmon_roll_val = data.get('soldout_salmon_roll', 'false')
    soldout_dragon_roll_val = data.get('soldout_dragon_roll', 'false')
    soldout_beef_roll_val = data.get('soldout_beef_roll', 'false')
    soldout_chicken_roll_val = data.get('soldout_chicken_roll', 'false')
    soldout_nigiri_box_val = data.get('soldout_nigiri_box', 'false')
    soldout_salmon_sashimi_val = data.get('soldout_salmon_sashimi', 'false')
    soldout_flamed_salmon_sashimi_val = data.get('soldout_flamed_salmon_sashimi', 'false')
    soldout_tonijn_sashimi_val = data.get('soldout_tonijn_sashimi', 'false')
    soldout_flamed_tonijn_sashimi_val = data.get('soldout_flamed_tonijn_sashimi', 'false')
    soldout_beef_sashimi_val = data.get('soldout_beef_sashimi', 'false')
    soldout_zalm_crispy_rice_sandwich_val = data.get('soldout_zalm_crispy_rice_sandwich', 'false')
    soldout_spicytuna_crispy_rice_sandwich_val = data.get('soldout_spicytuna_crispy_rice_sandwich', 'false')
    soldout_ebi_crispy_rice_sandwich_val = data.get('soldout_ebi_crispy_rice_sandwich', 'false')
    soldout_beef_crispy_rice_sandwich_val = data.get('soldout_beef_crispy_rice_sandwich', 'false')
    soldout_california_crispy_rice_sandwich_val = data.get('soldout_california_crispy_rice_sandwich', 'false')
    soldout_chicken_crispy_rice_sandwich_val = data.get('soldout_chicken_crispy_rice_sandwich', 'false')
    soldout_xbento_val = data.get('soldout_xbento', 'false')
    soldout_zalm_bowl_val = data.get('soldout_zalm_bowl', 'false')
    soldout_tuna_bowl_val = data.get('soldout_tuna_bowl', 'false')
    soldout_ebi_fry_bowl_val = data.get('soldout_ebi_fry_bowl', 'false')
    soldout_chicken_karaage_bowl_val = data.get('soldout_chicken_karaage_bowl', 'false')
    soldout_spicy_chicken_bowl_val = data.get('soldout_spicy_chicken_bowl', 'false')
    soldout_teriyaki_chicken_bowl_val = data.get('soldout_teriyaki_chicken_bowl', 'false')
    soldout_teriyaki_beef_bowl_val = data.get('soldout_teriyaki_beef_bowl', 'false')
    soldout_california_bowl_val = data.get('soldout_california_bowl', 'false')
    soldout_vega_bowl_val = data.get('soldout_vega_bowl', 'false')
    soldout_meatlover_bowl_val = data.get('soldout_meatlover_bowl', 'false')
    soldout_rainbow_bowl_val = data.get('soldout_rainbow_bowl', 'false')
    soldout_spicy_tuna_bowl_val = data.get('soldout_spicy_tuna_bowl', 'false')
    soldout_flamed_zalm_bowl_val = data.get('soldout_flamed_zalm_bowl', 'false')
    soldout_flamed_tuna_bowl_val = data.get('soldout_flamed_tuna_bowl', 'false')
    soldout_x_bowl_val = data.get('soldout_x_bowl', 'false')
    soldout_ebi_ramen_val = data.get('soldout_ebi_ramen', 'false')
    soldout_chicken_ramen_val = data.get('soldout_chicken_ramen', 'false')
    soldout_beef_ramen_val = data.get('soldout_beef_ramen', 'false')
    soldout_ribeye_ramen_val = data.get('soldout_ribeye_ramen', 'false')
    soldout_chasiu_ramen_val = data.get('soldout_chasiu_ramen', 'false')
    soldout_karaage_val = data.get('soldout_karaage', 'false')
    soldout_ebi_fry_val = data.get('soldout_ebi_fry', 'false')
    soldout_spicy_crispy_chicken_val = data.get('soldout_spicy_crispy_chicken', 'false')
    soldout_chicken_loempia_val = data.get('soldout_chicken_loempia', 'false')
    soldout_gyoza_val = data.get('soldout_gyoza', 'false')
    soldout_inktvis_ringen_val = data.get('soldout_inktvis_ringen', 'false')
    soldout_sesambal_val = data.get('soldout_sesambal', 'false')
    soldout_yakitori_val = data.get('soldout_yakitori', 'false')
    soldout_mini_loempia_val = data.get('soldout_mini_loempia', 'false')
    soldout_edamame_val = data.get('soldout_edamame', 'false')
    soldout_kimchi_komkommer_val = data.get('soldout_kimchi_komkommer', 'false')
    soldout_kimchi_kool_val = data.get('soldout_kimchi_kool', 'false')
    soldout_zeewiersalade_val = data.get('soldout_zeewiersalade', 'false')
    soldout_mochi_mango_val = data.get('soldout_mochi_mango', 'false')
    soldout_mochi_aardbei_val = data.get('soldout_mochi_aardbei', 'false')
    soldout_mochi_matcha_val = data.get('soldout_mochi_matcha', 'false')
    soldout_mochi_pistachio_val = data.get('soldout_mochi_pistachio', 'false')
    soldout_cola_val = data.get('soldout_cola', 'false')
    soldout_cola_zero_val = data.get('soldout_cola_zero', 'false')
    soldout_spa_blauw_val = data.get('soldout_spa_blauw', 'false')
    soldout_spa_rood_val = data.get('soldout_spa_rood', 'false')
    soldout_red_bull_val = data.get('soldout_red_bull', 'false')
    
    for key, val in [
        ('is_open', is_open_val),
        ('open_time', open_time_val),
        ('close_time', close_time_val),
        ('closed_days', closed_days_val),
        ('pickup_enabled', pickup_enabled_val),
        ('delivery_enabled', delivery_enabled_val),
        ('pickup_start', pickup_start_val),
        ('pickup_end', pickup_end_val),
        ('pickup_address', pickup_address_val),
        ('delivery_start', delivery_start_val),
        ('delivery_end', delivery_end_val),
        ('delivery_postcodes', delivery_postcodes_val),
        ('time_interval', time_interval_val),
        ('milktea_soldout', milktea_soldout_val),
        ('soldout_japans_chicken_bento', soldout_japans_chicken_bento_val),
        ('soldout_korean_chicken_bento', soldout_korean_chicken_bento_val),
        ('soldout_meatlover_bento', soldout_meatlover_bento_val),
        ('soldout_zalm_lover_bento', soldout_zalm_lover_bento_val),
        ('soldout_ebi_lover_bento', soldout_ebi_lover_bento_val),
        ('soldout_surf_turf_bento', soldout_surf_turf_bento_val),
        ('soldout_dimsum_bento', soldout_dimsum_bento_val),
        ('soldout_lamskotelet_bento', soldout_lamskotelet_bento_val),
        ('soldout_unagi_bento', soldout_unagi_bento_val),
        ('soldout_veggie_bento', soldout_veggie_bento_val),
        ('soldout_sushi_bento', soldout_sushi_bento_val),
        ('soldout_salmon_roll', soldout_salmon_roll_val),
        ('soldout_dragon_roll', soldout_dragon_roll_val),
        ('soldout_beef_roll', soldout_beef_roll_val),
        ('soldout_chicken_roll', soldout_chicken_roll_val),
        ('soldout_nigiri_box', soldout_nigiri_box_val),
        ('soldout_salmon_sashimi', soldout_salmon_sashimi_val),
        ('soldout_flamed_salmon_sashimi', soldout_flamed_salmon_sashimi_val),
        ('soldout_tonijn_sashimi', soldout_tonijn_sashimi_val),
        ('soldout_flamed_tonijn_sashimi', soldout_flamed_tonijn_sashimi_val),
        ('soldout_beef_sashimi', soldout_beef_sashimi_val),
        ('soldout_zalm_crispy_rice_sandwich', soldout_zalm_crispy_rice_sandwich_val),
        ('soldout_spicytuna_crispy_rice_sandwich', soldout_spicytuna_crispy_rice_sandwich_val),
        ('soldout_ebi_crispy_rice_sandwich', soldout_ebi_crispy_rice_sandwich_val),
        ('soldout_beef_crispy_rice_sandwich', soldout_beef_crispy_rice_sandwich_val),
        ('soldout_california_crispy_rice_sandwich', soldout_california_crispy_rice_sandwich_val),
        ('soldout_chicken_crispy_rice_sandwich', soldout_chicken_crispy_rice_sandwich_val),
        ('soldout_xbento', soldout_xbento_val),
        ('soldout_zalm_bowl', soldout_zalm_bowl_val),
        ('soldout_tuna_bowl', soldout_tuna_bowl_val),
        ('soldout_ebi_fry_bowl', soldout_ebi_fry_bowl_val),
        ('soldout_chicken_karaage_bowl', soldout_chicken_karaage_bowl_val),
        ('soldout_spicy_chicken_bowl', soldout_spicy_chicken_bowl_val),
        ('soldout_teriyaki_chicken_bowl', soldout_teriyaki_chicken_bowl_val),
        ('soldout_teriyaki_beef_bowl', soldout_teriyaki_beef_bowl_val),
        ('soldout_california_bowl', soldout_california_bowl_val),
        ('soldout_vega_bowl', soldout_vega_bowl_val),
        ('soldout_meatlover_bowl', soldout_meatlover_bowl_val),
        ('soldout_rainbow_bowl', soldout_rainbow_bowl_val),
        ('soldout_spicy_tuna_bowl', soldout_spicy_tuna_bowl_val),
        ('soldout_flamed_zalm_bowl', soldout_flamed_zalm_bowl_val),
        ('soldout_flamed_tuna_bowl', soldout_flamed_tuna_bowl_val),
        ('soldout_x_bowl', soldout_x_bowl_val),
        ('soldout_ebi_ramen', soldout_ebi_ramen_val),
        ('soldout_chicken_ramen', soldout_chicken_ramen_val),
        ('soldout_beef_ramen', soldout_beef_ramen_val),
        ('soldout_ribeye_ramen', soldout_ribeye_ramen_val),
        ('soldout_chasiu_ramen', soldout_chasiu_ramen_val),
        ('soldout_karaage', soldout_karaage_val),
        ('soldout_ebi_fry', soldout_ebi_fry_val),
        ('soldout_spicy_crispy_chicken', soldout_spicy_crispy_chicken_val),
        ('soldout_chicken_loempia', soldout_chicken_loempia_val),
        ('soldout_gyoza', soldout_gyoza_val),
        ('soldout_inktvis_ringen', soldout_inktvis_ringen_val),
        ('soldout_sesambal', soldout_sesambal_val),
        ('soldout_yakitori', soldout_yakitori_val),
        ('soldout_mini_loempia', soldout_mini_loempia_val),
        ('soldout_edamame', soldout_edamame_val),
        ('soldout_kimchi_komkommer', soldout_kimchi_komkommer_val),
        ('soldout_kimchi_kool', soldout_kimchi_kool_val),
        ('soldout_zeewiersalade', soldout_zeewiersalade_val),
        ('soldout_mochi_mango', soldout_mochi_mango_val),
        ('soldout_mochi_aardbei', soldout_mochi_aardbei_val),
        ('soldout_mochi_matcha', soldout_mochi_matcha_val),
        ('soldout_mochi_pistachio', soldout_mochi_pistachio_val),
        ('soldout_cola', soldout_cola_val),
        ('soldout_cola_zero', soldout_cola_zero_val),
        ('soldout_spa_blauw', soldout_spa_blauw_val),
        ('soldout_spa_rood', soldout_spa_rood_val),
        ('soldout_red_bull', soldout_red_bull_val),
        
    ]:
        s = Setting.query.filter_by(key=key).first()
        if not s:
            db.session.add(Setting(key=key, value=val))
        else:
            s.value = val

    db.session.commit()
    settings = {s.key: s.value for s in Setting.query.all()}
    socketio.emit('setting_update', settings)
    time_settings = {
        'pickup_start': settings.get('pickup_start'),
        'pickup_end': settings.get('pickup_end'),
        'delivery_start': settings.get('delivery_start'),
        'delivery_end': settings.get('delivery_end'),
        'time_interval': settings.get('time_interval')
    }
    socketio.emit('time_update', time_settings)
    return jsonify({'success': True})



@app.route('/dashboard/add_section', methods=['POST'])
@login_required
def add_section():
    name = request.form.get('section_name', '').strip()
    if name:
        section = MenuSection(name=name)
        db.session.add(section)
        db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/dashboard/add_item', methods=['POST'])
@login_required
def add_item():
    name = request.form.get('item_name', '').strip()
    price = request.form.get('price', '0')
    section_id = request.form.get('section_id')
    image_file = request.files.get('image')
    image_path = None
    if image_file and image_file.filename:
        filename = secure_filename(image_file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        image_file.save(filepath)
        image_path = f'uploads/{filename}'
    if name and section_id:
        try:
            price_value = float(price)
        except ValueError:
            price_value = 0.0
        item = MenuItem(
            name=name,
            price=price_value,
            section_id=section_id,
            image=image_path,
        )
        db.session.add(item)
        db.session.commit()
        items = [
            {
                'id': i.id,
                'name': i.name,
                'price': i.price,
                'section': i.section.name if i.section else None,
                'image': i.image,
            }
            for i in MenuItem.query.all()
        ]
        socketio.emit('menu_update', items)
    return redirect(url_for('dashboard'))


@app.route('/dashboard/item/<int:item_id>', methods=['POST'])
@login_required
def update_item(item_id):
    price = request.form.get('price', '0')
    try:
        price_value = float(price)
    except ValueError:
        price_value = 0.0
    item = MenuItem.query.get_or_404(item_id)
    item.price = price_value
    db.session.commit()
    items = [
        {
            'id': i.id,
            'name': i.name,
            'price': i.price,
            'section': i.section.name if i.section else None,
            'image': i.image,
        }
        for i in MenuItem.query.all()
    ]
    socketio.emit('menu_update', items)
    return redirect(url_for('dashboard'))


@app.route('/update_milktea_price', methods=['POST'])
@login_required
def update_milktea_price():
    data = request.get_json() or {}
    try:
        price_val = float(data.get('price'))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'invalid_price'}), 400

    setting = Setting.query.filter_by(key='milktea_price').first()
    if not setting:
        setting = Setting(key='milktea_price', value=str(price_val))
        db.session.add(setting)
    else:
        setting.value = str(price_val)
    db.session.commit()
    socketio.emit('milktea_price_update', {'price': price_val})
    return jsonify({'success': True})


@app.route('/update_crispy_prices', methods=['POST'])
@login_required
def update_crispy_prices():
    data = request.get_json() or {}
    mapping = {
        'zalm': 'price_zalm_crispy_rice_sandwich',
        'spicytuna': 'price_spicytuna_crispy_rice_sandwich',
        'ebi': 'price_ebi_crispy_rice_sandwich',
        'beef': 'price_beef_crispy_rice_sandwich',
        'california': 'price_california_crispy_rice_sandwich',
        'chicken': 'price_chicken_crispy_rice_sandwich',
    }
    updated = {}
    for k, setting_key in mapping.items():
        if k in data:
            try:
                price_val = float(data[k])
            except (TypeError, ValueError):
                continue
            setting = Setting.query.filter_by(key=setting_key).first()
            if not setting:
                setting = Setting(key=setting_key, value=str(price_val))
                db.session.add(setting)
            else:
                setting.value = str(price_val)
            updated[setting_key] = price_val
    db.session.commit()
    if updated:
        socketio.emit('crispy_price_update', updated)
    return jsonify({'success': True})


@app.route('/dashboard/bubble_options/add', methods=['POST'])
@login_required
def add_bubble_option():
    name = request.form.get('name', '').strip()
    category = request.form.get('category')
    price = request.form.get('price', '0')
    try:
        price_val = float(price)
    except ValueError:
        price_val = 0.0
    if name and category in ['base', 'smaak', 'topping']:
        opt = BubbleOption(name=name, category=category, price=price_val)
        db.session.add(opt)
        db.session.commit()
        socketio.emit('bubble_options_update', get_bubble_options_dict())
    return redirect(url_for('dashboard'))


@app.route('/dashboard/bubble_options/<int:opt_id>', methods=['POST'])
@login_required
def update_bubble_option(opt_id):
    opt = BubbleOption.query.get_or_404(opt_id)
    opt.name = request.form.get('name', opt.name)
    try:
        opt.price = float(request.form.get('price', opt.price))
    except ValueError:
        pass
    db.session.commit()
    socketio.emit('bubble_options_update', get_bubble_options_dict())
    return redirect(url_for('dashboard'))


@app.route('/dashboard/bubble_options/<int:opt_id>/delete', methods=['POST'])
@login_required
def delete_bubble_option(opt_id):
    opt = BubbleOption.query.get_or_404(opt_id)
    db.session.delete(opt)
    db.session.commit()
    socketio.emit('bubble_options_update', get_bubble_options_dict())
    return redirect(url_for('dashboard'))


@app.route('/dashboard/xbento_options/add', methods=['POST'])
@login_required
def add_xbento_option():
    name = request.form.get('name', '').strip()
    category = request.form.get('category')
    price = request.form.get('price', '0')
    try:
        price_val = float(price)
    except ValueError:
        price_val = 0.0
    if name and category in ['main', 'side', 'rice', 'groente']:
        opt = XbentoOption(name=name, category=category, price=price_val)
        db.session.add(opt)
        db.session.commit()
        socketio.emit('xbento_options_update', get_xbento_options_dict())
    return redirect(url_for('dashboard'))


@app.route('/dashboard/xbento_options/<int:opt_id>', methods=['POST'])
@login_required
def update_xbento_option(opt_id):
    opt = XbentoOption.query.get_or_404(opt_id)
    opt.name = request.form.get('name', opt.name)
    try:
        opt.price = float(request.form.get('price', opt.price))
    except ValueError:
        pass
    db.session.commit()
    socketio.emit('xbento_options_update', get_xbento_options_dict())
    return redirect(url_for('dashboard'))


@app.route('/dashboard/xbento_options/<int:opt_id>/delete', methods=['POST'])
@login_required
def delete_xbento_option(opt_id):
    opt = XbentoOption.query.get_or_404(opt_id)
    db.session.delete(opt)
    db.session.commit()
    socketio.emit('xbento_options_update', get_xbento_options_dict())
    return redirect(url_for('dashboard'))



# 管理页面
@app.route('/admin')
@login_required
def admin():
    return render_template('admin.html')

@app.route('/admin/orders')
@login_required
def admin_orders():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    order_data = []

    for o in orders:
        try:
            items = json.loads(o.items or "{}")
        except Exception:
            try:
                import ast
                items = ast.literal_eval(o.items)
            except Exception:
                items = {}

        o.created_at_local = to_nl(o.created_at)
        # 不再重新计算 o.totaal，而是使用数据库字段的原值
        order_data.append({
            "order": o,
            "items": items,
            "total": o.totaal or 0,  # 显示数据库值，如果为空则为0
            "totaal": o.totaal or 0,
            "is_completed": o.is_completed,
            "is_cancelled": o.is_cancelled,
        })

    return render_template("admin_orders.html", order_data=order_data)


@app.route('/admin/review-list')
@login_required
def admin_review_list():
    return render_template('admin/review-list.html')
@app.route('/pos/orders_today')
@login_required
def pos_orders_today():
    today = datetime.now(NL_TZ).date()
    start_local = datetime.combine(today, datetime.min.time(), tzinfo=NL_TZ)
    start = start_local.astimezone(UTC).replace(tzinfo=None)

    orders = Order.query.filter(Order.created_at >= start).order_by(Order.created_at.desc()).all()
    order_dicts = []

    for o in orders:
        try:
            o.items_dict = json.loads(o.items or "{}")
        except Exception:
            try:
                import ast
                o.items_dict = ast.literal_eval(o.items)
            except Exception as e:
                print(f"❌ JSON解析失败: {e}")
                o.items_dict = {}

        # ✅ 正确使用数据库中的 totaal
        totaal = o.totaal or 0

        o.created_at_local = to_nl(o.created_at)
        summary = "\n".join(f"{name} x {item['qty']}" for name, item in o.items_dict.items())

        is_pickup = o.order_type in ["afhalen", "pickup"]
        if is_pickup:
            details = f"[Afhalen]\nNaam: {o.customer_name}\nTelefoon: {o.phone}"
            if o.email:
                details += f"\nEmail: {o.email}"
            details += f"\nAfhaaltijd: {o.pickup_time}\nBetaalwijze: {o.payment_method}"
        else:
            details = f"[Bezorgen]\nNaam: {o.customer_name}\nTelefoon: {o.phone}"
            if o.email:
                details += f"\nEmail: {o.email}"
            details += (
                f"\nAdres: {o.street} {o.house_number}"\
                f"\nPostcode: {o.postcode}\nBezorgtijd: {o.delivery_time}"\
                f"\nBetaalwijze: {o.payment_method}"
            )

        o.formatted = (
            f"📦 Nieuwe bestelling bij *Nova Asia*:\n\n"
            f"Bestelnummer: {o.order_number}\n"  # ✅ 插入编号
            f"{summary}\n{details}\nTotaal: €{totaal:.2f}"

        )


        order_dicts.append({
            "id": o.id,
            "order_type": o.order_type,
            "customer_name": o.customer_name,
            "phone": o.phone,
            "email": o.email,
            "payment_method": o.payment_method,
            "pickup_time": o.pickup_time,
            "delivery_time": o.delivery_time,
            "tijdslot_display": o.tijdslot_display,
            "pickupTime": o.pickup_time,
            "deliveryTime": o.delivery_time,
            "postcode": o.postcode,
            "house_number": o.house_number,
            "street": o.street,
            "city": o.city,
            "maps_link": build_maps_link(o.street, o.house_number, o.postcode, o.city),
            "opmerking": o.opmerking,
            "created_date": to_nl(o.created_at).strftime("%Y-%m-%d"),
            "created_at": to_nl(o.created_at).strftime("%H:%M"),
            "items": o.items_dict,
            "total": totaal,   # ✅ 关键是这里：使用数据库中的 totaal
            "totaal": totaal,
            "fooi": o.fooi or 0,
            "order_number": o.order_number,  # ✅ 加上这行
            "is_completed": o.is_completed,
            "is_cancelled": o.is_cancelled
        })

    if request.args.get("json"):
        return jsonify(order_dicts)

    return render_template("pos_orders.html", orders=orders)


@app.route('/pos/orders_by_date')
@login_required
def pos_orders_by_date():
    date_str = request.args.get('date')
    try:
        qdate = datetime.strptime(date_str, '%Y-%m-%d').date()
    except Exception:
        return jsonify([])

    start_local = datetime.combine(qdate, datetime.min.time(), tzinfo=NL_TZ)
    end_local = datetime.combine(qdate, datetime.max.time(), tzinfo=NL_TZ)
    start = start_local.astimezone(UTC).replace(tzinfo=None)
    end = end_local.astimezone(UTC).replace(tzinfo=None)
    orders = Order.query.filter(Order.created_at >= start, Order.created_at <= end).order_by(Order.created_at.desc()).all()
    for o in orders:
        try:
            o.items_dict = json.loads(o.items or '{}')
        except Exception:
            try:
                import ast
                o.items_dict = ast.literal_eval(o.items)
            except Exception:
                o.items_dict = {}
        o.created_at_local = to_nl(o.created_at)
        o.maps_link = build_maps_link(o.street, o.house_number, o.postcode, o.city)

    order_dicts = orders_to_dicts(orders)
    if request.args.get('json'):
        return jsonify(order_dicts)
    return render_template('pos_orders.html', orders=orders)


@app.route('/pos/orders_range')
@login_required
def pos_orders_range():
    start_str = request.args.get('start')
    end_str = request.args.get('end')
    try:
        sdate = datetime.strptime(start_str, '%Y-%m-%d').date()
        edate = datetime.strptime(end_str, '%Y-%m-%d').date()
    except Exception:
        return jsonify([])

    start_local = datetime.combine(sdate, datetime.min.time(), tzinfo=NL_TZ)
    end_local = datetime.combine(edate, datetime.max.time(), tzinfo=NL_TZ)
    start = start_local.astimezone(UTC).replace(tzinfo=None)
    end = end_local.astimezone(UTC).replace(tzinfo=None)
    orders = Order.query.filter(Order.created_at >= start, Order.created_at <= end).order_by(Order.created_at.desc()).all()
    for o in orders:
        try:
            o.items_dict = json.loads(o.items or '{}')
        except Exception:
            try:
                import ast
                o.items_dict = ast.literal_eval(o.items)
            except Exception:
                o.items_dict = {}
        o.created_at_local = to_nl(o.created_at)
        o.maps_link = build_maps_link(o.street, o.house_number, o.postcode, o.city)

    order_dicts = orders_to_dicts(orders)
    if request.args.get('json'):
        return jsonify(order_dicts)
    return render_template('pos_orders.html', orders=orders)
@app.route('/login', methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username == "admin" and password == "novaasia3693":
            login_user(User("admin"))
            return redirect(url_for("pos"))
        return render_template("login.html", error=True)
    return render_template("login.html")

# 登出
@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# 启动
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)























































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































































