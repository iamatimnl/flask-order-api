# Combined Flask application for Nova Asia
import os
import json
import random
import string
import smtplib
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import quote_plus, quote

import pandas as pd
import requests
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    redirect,
    url_for,
    send_file,
)
from flask_cors import CORS
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required,
)
from flask_migrate import Migrate
from sqlalchemy import text
from zoneinfo import ZoneInfo
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
)
import eventlet
from flask import Flask, render_template

eventlet.monkey_patch()

TZ = ZoneInfo("Europe/Amsterdam")

# === Configuration ===
POS_API_URL = "https://nova-asia.onrender.com/api/orders"
TIKKIE_PAYMENT_LINK = "https://tikkie.me/pay/example"

BOT_TOKEN = '7509433067:AAGoLc1NVWqmgKGcrRVb3DwMh1o5_v5Fyio'
CHAT_ID = '8047420957'
SENDER_EMAIL = "qianchennl@gmail.com"
SENDER_PASSWORD = "wtuyxljsjwftyzfm"
RECEIVER_EMAIL = "qianchennl@gmail.com"

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

app.config["SECRET_KEY"] = "replace-this"
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")

# === Database setup ===
db = SQLAlchemy(app)
migrate = Migrate(app, db)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/menu')
def menu():
    return render_template('menu.html')



@app.route('/login')
def login():
    return render_template('login.html')


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
    payment_method = db.Column(db.String(20))
    postcode = db.Column(db.String(10))
    house_number = db.Column(db.String(10))
    street = db.Column(db.String(100))
    city = db.Column(db.String(100))
    opmerking = db.Column(db.Text)
    items = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    totaal = db.Column(db.Float)

class User(UserMixin):
    def __init__(self, user_id: str):
        self.id = user_id

login_manager = LoginManager(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id: str):
    return User("admin") if user_id == "admin" else None

with app.app_context():
    db.create_all()
    try:
        inspector = db.inspect(db.engine)
        cols = {c["name"] for c in inspector.get_columns("orders")}
        if "opmerking" not in cols:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE orders ADD COLUMN opmerking TEXT"))
    except Exception as e:
        print(f"DB init error: {e}")

# === In-memory overview ===
ORDERS = []

# === Utility functions ===
def build_google_maps_link(data):
    street = data.get("street", "").strip()
    house_number = data.get("houseNumber") or data.get("house_number", "")
    postcode = data.get("postcode", "").strip()
    city = data.get("city", "").strip()
    if street:
        first_part = f"{street} {house_number}".strip()
    else:
        first_part = house_number
    second_part = " ".join(part for part in [postcode, city] if part).strip()
    address_parts = [part for part in [first_part, second_part] if part]
    if not address_parts:
        return None
    address = ", ".join(address_parts)
    query = quote_plus(address)
    return f"https://www.google.com/maps/search/?api=1&query={query}"

def to_nl(dt: datetime) -> datetime:
    if dt is None:
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)

def generate_order_number(length=8):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def send_telegram_message(order_text: str) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {'chat_id': CHAT_ID, 'text': order_text}
    try:
        response = requests.post(url, json=data)
        print("✅ Telegram bericht verzonden!")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Telegram-fout: {e}")
        return False

def send_email_notification(order_text: str) -> bool:
    subject = "Nova Asia - Nieuwe bestelling"
    msg = MIMEText(order_text, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr(("NovaAsia", SENDER_EMAIL))
    msg["To"] = RECEIVER_EMAIL
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, [RECEIVER_EMAIL], msg.as_string())
        print("✅ E-mail verzonden!")
        return True
    except Exception as e:
        print(f"❌ Verzendfout: {e}")
        return False

def send_confirmation_email(order_text: str, customer_email: str) -> None:
    subject = "Nova Asia - Bevestiging van je bestelling"
    html_body = (
        "Bedankt voor je bestelling bij Nova Asia!<br><br>" +
        order_text.replace("\n", "<br>") +
        "<br><br>Met vriendelijke groet,<br>Nova Asia"
    )
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr(("NovaAsia", SENDER_EMAIL))
    msg["To"] = customer_email
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, [customer_email], msg.as_string())
        print("✅ Klantbevestiging verzonden!")
    except Exception as e:
        print(f"❌ Klantbevestiging-fout: {e}")

def send_pos_order(order_data):
    try:
        response = requests.post(POS_API_URL, json=order_data)
        if response.status_code == 200:
            print("✅ POS-bestelling verzonden!")
            return True, None
        print(f"❌ POS-response: {response.status_code} {response.text}")
        return False, f"status {response.status_code}"
    except Exception as e:
        print(f"❌ POS-fout: {e}")
        return False, str(e)

def record_order(order_data, pos_ok):
    pickup_time = order_data.get("pickup_time") or order_data.get("pickupTime")
    delivery_time = order_data.get("delivery_time") or order_data.get("deliveryTime")
    if not pickup_time and not delivery_time:
        tijdslot = order_data.get("tijdslot")
        if tijdslot:
            if order_data.get("orderType") == "bezorgen":
                delivery_time = tijdslot
            else:
                pickup_time = tijdslot
    ORDERS.append({
        "timestamp": datetime.now(TZ).isoformat(timespec="seconds"),
        "name": order_data.get("name"),
        "items": order_data.get("items"),
        "paymentMethod": order_data.get("paymentMethod"),
        "orderType": order_data.get("orderType"),
        "opmerking": order_data.get("opmerking") or order_data.get("remark"),
        "pickup_time": pickup_time,
        "delivery_time": delivery_time,
        "pos_ok": pos_ok,
        "totaal": order_data.get("totaal") or (order_data.get("summary") or {}).get("total")
    })

def format_order_notification(data):
    lines = []
    order_number = data.get("order_number")
    if order_number:
        lines.append(f"Bestelnummer: {order_number}")
    name = data.get("name")
    if name:
        lines.append(f"Naam: {name}")
    phone = data.get("phone")
    if phone:
        lines.append(f"Tel: {phone}")
    email = data.get("email") or data.get("customerEmail")
    if email:
        lines.append(f"Email: {email}")
    order_type = data.get("orderType")
    if order_type:
        lines.append(f"Type: {order_type}")
    if order_type == "bezorgen":
        addr_parts = [
            data.get("street"),
            data.get("house_number") or data.get("houseNumber"),
            data.get("postcode"),
            data.get("city"),
        ]
        addr = " ".join(str(p) for p in addr_parts if p)
        if addr:
            lines.append(f"Adres: {addr}")
    payment_method = data.get("payment_method") or data.get("paymentMethod")
    if payment_method:
        lines.append(f"Betaling: {payment_method}")
    delivery_time = data.get("delivery_time") or data.get("deliveryTime")
    pickup_time = data.get("pickup_time") or data.get("pickupTime")
    tijdslot = data.get("tijdslot")
    if tijdslot and not delivery_time and not pickup_time:
        if order_type == "bezorgen":
            lines.append(f"Bezorgtijd: {tijdslot}")
        else:
            lines.append(f"Afhaaltijd: {tijdslot}")
    else:
        if delivery_time:
            lines.append(f"Bezorgtijd: {delivery_time}")
        if pickup_time:
            lines.append(f"Afhaaltijd: {pickup_time}")
    message = data.get("message")
    if message:
        lines.append(message)
    remark = data.get("opmerking") or data.get("remark")
    if remark and (not message or f"Opmerking: {remark}" not in message):
        lines.append(f"Opmerking: {remark}")
    summary = data.get("summary") or {}
    def fmt(value):
        try:
            return f"€{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)
    subtotal = data.get("subtotal")
    if subtotal is None:
        subtotal = summary.get("subtotal")
    if subtotal is not None:
        lines.append(f"Subtotaal: {fmt(subtotal)}")
    packaging_fee = data.get("packaging_fee")
    if packaging_fee is None:
        packaging_fee = summary.get("packaging")
    if packaging_fee:
        lines.append(f"Verpakkingskosten: {fmt(packaging_fee)}")
    delivery_fee = data.get("delivery_fee")
    if delivery_fee is None:
        delivery_fee = summary.get("delivery")
    if delivery_fee:
        lines.append(f"Bezorgkosten: {fmt(delivery_fee)}")
    tip = data.get("tip")
    if tip:
        lines.append(f"Fooi: {fmt(tip)}")
    discount_amount = summary.get("discountAmount")
    if discount_amount:
        lines.append(f"Korting: -{fmt(discount_amount)}")
    btw_amount = data.get("btw")
    if btw_amount is None:
        btw_amount = summary.get("btw")
    if btw_amount is not None:
        lines.append(f"BTW: {fmt(btw_amount)}")
    total = data.get("totaal")
    if total is None:
        total = summary.get("total")
    if total is not None:
        lines.append(f"Totaal: {fmt(total)}")
    return "\n".join(lines)

def _orders_overview():
    today = datetime.now(TZ).date()
    overview = []
    for entry in ORDERS:
        try:
            ts = datetime.fromisoformat(entry.get("timestamp", ""))
        except Exception:
            continue
        if ts.date() == today:
            overview.append({
                "time": ts.strftime("%H:%M"),
                "customerName": entry.get("name"),
                "items": entry.get("items"),
                "paymentMethod": entry.get("paymentMethod"),
                "orderType": entry.get("orderType"),
                "opmerking": entry.get("opmerking") or entry.get("remark"),
                "pos_ok": entry.get("pos_ok"),
                "totaal": entry.get("totaal"),
                "pickup_time": entry.get("pickup_time") or entry.get("pickupTime"),
                "delivery_time": entry.get("delivery_time") or entry.get("deliveryTime"),
            })
    return overview

# === PDF/Excel helpers ===
def generate_excel_today():
    today = datetime.now(TZ).date()
    start_local = datetime.combine(today, datetime.min.time(), tzinfo=TZ)
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    orders = Order.query.filter(Order.created_at >= start).order_by(Order.created_at.desc()).all()
    data = []
    for o in orders:
        try:
            items = json.loads(o.items or "{}")
        except Exception:
            items = {}
        summary = ", ".join(f"{k} x {v.get('qty')}" for k, v in items.items())
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
        })
    df = pd.DataFrame(data)
    output = BytesIO()
    df.to_excel(output, index=False, engine='xlsxwriter')
    output.seek(0)
    return output

def generate_pdf_today():
    today = datetime.now(TZ).date()
    start_local = datetime.combine(today, datetime.min.time(), tzinfo=TZ)
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    orders = Order.query.filter(Order.created_at >= start).order_by(Order.created_at.desc()).all()
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    data = [["Datum", "Tijd", "Naam", "Totaal", "Items"]]
    for o in orders:
        try:
            items = json.loads(o.items or "{}")
        except Exception:
            items = {}
        summary = ", ".join(f"{k} x {v.get('qty')}" for k, v in items.items())
        data.append([
            to_nl(o.created_at).strftime("%Y-%m-%d"),
            to_nl(o.created_at).strftime("%H:%M"),
            o.customer_name,
            f"€{o.totaal:.2f}",
            summary,
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
    doc.build([table])
    buffer.seek(0)
    return buffer

# === Routes ===
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/orders/today', methods=['GET'])
@app.route('/api/orders', methods=['GET'])
def get_orders_today():
    return jsonify(_orders_overview())

@app.route('/api/orders', methods=['POST'])
def create_order():
    return submit_order()

@app.route('/api/send', methods=['POST'])
def api_send_order():
    data = request.get_json() or {}
    message = data.get('message', '')
    remark = data.get('opmerking') or data.get('remark', '')
    data['opmerking'] = remark
    customer_email = data.get('customerEmail') or data.get('email')
    payment_method = data.get('paymentMethod', '').lower()

    order_text = format_order_notification(data)
    maps_link = build_google_maps_link(data)
    if maps_link:
        order_text += f"\n📍 Google Maps: {maps_link}"

    now = datetime.now(TZ)
    created_at = now.strftime('%Y-%m-%d %H:%M:%S')
    created_date = now.strftime('%Y-%m-%d')
    created_time = now.strftime('%H:%M')
    data['total'] = data.get('totaal') or (data.get('summary') or {}).get('total')
    data['created_at'] = created_at

    telegram_ok = send_telegram_message(order_text)
    email_ok = send_email_notification(order_text)
    pos_ok, pos_error = send_pos_order(data)
    record_order(data, pos_ok)

    # --- persist to DB ---
    try:
        order = Order(
            order_number=data.get('order_number') or generate_order_number(),
            order_type=data.get('orderType') or data.get('order_type'),
            customer_name=data.get('name') or data.get('customer_name'),
            phone=data.get('phone'),
            email=data.get('customerEmail') or data.get('email'),
            pickup_time=data.get('pickup_time') or data.get('pickupTime'),
            delivery_time=data.get('delivery_time') or data.get('deliveryTime'),
            payment_method=payment_method,
            postcode=data.get('postcode'),
            house_number=data.get('houseNumber') or data.get('house_number'),
            street=data.get('street'),
            city=data.get('city'),
            opmerking=remark,
            items=json.dumps(data.get('items', {})),
            totaal=data.get('totaal') or (data.get('summary') or {}).get('total'),
        )
        db.session.add(order)
        db.session.commit()
    except Exception as e:
        print(f"DB error: {e}")

    payment_link = None
    if payment_method and payment_method != 'cash':
        payment_link = TIKKIE_PAYMENT_LINK

    if customer_email:
        send_confirmation_email(order_text, customer_email)

    delivery_time = data.get('delivery_time') or data.get('deliveryTime', '')
    pickup_time = data.get('pickup_time') or data.get('pickupTime', '')
    tijdslot = data.get('tijdslot') or delivery_time or pickup_time
    if tijdslot:
        if not delivery_time and not pickup_time:
            if data.get('orderType') == 'bezorgen':
                delivery_time = tijdslot
            else:
                pickup_time = tijdslot

    socket_order = {
        'message': message,
        'opmerking': remark,
        'customer_name': data.get('name', ''),
        'order_type': data.get('orderType', ''),
        'created_at': data['created_at'],
        'created_date': created_date,
        'time': created_time,
        'phone': data.get('phone', ''),
        'email': data.get('email', ''),
        'payment_method': payment_method,
        'items': data.get('items', {}),
        'street': data.get('street', ''),
        'house_number': data.get('houseNumber', ''),
        'postcode': data.get('postcode', ''),
        'city': data.get('city', ''),
        'maps_link': maps_link,
        'google_maps_link': maps_link,
        'delivery_time': delivery_time,
        'pickup_time': pickup_time,
        'tijdslot': tijdslot,
        'subtotal': data.get('subtotal') or (data.get('summary') or {}).get('subtotal'),
        'packaging_fee': data.get('packaging_fee') or (data.get('summary') or {}).get('packaging'),
        'delivery_fee': data.get('delivery_fee') or (data.get('summary') or {}).get('delivery'),
        'tip': data.get('tip'),
        'btw': data.get('btw') or (data.get('summary') or {}).get('btw'),
        'totaal': data.get('totaal') or (data.get('summary') or {}).get('total'),
        'discount_amount': (data.get('summary') or {}).get('discountAmount'),
    }
    socketio.emit('new_order', socket_order)

    if telegram_ok and email_ok and pos_ok:
        resp = {'status': 'ok'}
        if payment_link:
            resp['paymentLink'] = payment_link
        return jsonify(resp), 200
    if not telegram_ok:
        return jsonify({'status': 'fail', 'error': 'Telegram-fout'}), 500
    if not email_ok:
        return jsonify({'status': 'fail', 'error': 'E-mailfout'}), 500
    if not pos_ok:
        return jsonify({'status': 'fail', 'error': f'POS-fout: {pos_error}'}), 500
    return jsonify({'status': 'fail', 'error': 'Beide mislukt'}), 500

@app.route('/submit_order', methods=['POST'])
def submit_order():
    data = request.get_json() or {}
    message = data.get('message', '')
    remark = data.get('opmerking') or data.get('remark', '')
    data['opmerking'] = remark
    customer_email = data.get('customerEmail') or data.get('email')
    payment_method = data.get('paymentMethod', '').lower()

    now = datetime.now(TZ)
    created_at = now.strftime('%Y-%m-%d %H:%M:%S')
    created_date = now.strftime('%Y-%m-%d')
    created_time = now.strftime('%H:%M')
    data['total'] = data.get('totaal') or (data.get('summary') or {}).get('total')
    data['created_at'] = created_at

    order_text = format_order_notification(data)
    maps_link = build_google_maps_link(data)
    if maps_link:
        order_text += f"\n📍 Google Maps: {maps_link}"

    telegram_ok = send_telegram_message(order_text)
    email_ok = send_email_notification(order_text)
    pos_ok, pos_error = send_pos_order(data)
    record_order(data, pos_ok)

    try:
        order = Order(
            order_number=data.get('order_number') or generate_order_number(),
            order_type=data.get('orderType') or data.get('order_type'),
            customer_name=data.get('name') or data.get('customer_name'),
            phone=data.get('phone'),
            email=data.get('customerEmail') or data.get('email'),
            pickup_time=data.get('pickup_time') or data.get('pickupTime'),
            delivery_time=data.get('delivery_time') or data.get('deliveryTime'),
            payment_method=payment_method,
            postcode=data.get('postcode'),
            house_number=data.get('houseNumber') or data.get('house_number'),
            street=data.get('street'),
            city=data.get('city'),
            opmerking=remark,
            items=json.dumps(data.get('items', {})),
            totaal=data.get('totaal') or (data.get('summary') or {}).get('total'),
        )
        db.session.add(order)
        db.session.commit()
    except Exception as e:
        print(f"DB error: {e}")

    payment_link = None
    if payment_method and payment_method != 'cash':
        payment_link = TIKKIE_PAYMENT_LINK

    if customer_email:
        send_confirmation_email(order_text, customer_email)

    delivery_time = data.get('delivery_time') or data.get('deliveryTime', '')
    pickup_time = data.get('pickup_time') or data.get('pickupTime', '')
    tijdslot = data.get('tijdslot') or delivery_time or pickup_time
    if tijdslot:
        if not delivery_time and not pickup_time:
            if data.get('orderType') == 'bezorgen':
                delivery_time = tijdslot
            else:
                pickup_time = tijdslot

    socket_order = {
        'message': message,
        'opmerking': remark,
        'customer_name': data.get('name', ''),
        'order_type': data.get('orderType', ''),
        'created_at': data['created_at'],
        'created_date': created_date,
        'time': created_time,
        'phone': data.get('phone', ''),
        'email': data.get('email', ''),
        'payment_method': payment_method,
        'items': data.get('items', {}),
        'street': data.get('street', ''),
        'house_number': data.get('houseNumber', ''),
        'postcode': data.get('postcode', ''),
        'city': data.get('city', ''),
        'maps_link': maps_link,
        'google_maps_link': maps_link,
        'delivery_time': delivery_time,
        'pickup_time': pickup_time,
        'tijdslot': tijdslot,
        'subtotal': data.get('subtotal') or (data.get('summary') or {}).get('subtotal'),
        'packaging_fee': data.get('packaging_fee') or (data.get('summary') or {}).get('packaging'),
        'delivery_fee': data.get('delivery_fee') or (data.get('summary') or {}).get('delivery'),
        'tip': data.get('tip'),
        'btw': data.get('btw') or (data.get('summary') or {}).get('btw'),
        'totaal': data.get('totaal') or (data.get('summary') or {}).get('total'),
        'discount_amount': (data.get('summary') or {}).get('discountAmount'),
    }
    socketio.emit('new_order', socket_order)

    if telegram_ok and email_ok and pos_ok:
        resp = {'status': 'ok'}
        if payment_link:
            resp['paymentLink'] = payment_link
        return jsonify(resp), 200
    if not telegram_ok:
        return jsonify({'status': 'fail', 'error': 'Telegram-fout'}), 500
    if not email_ok:
        return jsonify({'status': 'fail', 'error': 'E-mailfout'}), 500
    if not pos_ok:
        return jsonify({'status': 'fail', 'error': f'POS-fout: {pos_error}'}), 500
    return jsonify({'status': 'fail', 'error': 'Beide mislukt'}), 500

@app.route('/create_db')
def create_db():
    try:
        inspector = db.inspect(db.engine)
        cols = {c['name'] for c in inspector.get_columns('orders')}
        if 'opmerking' not in cols:
            with db.engine.begin() as conn:
                conn.execute(db.text('ALTER TABLE orders ADD COLUMN opmerking TEXT'))
        db.create_all()
        return '✅ Database tables created!'
    except Exception as e:
        return f'❌ Error: {e}'

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
            items = json.loads(o.items or '{}')
        except Exception:
            try:
                import ast
                items = ast.literal_eval(o.items)
            except Exception:
                items = {}
        o.created_at_local = to_nl(o.created_at)
        order_data.append({
            'order': o,
            'items': items,
            'total': o.totaal or 0,
            'totaal': o.totaal or 0,
        })
    return render_template('admin_orders.html', order_data=order_data)

@app.route('/admin/orders/download/pdf')
@login_required
def download_pdf():
    output = generate_pdf_today()
    return send_file(output, mimetype='application/pdf', as_attachment=True, download_name='bestellingen_vandaag.pdf')

@app.route('/admin/orders/download/excel')
@login_required
def download_excel():
    output = generate_excel_today()
    return send_file(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name='bestellingen_vandaag.xlsx')

@app.route('/pos', methods=['GET', 'POST'])
@login_required
def pos():
    if request.method == 'POST':
        return submit_order()
    today = datetime.now(TZ).date()
    start_local = datetime.combine(today, datetime.min.time(), tzinfo=TZ)
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    orders = Order.query.filter(Order.created_at >= start).order_by(Order.created_at.desc()).all()
    for o in orders:
        try:
            o.items_dict = json.loads(o.items or '{}')
        except Exception:
            try:
                import ast
                o.items_dict = ast.literal_eval(o.items)
            except Exception:
                o.items_dict = {}
        o.total = sum(float(i.get('price', 0)) * int(i.get('qty', 0)) for i in o.items_dict.values())
        o.created_at_local = to_nl(o.created_at)
        o.maps_link = build_google_maps_link({
            'street': o.street,
            'house_number': o.house_number,
            'postcode': o.postcode,
            'city': o.city,
        })
    return render_template('pos.html', orders=orders)

@app.route('/pos/orders_today')
@login_required
def pos_orders_today():
    today = datetime.now(TZ).date()
    start_local = datetime.combine(today, datetime.min.time(), tzinfo=TZ)
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    orders = Order.query.filter(Order.created_at >= start).order_by(Order.created_at.desc()).all()
    order_dicts = []
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
        o.created_at_local = to_nl(o.created_at)
        order_dicts.append({
            'id': o.id,
            'order_type': o.order_type,
            'customer_name': o.customer_name,
            'phone': o.phone,
            'email': o.email,
            'payment_method': o.payment_method,
            'pickup_time': o.pickup_time,
            'delivery_time': o.delivery_time,
            'pickupTime': o.pickup_time,
            'deliveryTime': o.delivery_time,
            'postcode': o.postcode,
            'house_number': o.house_number,
            'street': o.street,
            'city': o.city,
            'maps_link': build_google_maps_link({
                'street': o.street,
                'house_number': o.house_number,
                'postcode': o.postcode,
                'city': o.city,
            }),
            'opmerking': o.opmerking,
            'created_date': to_nl(o.created_at).strftime('%Y-%m-%d'),
            'created_at': to_nl(o.created_at).strftime('%H:%M'),
            'items': o.items_dict,
            'total': totaal,
            'totaal': totaal,
            'order_number': o.order_number,
        })
    if request.args.get('json'):
        return jsonify(order_dicts)
    return render_template('pos_orders.html', orders=orders)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'admin' and password == 'novaasia3693':
            login_user(User('admin'))
            return redirect(url_for('pos'))
        return render_template('login.html', error=True)
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
