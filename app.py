from flask import Flask, request, jsonify
from flask_cors import CORS
import smtplib
from email.mime.text import MIMEText
from email.header import Header
from email.utils import formataddr

app = Flask(__name__)
CORS(app)  # 允许跨域访问

def send_email_notification(order_text):
    sender_email = "qianchennl@gmail.com"
    sender_password = "wtuyxljsjwftyzfm"
    receiver_email = "qianchennl@gmail.com"

    subject = "Nova Asia - Nieuwe bestelling"
    msg = MIMEText(order_text, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr(("NovaAsia", sender_email))
    msg["To"] = receiver_email

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, [receiver_email], msg.as_string())
        print("✅ E-mail verzonden!")
        return True
    except Exception as e:
        print(f"❌ Verzendfout: {e}")
        return False

@app.route("/")
def index():
    return "Flask API werkt!"

@app.route("/api/send", methods=["POST"])
def api_send_order():
    data = request.get_json()
    message = data.get("message", "")
    success = send_email_notification(message)
    return jsonify({"status": "ok" if success else "fail"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

