#!/usr/bin/env bash
echo "🚀 Custom Render Build Script: starting install"

# 强制使用 pip 而不是 Poetry
pip install --upgrade pip
pip install -r requirements.txt

echo "✅ Dependencies installed. Starting gunicorn..."
