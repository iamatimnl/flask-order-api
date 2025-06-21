# Nova Asia – Flask on Render

This repository demonstrates a minimal Flask deployment on Render. The goal is to avoid automatic Poetry detection and ensure `gunicorn` is installed and used at runtime.

## Structure

- `app.py` – Flask application.
- `wsgi.py` – Gunicorn entry point.
- `requirements.txt` – Dependencies. Now includes `Flask-SocketIO` for real-time
  updates.
- `runtime.txt` – Python version lock.
- `render.yaml` – Render configuration with an `eventlet` worker.

## Render configuration

The `render.yaml` file overrides Render's build steps to install dependencies with `pip` and explicitly install `gunicorn`:

```yaml
services:
  - type: web
    name: nova-asia-test
    runtime: python
    buildCommand: |
      pip install --upgrade pip
      pip install -r requirements.txt
      pip install gunicorn eventlet
    startCommand: gunicorn --worker-class eventlet -w 1 wsgi:app
```

Create a new Web Service on Render, link this repository, and it will deploy using the configuration above.

## Environment Variables

- `DATABASE_URL` - database connection URI.
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` - optional Telegram notification settings.
- `SMTP_SERVER`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `FROM_EMAIL` - optional SMTP settings.
- `ADMIN_EMAIL` - address to receive order notifications.
- `TIKKIE_URL` - URL to redirect customers for online payments.

For local development copy `.env.example` to `.env` and fill in the values.
Without these variables Telegram, email and payment notifications will fail.
