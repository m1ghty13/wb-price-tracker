#!/bin/bash
set -e

git pull origin main
source venv/bin/activate
pip install -r requirements.txt -q
python -m playwright install chromium --with-deps
systemctl restart wb-tracker
systemctl status wb-tracker --no-pager
echo "Deploy done"
