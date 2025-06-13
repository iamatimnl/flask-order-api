from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO
import requests
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus

TZ = ZoneInfo("Europe/Amsterdam")






POS_API_URL = "https://nova-asia.onrender.com/api/orders"

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# === Telegram 配置 ===
BOT_TOKEN = '7509433067:AAGoLc1NVWqmgKGcrRVb3DwMh1o5_v5Fyio'
CHAT_ID = '8047420957'

# === Gmail 配置 ===
SENDER_EMAIL = "qianchennl@gmail.com"
SENDER_PASSWORD = "wtuyxljsjwftyzfm"
RECEIVER_EMAIL = "qianchennl@gmail.com"

# === POS 配置 ===
# Endpoint for forwarding orders to the POS system. Replace with the actual URL.
POS_API_URL = "https://nova-asia.onrender.com/api/orders"

TIKKIE_PAYMENT_LINK = "https://tikkie.me/pay/example"

# In-memory log of orders for today's overview
ORDERS = []

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

def send_email_notification(order_text):
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

def send_confirmation_email(order_text, customer_email):
    """Send order confirmation to the customer. Errors are only logged."""
    subject = "Nova Asia - Bevestiging van je bestelling"
    html_body = (
        "Bedankt voor je bestelling bij Nova Asia!<br><br>"
        + order_text.replace("\n", "<br>")
        + "<br><br>Met vriendelijke groet,<br>Nova Asia"
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
        # Failure should not affect order processing
        print(f"❌ Klantbevestiging-fout: {e}")

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


def record_order(order_data, pos_ok):
    ORDERS.append({
        "timestamp": datetime.now(TZ).isoformat(timespec="seconds"),
        "name": order_data.get("name"),
        "items": order_data.get("items"),
        "paymentMethod": order_data.get("paymentMethod"),
        "orderType": order_data.get("orderType"),
        # Use snake_case for time fields when storing orders
        "pickup_time": order_data.get("pickup_time") or order_data.get("pickupTime"),
        "delivery_time": order_data.get("delivery_time") or order_data.get("deliveryTime"),
        "pos_ok": pos_ok,
    })



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
                "pos_ok": entry.get("pos_ok"),
                # Older entries may still use camelCase; support both
                "pickup_time": entry.get("pickup_time") or entry.get("pickupTime"),
                "delivery_time": entry.get("delivery_time") or entry.get("deliveryTime"),
            })
    return overview


@app.route("/api/orders/today", methods=["GET"])
@app.route("/api/orders", methods=["GET"])
def get_orders_today():
    return jsonify(_orders_overview())

@app.route("/api/send", methods=["POST"])
def api_send_order():
    data = request.get_json()
    message = data.get("message", "")
    remark = data.get("remark", "")
    customer_email = data.get("customerEmail") or data.get("email")
    payment_method = data.get("paymentMethod", "").lower()

    order_text = message
    if remark:
        order_text += f"\nOpmerking: {remark}"

    maps_link = build_google_maps_link(data)
    if maps_link:
        order_text += f"\n📍 Google Maps: {maps_link}"

    telegram_ok = send_telegram_message(order_text)
    email_ok = send_email_notification(order_text)
    pos_ok, pos_error = send_pos_order(data)
    record_order(data, pos_ok)

    payment_link = None
    if payment_method and payment_method != "cash":
        payment_link = TIKKIE_PAYMENT_LINK

    if customer_email:
        send_confirmation_email(order_text, customer_email)

    if telegram_ok and email_ok and pos_ok:
        resp = {"status": "ok"}
        if payment_link:
            resp["paymentLink"] = payment_link
        return jsonify(resp), 200

    if not telegram_ok:
        return jsonify({"status": "fail", "error": "Telegram-fout"}), 500
    if not email_ok:
        return jsonify({"status": "fail", "error": "E-mailfout"}), 500
    if not pos_ok:
        return jsonify({"status": "fail", "error": f"POS-fout: {pos_error}"}), 500

    return jsonify({"status": "fail", "error": "Beide mislukt"}), 500

@app.route("/submit_order", methods=["POST"])
def submit_order():
    data = request.get_json()
    message = data.get("message", "")
    remark = data.get("remark", "")
    customer_email = data.get("customerEmail") or data.get("email")
    payment_method = data.get("paymentMethod", "").lower()

    # ✅ 添加 created_at 时间戳，并加入 data 中
    created_at = datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S')
    data["created_at"] = created_at

    order_text = message
    if remark:
        order_text += f"\nOpmerking: {remark}"

    maps_link = build_google_maps_link(data)
    if maps_link:
        order_text += f"\n📍 Google Maps: {maps_link}"

    telegram_ok = send_telegram_message(order_text)
    email_ok = send_email_notification(order_text)
    pos_ok, pos_error = send_pos_order(data)
    record_order(data, pos_ok)

    payment_link = None
    if payment_method and payment_method != "cash":
        payment_link = TIKKIE_PAYMENT_LINK

    if customer_email:
        send_confirmation_email(order_text, customer_email)

    # ✅ 实时推送完整订单数据给前端 POS（包含时间、地址、姓名等）
    socket_order = {
        "message": message,
        "remark": remark,
        "customer_name": data.get("name", ""),
        "order_type": data.get("orderType", ""),
        "created_at": created_at,
        "phone": data.get("phone", ""),
        "email": data.get("email", ""),
        "payment_method": payment_method,
        "items": data.get("items", {}),
        "street": data.get("street", ""),
        "house_number": data.get("houseNumber", ""),
        "postcode": data.get("postcode", ""),
        "city": data.get("city", ""),
        "google_maps_link": maps_link,
        # Emit snake_case keys for frontend templates
        "delivery_time": data.get("delivery_time") or data.get("deliveryTime", ""),
        "pickup_time": data.get("pickup_time") or data.get("pickupTime", "")
    }
    socketio.emit("new_order", socket_order)

    if telegram_ok and email_ok and pos_ok:
        resp = {"status": "ok"}
        if payment_link:
            resp["paymentLink"] = payment_link
        return jsonify(resp), 200

    if not telegram_ok:
        return jsonify({"status": "fail", "error": "Telegram-fout"}), 500
    if not email_ok:
        return jsonify({"status": "fail", "error": "E-mailfout"}), 500
    if not pos_ok:
        return jsonify({"status": "fail", "error": f"POS-fout: {pos_error}"}), 500

    return jsonify({"status": "fail", "error": "Beide mislukt"}), 500

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0")
