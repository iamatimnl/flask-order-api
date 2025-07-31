
from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_cors import CORS
from flask_socketio import SocketIO
import requests
import smtplib
import string
import secrets
import os
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime
import json
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

TZ = ZoneInfo("Europe/Amsterdam")






POS_API_URL = "https://nova-asia.onrender.com/api/orders"
DISCOUNT_API_URL = "https://nova-asia.onrender.com/api/discounts"

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

SETTINGS_FILE = "settings.json"
SETTINGS = {}

def load_settings():
    global SETTINGS
    try:
        with open(SETTINGS_FILE, "r") as f:
            SETTINGS = json.load(f)
        if "bubble_tea_available" not in SETTINGS:
            SETTINGS["bubble_tea_available"] = "true"
    except Exception:
        SETTINGS = {
            "is_open": "true",
            "open_time": "11:00",
            "close_time": "21:00",
            "bubble_tea_available": "true",
        }

def save_settings():
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(SETTINGS, f)
    except Exception as e:
        print(f"Failed to save settings: {e}")

load_settings()

# === Telegram 配置 ===
BOT_TOKEN = '7509433067:AAGoLc1NVWqmgKGcrRVb3DwMh1o5_v5Fyio'
CHAT_ID = '8047420957'
TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# === Email SMTP config ===
SMTP_SERVER = "smtp-relay.brevo.com"
SMTP_PORT = 587
SMTP_USERNAME = "92a3ac003@smtp-brevo.com"
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")  # Already set in Render
FROM_EMAIL = "orders@novaasia.nl"  # Verified sender address
RECEIVER_EMAIL = "qianchennl@gmail.com"

# === POS 配置 ===
# Endpoint for forwarding orders to the POS system. Replace with the actual URL.
POS_API_URL = "https://nova-asia.onrender.com/api/orders"

# === Mollie 配置 ===
MOLLIE_API_KEY = os.environ.get("MOLLIE_API_KEY", "test_E6gVk3tT2Frgdedj9Bcexar82dgUMe")
MOLLIE_REDIRECT_URL = os.environ.get("MOLLIE_REDIRECT_URL", "https://novaasia.nl/payment-success")
MOLLIE_WEBHOOK_URL = os.environ.get(
    "MOLLIE_WEBHOOK_URL",
    "https://flask-order-api.onrender.com/webhook",
)

# In-memory log of orders for today's overview
ORDERS = []

# Keywords for identifying extra items that should be shown in bold
EXTRA_KEYWORDS = ["sojasaus", "stokjes", "gember", "wasabi"]


def sort_items(items):
    """Return items dict sorted with main items first and extras last."""
    sorted_items = {}
    main_items = []
    extra_items = []

    for name, item in items.items():
        if any(keyword.lower() in name.lower() for keyword in EXTRA_KEYWORDS):
            extra_items.append((name, item))
        else:
            main_items.append((name, item))

    for name, item in main_items + extra_items:
        sorted_items[name] = item

    return sorted_items

def build_google_maps_link(data):
    """Return a Google Maps search link for the order address."""
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


def build_socket_order(data, created_date="", created_time="", maps_link=None,
                       discount_code=None, discount_amount=None):
    """Return order data formatted for POS socket events."""

    delivery_time = data.get("delivery_time") or data.get("deliveryTime", "")
    pickup_time = data.get("pickup_time") or data.get("pickupTime", "")
    tijdslot = data.get("tijdslot") or delivery_time or pickup_time
    tijdslot = str(tijdslot).strip()

    # ✅ 修复 ZSM 问题：强制识别为 ZSM 并清空其他时间字段
    if tijdslot.lower() in ["zsm", "asap"]:
        tijdslot = "ZSM"
        tijdslot_display = "ZSM"
        delivery_time = "" if data.get("orderType") == "bezorgen" else delivery_time
        pickup_time = "" if data.get("orderType") != "bezorgen" else pickup_time
    else:
        tijdslot_display = tijdslot


    order = {
        "message": data.get("message", ""),
        "opmerking": data.get("opmerking") or data.get("remark", ""),
        "customer_name": data.get("name", ""),
        "order_type": data.get("orderType", ""),
        "created_at": data.get("created_at"),
        "created_date": created_date,
        "time": created_time,
        "phone": data.get("phone", ""),
        "email": data.get("email", ""),
        "payment_method": (data.get("paymentMethod") or data.get("payment_method", "")).lower(),
        "order_number": data.get("order_number") or data.get("orderNumber"),
        "status": data.get("status"),
        "payment_id": data.get("payment_id"),
        "items": data.get("items", {}),
        "street": data.get("street", ""),
        "house_number": data.get("house_number") or data.get("houseNumber", ""),
        "postcode": data.get("postcode", ""),
        "city": data.get("city", ""),
        "maps_link": maps_link,
        "google_maps_link": maps_link,
        "isNew": True,
        "delivery_time": delivery_time,
        "pickup_time": pickup_time,
        "tijdslot": tijdslot,
        "tijdslot_display": tijdslot_display,
        "subtotal": data.get("subtotal") or (data.get("summary") or {}).get("subtotal"),
        "packaging_fee": data.get("packaging_fee") or (data.get("summary") or {}).get("packaging"),
        "delivery_fee": data.get("delivery_fee") or (data.get("summary") or {}).get("delivery"),
        "bezorgkosten": data.get("bezorgkosten") or data.get("delivery_fee") or (data.get("summary") or {}).get("delivery_cost") or 0,
        "tip": data.get("tip"),
        "btw": data.get("btw") or (data.get("summary") or {}).get("btw"),
        "totaal": data.get("totaal") or (data.get("summary") or {}).get("total"),
        "discount_code": discount_code,
        "discount_amount": discount_amount,
        "discountAmount": data.get("discountAmount"),
        "discountCode": data.get("discountCode"),
    }

    order["items"] = sort_items(order.get("items", {}))
    return order
@app.route('/logout')
def logout():
    return redirect(url_for('dashboard'))

def send_telegram_message(order_text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        'chat_id': CHAT_ID,
        'text': order_text
    }
    try:
        response = requests.post(url, json=data)
        print("✅ Telegram bericht verzonden!")
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Telegram-fout: {e}")
        return False
@app.route('/add_section', methods=['POST'])
def add_section():
    # 暂时什么都不做，直接返回 dashboard
    return redirect(url_for('dashboard'))

def send_email_notification(order_text):
    """Send order details via email (disabled)."""

    # Skip sending email to avoid external side effects.
    return True

def translate_order_text_to_english(order_text_nl: str) -> str:
    translations = {
        "Ordernr": "Order no.",
        "Status": "Status",
        "Naam": "Name",
        "Tel": "Phone",
        "Email": "Email",
        "Type": "Type",
        "Betaling": "Payment",
        "Afhaaltijd": "Pickup time",
        "Bezorgtijd": "Delivery time",
        "Bestelde items": "Ordered items",
        "Subtotaal": "Subtotal",
        "Verpakkingskosten": "Packaging cost",
        "Bezorgkosten": "Delivery fee",
        "Fooi": "Tip",
        "Korting": "Discount",
        "BTW": "VAT",
        "Totaal": "Total",
        "contant": "cash",
        "afhalen": "pickup",
        "bezorgen": "delivery",
        "Z.S.M.": "ASAP",
        "geen": "none"
    }

    translated = order_text_nl
    for nl, en in translations.items():
        translated = translated.replace(nl, en)
    return translated



def send_confirmation_email(order_text, customer_email, order_number, discount_code=None, discount_amount=None):
    """Send bilingual order confirmation email to the customer with review link."""
    review_link = f"https://www.novaasia.nl/review?order={order_number}"
    subject = "Nova Asia - Bevestiging van je bestelling | Order Confirmation"

    # Format korting
    korting_html = ""
    korting_en_html = ""
    if discount_code:
        formatted = f"€{discount_amount:.2f}" if discount_amount is not None else ""
        korting_html = (
            f"<br><br> Je kortingscode: <strong>{discount_code}</strong><br>"
            "Gebruik deze code bij je volgende bestelling!"
        )
        if formatted:
            korting_html += f"<br>Deze code geeft je 3% korting.<br>De verwachte korting op basis van je huidige bestelling is ongeveer {formatted}."

        korting_en_html = (
            f"<br><br>Your discount code: <strong>{discount_code}</strong><br>"
            "Use this code on your next order!"
        )
        if formatted:
            korting_en_html += f"<br>This code gives you a 3% discount.<br>The expected discount based on your current order is about {formatted}."

    # 💬 翻译订单文本
    order_text_nl = order_text.replace("\n", "<br>")
    order_text_en = translate_order_text_to_english(order_text).replace("\n", "<br>")

    # 📧 拼接 HTML 邮件
    html_body = (
        "<strong>Nederlands bovenaan |  English version below</strong><br><br>"
        "<strong>--- Nederlands ---</strong><br><br>"
        "Bedankt voor je bestelling bij Nova Asia!<br><br>"
        + order_text_nl +
        f"<br><br>We horen graag je mening! Laat hier je review achter: <a href='{review_link}' target='_blank'>{review_link}</a>"
        + "<br><br>Met vriendelijke groet,<br>Nova Asia"
        + korting_html +
        "<br><br><hr><br>"
        "<strong>--- English ---</strong><br><br>"
        "Thank you for your order at Nova Asia!<br><br>"
        + order_text_en +
        f"<br><br>We’d love to hear your opinion! Leave your review here: <a href='{review_link}' target='_blank'>{review_link}</a>"
        + "<br><br>Kind regards,<br>Nova Asia"
        + korting_en_html
    )

    # 发送邮件
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr(("NovaAsia", FROM_EMAIL))
    msg["To"] = customer_email

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, [customer_email], msg.as_string())
        print("✅ Bilingual confirmation email sent!")
    except Exception as e:
        print(f"❌ Email send error: {e}")




def send_discount_email(code, customer_email):
    subject = "Nova Asia - Kortingscode | Discount Code"

    body = (
        " Nederlands bovenaan | English version below\n\n"
        "Bedankt voor je bestelling bij Nova Asia!\n\n"
        f"Gebruik deze code voor 3% korting op je volgende bestelling: {code}\n"
        "Voer deze code in bij het afrekenen via onze website.\n\n"
        "Met vriendelijke groet,\nNova Asia\n"
        "----------------------------------------------\n\n"
        " Thank you for your order at Nova Asia!\n\n"
        f"Use this code to get 3% discount on your next order: {code}\n"
        "Apply this code during checkout on our website.\n\n"
        "Kind regards,\nNova Asia"
    )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr(("NovaAsia", FROM_EMAIL))
    msg["To"] = customer_email

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, [customer_email], msg.as_string())
        print("✅ Kortingscode verzonden!")
    except Exception as e:
        print(f"❌ Kortingscode-fout: {e}")



def send_simple_email(subject, body, to_email):
    """Send a plain text email to a specific recipient."""
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr(("NovaAsia", FROM_EMAIL))
    msg["To"] = to_email

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        print("✅ Bevestigingsmail verzonden!")
        return True
    except Exception as e:
        print(f"❌ Bevestigingsmail-fout: {e}")
        return False


def send_telegram_to_customer(phone, text):
    """Attempt to send a Telegram message directly to the customer's phone."""
    if not phone:
        return False

    chat_id = str(phone).replace(" ", "").replace("+", "")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text,
    }
    try:
        response = requests.post(url, json=data)
        return response.status_code == 200
    except Exception as e:
        print(f"❌ Telegram-klantfout: {e}")
        return False



def send_pos_order(order_data):
    """Forward the order data to the POS system."""
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

def create_mollie_payment(order_number, amount):
    """Create a Mollie payment and return the checkout link and payment id."""
    headers = {
        "Authorization": f"Bearer {MOLLIE_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "amount": {"currency": "EUR", "value": f"{amount:.2f}"},
        "description": f"Order {order_number}",
        "redirectUrl": MOLLIE_REDIRECT_URL,
        "webhookUrl": MOLLIE_WEBHOOK_URL,
        "metadata": {"order_id": order_number},
    }
    try:
        resp = requests.post("https://api.mollie.com/v2/payments", headers=headers, json=payload)
        if resp.status_code in (200, 201):
            info = resp.json()
            link = (info.get("_links") or {}).get("checkout", {}).get("href")
            return link, info.get("id")
        print(f"❌ Mollie-response: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"❌ Mollie-fout: {e}")
    return None, None

def generate_discount_code(length=8):
    """Generate a random alphanumeric discount code."""
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def validate_discount_code_api(code, order_total):
    try:
        resp = requests.post(f"{DISCOUNT_API_URL}/validate", json={
            'code': code,
            'order_total': order_total
        })
        if resp.status_code == 200:
            return resp.json()
        return resp.json()
    except Exception as e:
        print(f"❌ Kortingscode check-fout: {e}")
        return {'valid': False, 'error': 'server_error'}


def record_order(order_data, pos_ok):
    """Store a simplified snapshot of the order for today's overview."""
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
        "order_number": order_data.get("order_number") or order_data.get("orderNumber"),
        "status": order_data.get("status", "Pending"),
        "payment_id": order_data.get("payment_id"),
        # Use snake_case for time fields when storing orders
        "pickup_time": pickup_time,
        "delivery_time": delivery_time,
        "pos_ok": pos_ok,
        "totaal": order_data.get("totaal") or (order_data.get("summary") or {}).get("total"),  # ✅ 添加这行
        "discountAmount": order_data.get("discountAmount"),
        "discountCode": order_data.get("discountCode"),
        "full": order_data,
    })


def format_order_notification(data):
    lines = []

    order_number = data.get("order_number") or data.get("orderNumber")
    if order_number:
        lines.append(f"Ordernr: {order_number}")
    status_line = data.get("status")
    if status_line:
        lines.append(f"Status: {status_line}")
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
    tijdslot_display = data.get("tijdslot_display")
    tijdslot = data.get("tijdslot")

    if tijdslot_display:
        if order_type == "bezorgen":
            lines.append(f"Bezorgtijd: {tijdslot_display}")
        else:
            lines.append(f"Afhaaltijd: {tijdslot_display}")
    elif tijdslot and not delivery_time and not pickup_time:
        if order_type == "bezorgen":
            lines.append(f"Bezorgtijd: {tijdslot}")
        else:
            lines.append(f"Afhaaltijd: {tijdslot}")
    else:
        if delivery_time:
            lines.append(f"Bezorgtijd: {delivery_time}")
        if pickup_time:
            lines.append(f"Afhaaltijd: {pickup_time}")

    remark = data.get("opmerking") or data.get("remark")
    if remark:
        lines.append(f"Opmerking: {remark}")

    items = data.get("items", {})
    if items:
        items = sort_items(items)
        lines.append("\nBestelde items:")
        for name, item in items.items():
            qty = item.get("qty", 1)
            price = float(item.get("price") or 0)
            total_price = qty * price
            if any(k.lower() in name.lower() for k in EXTRA_KEYWORDS):
                name_display = name.upper()
            else:
                name_display = name

            if price > 0:
                lines.append(f"{qty} x {name_display} = €{total_price:.2f}")
            else:
                lines.append(f"{qty} x {name_display}")

    summary = data.get("summary") or {}

    def fmt(value):
        try:
            return f"€{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)

    fields = [
        ("Subtotaal", data.get("subtotal") or summary.get("subtotal")),
        ("Verpakkingskosten", data.get("packaging_fee") or summary.get("packaging")),
        ("Bezorgkosten", data.get("delivery_fee") or summary.get("delivery")),
        ("Fooi", data.get("tip")),
    ]

    discount_amount_used = data.get("discountAmount")
    if discount_amount_used is None:
        discount_amount_used = summary.get("discountAmount")
    discount_code_used = data.get("discountCode")

    btw_value = data.get("btw") or summary.get("btw")
    total_value = data.get("totaal") or summary.get("total")

    for label, value in fields:
        if value is not None:
            lines.append(f"{label}: {fmt(value)}")

    if discount_amount_used is not None or discount_code_used:
        amount_str = fmt(discount_amount_used or 0)
        lines.append(f"Korting: -{amount_str} (Code: {discount_code_used or 'geen'})")

    if btw_value is not None:
        lines.append(f"BTW: {fmt(btw_value)}")
    if total_value is not None:
        lines.append(f"Totaal: {fmt(total_value)}")

    return "\n".join(lines)




def _orders_overview():
    """Return a simplified overview of today's orders."""
    today = datetime.now(TZ).date()
    overview = []
    for entry in ORDERS:
        try:
            ts = datetime.fromisoformat(entry.get("timestamp", ""))
        except Exception:
            # Skip malformed timestamps instead of failing
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
                "order_number": entry.get("order_number"),
                "status": entry.get("status"),
                "payment_id": entry.get("payment_id"),
            })
    return overview


@app.route("/api/orders/today", methods=["GET"])
@app.route("/api/orders", methods=["GET"])
def get_orders_today():
    return jsonify(_orders_overview())

@app.route("/api/send", methods=["POST"])
def api_send_order():
    data = request.get_json()

    # 基础字段预处理
    message = data.get("message", "")
    remark = data.get("opmerking") or data.get("remark", "")
    data["opmerking"] = remark
    customer_email = data.get("customerEmail") or data.get("email")
    payment_method = data.get("paymentMethod", "").lower()

    order_text = message or format_order_notification(data)
    maps_link = build_google_maps_link(data)
    if maps_link:
        order_text += f"\n📍 Google Maps: {maps_link}"

    now = datetime.now(TZ)
    created_at = now.strftime('%Y-%m-%d %H:%M:%S')
    created_date = now.strftime('%Y-%m-%d')
    created_time = now.strftime('%H:%M')

    # 总价 & 小费
    data["total"] = data.get("totaal") or (data.get("summary") or {}).get("total")
    data["fooi"] = float(data.get("tip") or 0)
    data["bezorgkosten"] = data.get("delivery_cost") or (data.get("summary") or {}).get("delivery_cost") or 0
    data["created_at"] = created_at
    data["status"] = "Pending"

    # 折扣处理
    discount_code = None
    discount_amount = None
    order_total_val = float(data.get("totaal") or (data.get("summary") or {}).get("total") or 0)
    if customer_email and order_total_val >= 20:
        discount_amount = round(order_total_val * 0.03, 2)
        discount_code = generate_discount_code()
        data["discount_code"] = discount_code
        data["discount_amount"] = discount_amount

    # 处理时间字段
    delivery_time = data.get("delivery_time") or data.get("deliveryTime", "")
    pickup_time = data.get("pickup_time") or data.get("pickupTime", "")
    tijdslot = data.get("tijdslot") or delivery_time or pickup_time

    # ✅ ✅ 修复 ZSM 误判问题（只在明确 ZSM/ASAP/Z.S.M. 时才设置为 ZSM）
    tijdslot = str(tijdslot or "").strip()
    tijdslot_lower = tijdslot.lower()
    if tijdslot_lower in ["zsm", "asap", "z.s.m."]:
        tijdslot = "ZSM"
        tijdslot_display = "ZSM"
    else:
        tijdslot_display = tijdslot

    data["tijdslot"] = tijdslot
    data["tijdslot_display"] = tijdslot_display  # 👈 确保前端 addRow() 正确显示

    # 如果 delivery_time / pickup_time 缺失，从 tijdslot 推导回来
    if not delivery_time and not pickup_time:
        if data.get("orderType") == "bezorgen":
            data["delivery_time"] = tijdslot
        else:
            data["pickup_time"] = tijdslot

    # 支付链接处理（仅在线支付）
    payment_link = None
    if payment_method == "online":
        amount = float(data.get("totaal") or (data.get("summary") or {}).get("total") or 0)
        payment_link, payment_id = create_mollie_payment(
            data.get("order_number") or data.get("orderNumber"),
            amount
        )
        if payment_id:
            data["payment_id"] = payment_id

    # 通知处理
    telegram_ok = True
    email_ok = True
    pos_ok = False
    pos_error = None

    if payment_method != "online":
        telegram_ok = send_telegram_message(order_text)
        email_ok = send_email_notification(order_text)
        pos_ok, pos_error = send_pos_order(data)

    # ✅ 保存到数据库
    record_order(data, pos_ok)

    # 客户确认邮件
    if payment_method != "online" and customer_email:
        order_number = data.get("order_number") or data.get("orderNumber")
        send_confirmation_email(
            order_text, customer_email, order_number,
            discount_code, discount_amount
        )

    # WebSocket 推送到 POS
    if payment_method != "online":
        socket_order = build_socket_order(
            data,
            created_date=created_date,
            created_time=created_time,
            maps_link=maps_link,
            discount_code=discount_code,
            discount_amount=discount_amount,
        )
        socketio.emit("new_order", socket_order)

    # 返回响应
    if payment_method == "online":
        resp = {"status": "ok"}
        if payment_link:
            resp["paymentLink"] = payment_link
        return jsonify(resp), 200

    if telegram_ok and email_ok and pos_ok:
        return jsonify({"status": "ok"}), 200
    if not telegram_ok:
        return jsonify({"status": "fail", "error": "Telegram-fout"}), 500
    if not email_ok:
        return jsonify({"status": "fail", "error": "E-mailfout"}), 500
    if not pos_ok:
        return jsonify({"status": "fail", "error": f"POS-fout: {pos_error}"}), 500

    return jsonify({"status": "fail", "error": "Beide mislukt"}), 500


@app.route('/api/order_time_changed', methods=['POST'])
def order_time_changed():
    data = request.get_json() or {}

    order_number = data.get("order_number", "")
    name = data.get("name", "")
    email = data.get("email", "")
    tijdslot_display = (data.get("tijdslot_display") or "").strip()
    tijdslot = data.get("tijdslot", "").strip()
    display_slot = tijdslot_display or tijdslot
    order_type = (data.get("order_type") or "afhaal").lower()

    if not order_number or not display_slot or not email:
        return jsonify({"status": "fail", "error": "Ontbrekende gegevens"}), 400

    # 类型判断
    if order_type in ["afhaal", "afhalen", "pickup"]:
        nl_context = "Uw afhaaltijd is gewijzigd"
        en_context = "Your pickup time has changed"
    else:
        nl_context = "Uw bezorgtijd is gewijzigd"
        en_context = "Your delivery time has changed"

    subject = f"Nova Asia - {nl_context} voor bestelling #{order_number} | {en_context} for order #{order_number}"

    body = (
        "🇳🇱 Nederlands bovenaan | 🇬🇧 English version below\n\n"
        "----- Nederlands -----\n\n"
        f"Beste {name},\n\n"
        f"{nl_context} naar: {display_slot}.\n"
        "Als u vragen heeft, neem gerust contact met ons op 0622599566.\n\n"
        "Met vriendelijke groet,\n"
        "Team Nova Asia\n\n"
        "-----------------------\n\n"
        "----- English -----\n\n"
        f"Dear {name},\n\n"
        f"{en_context} to: {display_slot}.\n"
        "If you have any questions, feel free to contact us at 0622599566.\n\n"
        "Kind regards,\n"
        "Team Nova Asia"
    )

    send_simple_email(subject, body, email)
    return jsonify({"status": "ok"})


def fetch_order_details(order_number):
    # 从 App A 请求订单详情
    response = requests.get(f"{POS_API_URL}/{order_number}")
    if response.ok:
        return response.json()
    return {}



def send_telegram_to_delivery(
    chat_id, delivery_person, order_number, customer_name, phone, opmerking,
    totaal, payment_method, tijdslot, street, house_number, postcode, city
):
    import requests

    # 构建完整地址
    full_address = f"{street} {house_number}, {postcode} {city}".strip()
    google_maps_url = build_google_maps_link({
        "street": street,
        "house_number": house_number,
        "postcode": postcode,
        "city": city,
    }) or ""

    # 格式化金额
    try:
        bedrag = f"€{float(totaal):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        bedrag = f"€{totaal}"

    # 构建消息内容（Markdown 格式）
    message = (
    f"✈️ Nieuwe bezorging voor {delivery_person}!\n\n"
    f"👤 Klant: {customer_name}\n"
    f"🧾 BN: #{order_number}\n"
    f"📞 Telefoon: {phone or 'Niet opgegeven'}\n"
    f"💬 Opmerking: {opmerking or 'Geen'}\n\n"
    f"🕐 Bezorgen: {tijdslot or 'ZSM'}\n"
    f"💶 Bedrag: {bedrag}\n"
    f"💳 Betaalmethode: {payment_method}\n"
    f"📍 [Adres: {full_address}]({google_maps_url})"
)


    # 发送 Telegram 消息
    requests.post(TELEGRAM_API_URL, json={
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    })
@app.route('/api/order_complete', methods=['POST'])
def order_complete():
    """Handle order completion notifications from the POS system."""
    data = request.get_json() or {}
    order_number = data.get("order_number", "")

    if not order_number:
        return jsonify({"status": "fail", "error": "Ontbrekend ordernummer"}), 400

    # 🔍 拉取完整订单详情（从 App A）
    full_order = fetch_order_details(order_number) or {}

    # 以后端数据为基础，只保留前端提供的送货信息
    delivery_person = data.get("delivery_person")
    delivery_chat_id = data.get("delivery_chat_id") or data.get("chat_id")

    merged = full_order.copy()
    # Fallback to frontend values only if后端没有提供
    for key, val in data.items():
        if key in ["order_number", "delivery_person", "delivery_chat_id", "chat_id"]:
            continue
        if not merged.get(key):
            merged[key] = val

    merged["order_number"] = order_number
    if delivery_person:
        merged["delivery_person"] = delivery_person
    if delivery_chat_id:
        merged["delivery_chat_id"] = delivery_chat_id
        merged["chat_id"] = delivery_chat_id

    data = merged

    # 🎯 公共变量
    name = data.get("name", "")
    email = data.get("email", "")
    order_type = data.get("order_type", "afhaal").lower()
    shop_address = "Sjoukje Dijkstralaan 83, 2134CN Hoofddorp"
    contact_number = "0622599566"

    # 📨 邮件通知内容
    if order_type in ["afhaal", "afhalen", "pickup"]:
        subject = f"Nova Asia - Uw bestelling #{order_number} is klaar | Order ready"
        dutch_message = (
            f"Goed nieuws,<br>"
            f"Uw bestelling is zojuist vers bereid en staat klaar om opgehaald te worden bij:<br><br>"
            f"{shop_address}<br><br>"
            f"Wij hopen dat u volop gaat genieten van uw maaltijd.<br>"
            f"Mocht u vragen hebben, bel ons gerust: {contact_number}.<br><br>"
            f"Bedankt dat u voor Nova Asia heeft gekozen!"
        )
        english_message = (
            f"Good news,<br>"
            f"Your order has just been freshly prepared and is ready for pickup at:<br><br>"
            f"{shop_address}<br><br>"
            f"We hope you enjoy your meal!<br>"
            f"If you have any questions, feel free to call us: {contact_number}.<br><br>"
            f"Thank you for choosing Nova Asia!"
        )
    else:
        subject = f"Nova Asia - Uw bestelling #{order_number} is onderweg | Order on the way"
        dutch_message = (
            f"Goed nieuws,<br>"
            f"Uw bestelling is onderweg naar het door u opgegeven bezorgadres.<br>"
            f"Onze bezorger doet zijn best om op tijd bij u te zijn.<br><br>"
            f"Mocht u vragen hebben, bel ons gerust: {contact_number}.<br><br>"
            f"Wij wensen u alvast smakelijk eten en bedanken u hartelijk voor uw bestelling bij Nova Asia!"
        )
        english_message = (
            f"Good news,<br>"
            f"Your order is on its way to the delivery address you provided.<br>"
            f"Our delivery driver is doing their best to arrive on time.<br><br>"
            f"If you have any questions, feel free to call us: {contact_number}.<br><br>"
            f"We hope you enjoy your meal and sincerely thank you for ordering at Nova Asia!"
        )

        # 📦 Telegram 配送通知
        delivery_person = data.get("delivery_person", "")
        delivery_chat_id = data.get("delivery_chat_id") or data.get("chat_id", "")

        klant_naam = name
        totaal = data.get("totaal", "")
        payment_method = data.get("payment_method", "")
        tijdslot = data.get("tijdslot", "")

        if delivery_chat_id:
            send_telegram_to_delivery(
                chat_id=delivery_chat_id,
                delivery_person=delivery_person,
                customer_name=klant_naam,
                order_number=order_number,
                phone=data.get("phone", ""),
                opmerking=data.get("opmerking", ""),
                totaal=totaal,
                payment_method=payment_method,
                tijdslot=tijdslot,
                street=data.get("street", ""),
                house_number=data.get("house_number", ""),
                postcode=data.get("postcode", ""),
                city=data.get("city", "")
             

            )

    # 📧 邮件通知客户
    if email:
        html_body = (
            "<strong>Nederlands bovenaan |  English version below</strong><br><br>"
            "<strong>--- Nederlands ---</strong><br><br>"
            f"Beste {name},<br><br>"
            f"{dutch_message}<br><br>"
            "<strong>--- English ---</strong><br><br>"
            f"Dear {name},<br><br>"
            f"{english_message}<br><br>"
            "Kind regards,<br>Team Nova Asia"
        )

        msg = MIMEText(html_body, "html", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr(("NovaAsia", FROM_EMAIL))
        msg["To"] = email

        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(FROM_EMAIL, [email], msg.as_string())
            print("✅ Order complete confirmation sent!")
        except Exception as e:
            print(f"❌ Error sending email: {e}")

    return jsonify({"status": "ok"})



@app.route('/api/order_cancelled', methods=['POST'])
def order_cancelled():
    """Handle order cancellation notifications from the POS system."""
    data = request.get_json() or {}
    order_number = data.get("order_number", "")
    name = data.get("name", "")
    email = data.get("email", "")
    order_type = data.get("order_type", "afhaal").lower()

    if not order_number:
        return jsonify({"status": "fail", "error": "Ontbrekend ordernummer"}), 400

    shop_address = "Sjoukje Dijkstralaan 83, 2134CN Hoofddorp"
    contact_number = "0622599566"

    subject = f"Nova Asia - Uw bestelling #{order_number} is geannuleerd | Order Cancelled"

    dutch_message = (
        f"Helaas moeten wij u informeren dat uw bestelling #{order_number} is geannuleerd.<br><br>"
        f"Mocht dit een vergissing zijn of heeft u vragen, neem dan gerust contact met ons op via:<br>"
        f"{contact_number} of kom langs bij:<br>{shop_address}<br><br>"
        f"Onze excuses voor het ongemak en hopelijk tot snel bij Nova Asia."
    )

    english_message = (
        f"We regret to inform you that your order #{order_number} has been cancelled.<br><br>"
        f"If this was a mistake or you have any questions, feel free to contact us at:<br>"
        f"{contact_number} or visit us at:<br>{shop_address}<br><br>"
        f"We apologize for the inconvenience and hope to serve you again soon at Nova Asia."
    )

    if email:
        html_body = (
            "<strong>Nederlands bovenaan | English version below</strong><br><br>"
            "<strong>--- Nederlands ---</strong><br><br>"
            f"Beste {name},<br><br>"
            f"{dutch_message}<br><br>"
            "<strong>--- English ---</strong><br><br>"
            f"Dear {name},<br><br>"
            f"{english_message}<br><br>"
            "Met vriendelijke groet,<br>Team Nova Asia"
        )

        msg = MIMEText(html_body, "html", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr(("NovaAsia", FROM_EMAIL))
        msg["To"] = email

        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(FROM_EMAIL, [email], msg.as_string())
            print("✅ Cancellation email sent successfully!")
        except Exception as e:
            print(f"❌ Error sending cancellation email: {e}")

    return jsonify({"status": "ok"})


@app.route('/validate_discount', methods=['POST'])
def validate_discount_route():
    data = request.get_json() or {}
    code = data.get('code')
    order_total = data.get('order_total')
    result = validate_discount_code_api(code, order_total)
    return jsonify(result)

# ==== Mollie webhook ====
@app.route('/webhook', methods=['POST'])
def mollie_webhook():
    """处理 Mollie 支付状态更新（包含下单确认、通知、折扣码邮件）"""
    payment_id = request.form.get('id')
    if not payment_id:
        return '', 400

    headers = {"Authorization": f"Bearer {MOLLIE_API_KEY}"}
    resp = requests.get(f"https://api.mollie.com/v2/payments/{payment_id}", headers=headers)
    if resp.status_code != 200:
        return '', 400

    info = resp.json()
    if info.get('status') == 'paid':
        order_id = (info.get('metadata') or {}).get('order_id')
        order_entry = None
        for o in ORDERS:
            if o.get('order_number') == order_id:
                o['status'] = 'Paid'
                o['paymentMethod'] = 'Online betaald'
                order_entry = o
                break

        if order_entry:
            order_data = order_entry.get('full', order_entry).copy()
            order_data['status'] = 'Paid'
            order_data['paymentMethod'] = 'Online betaald'

            pos_ok, _ = send_pos_order(order_data)
            if pos_ok:
                # ✅ 查询 POS，确认订单已入库
                check = requests.get(f"{POS_API_URL}/{order_id}")
                if check.status_code == 200:
                    # ✅ 格式化订单通知
                    text = format_order_notification(order_data)
                    maps_link = build_google_maps_link(order_data)
                    if maps_link:
                        text += f"\n📍 Google Maps: {maps_link}"

                    # ✅ 新增：折扣码提醒
                    kortingscode = order_data.get('discount_code') or order_data.get('discountCode')
                    kortingsbedrag = float(order_data.get('discount_amount') or 0)

                    if kortingscode:
                        text += (
                            f"\n\nJe kortingscode: {kortingscode}"
                            f"\nGebruik deze code bij je volgende bestelling!"
                            f"\nDeze code geeft je 3% korting."
                            f"\nDe verwachte korting op basis van je huidige bestelling is ongeveer €{kortingsbedrag:.2f}"
                        )

                    # ✅ 发送通知
                    send_telegram_message(text)
                    send_email_notification(text)

                    cust_email = order_data.get('customerEmail') or order_data.get('email')
                    if cust_email:
                        send_confirmation_email(text, cust_email, order_id)

                    order_data['items'] = sort_items(order_data.get('items', {}))
                    created_date = ""
                    created_time = ""
                    ts = order_data.get('created_at', '')
                    if ts:
                        if 'T' in ts:
                            try:
                                dt = datetime.fromisoformat(ts)
                                created_date = dt.strftime('%Y-%m-%d')
                                created_time = dt.strftime('%H:%M')
                            except Exception:
                                pass
                        elif ' ' in ts:
                            parts = ts.split(' ')
                            if len(parts) >= 2:
                                created_date = parts[0]
                                created_time = parts[1][:5]
                    socket_order = build_socket_order(
                        order_data,
                        created_date=created_date,
                        created_time=created_time,
                        maps_link=maps_link,
                    )
                    socketio.emit('new_order', socket_order)
                else:
                    print(f"❌ Order {order_id} niet gevonden in POS API!")
            else:
                print(f"❌ POS-fout bij webhook voor bestelling {order_id}")
        else:
            print(f"❌ Order {order_id} niet gevonden in lokale cache!")

    return '', 200


@app.route('/payment_success')
def payment_success():
    return render_template('payment_success.html')

# ==== Settings API ====

@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Return all settings."""
    return jsonify(SETTINGS)


@app.route('/api/settings/<key>', methods=['GET', 'POST'])
def setting_detail(key):
    if request.method == 'GET':
        return jsonify({key: SETTINGS.get(key)})

    data = request.get_json() or {}
    value = data.get('value')
    if value is not None:
        SETTINGS[key] = value
        save_settings()
        socketio.emit('setting_update', {'key': key, 'value': value})
        return jsonify({'status': 'ok', key: value})
    return jsonify({'status': 'fail', 'error': 'no value'}), 400


@app.route('/dashboard', methods=['GET'])
def dashboard():
    return render_template(
        'dashboard.html',
        is_open=SETTINGS.get('is_open', 'true'),
        open_time=SETTINGS.get('open_time', '11:00'),
        close_time=SETTINGS.get('close_time', '21:00'),
        bubble_tea_available=SETTINGS.get('bubble_tea_available', 'true'),
    )


@app.route('/update_setting', methods=['POST'])
def update_setting():
    changed = {}
    is_open = request.form.get('is_open')
    open_time = request.form.get('open_time')
    close_time = request.form.get('close_time')
    bubble_tea_available = request.form.get('bubble_tea_available')
    if is_open is not None:
        SETTINGS['is_open'] = is_open
        changed['is_open'] = is_open
    if open_time:
        SETTINGS['open_time'] = open_time
        changed['open_time'] = open_time
    if close_time:
        SETTINGS['close_time'] = close_time
        changed['close_time'] = close_time
    if bubble_tea_available is not None:
        SETTINGS['bubble_tea_available'] = bubble_tea_available
        changed['bubble_tea_available'] = bubble_tea_available
    if changed:
        save_settings()
        for k, v in changed.items():
            socketio.emit('setting_update', {'key': k, 'value': v})
    return redirect(url_for('dashboard'))

@app.route("/submit_order", methods=["POST"])
def submit_order():
    data = request.get_json()
    message = data.get("message", "")
    remark = data.get("opmerking") or data.get("remark", "")
    data["opmerking"] = remark
    customer_email = data.get("customerEmail") or data.get("email")
    payment_method = data.get("paymentMethod", "").lower()

    now = datetime.now(TZ)
    created_at = now.strftime('%Y-%m-%d %H:%M:%S')
    created_date = now.strftime('%Y-%m-%d')
    created_time = now.strftime('%H:%M')
    data["total"] = data.get("totaal") or (data.get("summary") or {}).get("total")
    data["fooi"] = float(data.get("tip") or 0)
    data["created_at"] = created_at
    data["status"] = "Pending"

    order_text = format_order_notification(data)
    maps_link = build_google_maps_link(data)
    if maps_link:
        order_text += f"\n📍 Google Maps: {maps_link}"

    discount_code = None
    discount_amount = None
    order_total_val = float(data.get("totaal") or (data.get("summary") or {}).get("total") or 0)
    if customer_email and order_total_val >= 20:
        discount_amount = round(order_total_val * 0.03, 2)
        discount_code = generate_discount_code()
        data["discount_code"] = discount_code
        data["discount_amount"] = discount_amount

    payment_link = None
    if payment_method == "online":
        # ✅ 仅创建支付链接，立即返回，不通知
        amount = float(data.get("totaal") or (data.get("summary") or {}).get("total") or 0)
        payment_link, payment_id = create_mollie_payment(data.get("order_number") or data.get("orderNumber"), amount)
        if payment_id:
            data["payment_id"] = payment_id

        record_order(data, False)  # 记录订单，状态 Pending，尚未支付

        resp = {"status": "ok"}
        if payment_link:
            resp["paymentLink"] = payment_link
        return jsonify(resp), 200

    # ✅ 非 online betaling，立即通知 POS、Telegram、Email、socketio
    telegram_ok = send_telegram_message(order_text)
    email_ok = send_email_notification(order_text)
    pos_ok, pos_error = send_pos_order(data)

    record_order(data, pos_ok)

    if customer_email:
        order_number = data.get("order_number") or data.get("orderNumber")
        send_confirmation_email(order_text, customer_email, order_number, discount_code, discount_amount)

    delivery_time = data.get("delivery_time") or data.get("deliveryTime", "")
    pickup_time = data.get("pickup_time") or data.get("pickupTime", "")
    tijdslot = data.get("tijdslot") or delivery_time or pickup_time

    if tijdslot:
        if not delivery_time and not pickup_time:
            if data.get("orderType") == "bezorgen":
                delivery_time = tijdslot
            else:
                pickup_time = tijdslot

    socket_order = build_socket_order(
        data,
        created_date=created_date,
        created_time=created_time,
        maps_link=maps_link,
        discount_code=discount_code,
        discount_amount=discount_amount,
    )
    socketio.emit("new_order", socket_order)

    if telegram_ok and email_ok and pos_ok:
        return jsonify({"status": "ok"}), 200

    if not telegram_ok:
        return jsonify({"status": "fail", "error": "Telegram-fout"}), 500
    if not email_ok:
        return jsonify({"status": "fail", "error": "E-mailfout"}), 500
    if not pos_ok:
        return jsonify({"status": "fail", "error": f"POS-fout: {pos_error}"}), 500

    return jsonify({"status": "fail", "error": "Beide mislukt"}), 500


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0")
