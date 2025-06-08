
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO
import requests
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr

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

@app.route("/api/send", methods=["POST"])
def api_send_order():
    data = request.get_json()
    message = data.get("message", "")

    telegram_ok = send_telegram_message(message)
    email_ok = send_email_notification(message)

    if telegram_ok and email_ok:
        return jsonify({"status": "ok"})
    elif not telegram_ok:
        return jsonify({"status": "fail", "error": "Telegram-fout"})
    elif not email_ok:
        return jsonify({"status": "fail", "error": "E-mailfout"})
    else:
        return jsonify({"status": "fail", "error": "Beide mislukt"})


@app.route("/submit_order", methods=["POST"])
def submit_order():
    data = request.get_json()
    message = data.get("message", "")

    telegram_ok = send_telegram_message(message)
    email_ok = send_email_notification(message)

    # Notify connected SocketIO clients about the new order
    socketio.emit('new_order', data)

    if telegram_ok and email_ok:
        return jsonify({"status": "ok"})
    elif not telegram_ok:
        return jsonify({"status": "fail", "error": "Telegram-fout"})
    elif not email_ok:
        return jsonify({"status": "fail", "error": "E-mailfout"})
    else:
        return jsonify({"status": "fail", "error": "Beide mislukt"})

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0")

