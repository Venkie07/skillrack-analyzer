# app.py
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import os
import time
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')


# ---------- SUPABASE REQUEST (Only saving – no reading) ----------
def supabase_request(method, endpoint, data=None):
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'resolution=merge-duplicates'
    }

    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"

    try:
        if method == 'POST':
            response = requests.post(url, headers=headers, json=data, timeout=10)
        else:
            return None

        response.raise_for_status()
        return response.json() if response.content else {'success': True}

    except Exception as e:
        print("DB Error:", e)
        return None


# ---------- FRONT-END PAGE ----------
@app.route('/')
def index():
    return render_template('index.html')


# ---------- MAIN API ----------
@app.route('/api/profile', methods=['POST'])
def fetch_profile():
    data = request.get_json()
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'error': 'URL is required'}), 400

    if "skillrack.com" not in url:
        return jsonify({'error': 'Invalid SkillRack URL'}), 400

    print("🔍 Scraping fresh data for:", url)

    # Fetch the HTML page
    html_content = fetch_page(url)
    if not html_content:
        print("⚠️ Warning: Empty HTML from SkillRack, continuing.")
        html_content = ""  # still try to parse


    # Clean & extract data
    lines = clean_html(html_content)
    profile = extract_data(url, lines)

    # Save to Supabase (optional)
    supabase_request('POST', 'skillrack_profiles?on_conflict=id', profile)

    return jsonify(profile)


# ---------- FETCH PAGE ----------
def fetch_page(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "text/html",
        }
        res = requests.get(url, headers=headers, timeout=15)

        # Accept page even if partially loaded
        if res.status_code == 200 and len(res.text) > 1000:
            return res.text
        else:
            print("❌ SkillRack returned very small HTML (blocked by Cloudflare)")
            return res.text  # return anyway, don’t block

    except Exception as e:
        print("❌ Request error:", e)
        return None



# ---------- CLEAN HTML ----------
def clean_html(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(['script', 'style']):
        t.decompose()

    text = soup.get_text(separator="\n")
    return [l.strip() for l in text.split("\n") if l.strip()]


# ---------- SAFE INT ----------
def to_int(v):
    try:
        return int(v)
    except:
        return 0


# ---------- UNIVERSAL ID + KEY EXTRACTOR ----------
def extract_data(url, lines):
    # For profile URL pattern
    match1 = re.search(r"profile/(\d+)/([a-f0-9]+)", url)

    # For resume URL pattern
    match2 = re.search(r"id=(\d+)&key=([a-f0-9]+)", url)

    if match1:
        user_id, key = match1.group(1), match1.group(2)
    elif match2:
        user_id, key = match2.group(1), match2.group(2)
    else:
        user_id, key = "Unknown", "Unknown"

    profile = {
        "id": user_id,
        "name": lines[9] if len(lines) > 9 else "Unknown",
        "college": lines[12] if len(lines) > 12 else "Unknown",
        "solved": to_int(lines[25]) if len(lines) > 25 else 0,
        "codeTutor": to_int(lines[35]) if len(lines) > 35 else 0,
        "codeTrack": to_int(lines[29]) if len(lines) > 29 else 0,
        "dc": to_int(lines[31]) if len(lines) > 31 else 0,
        "dt": to_int(lines[33]) if len(lines) > 33 else 0,
        "points": ((to_int(lines[29]) + to_int(lines[31])) * 2 + to_int(lines[33]) * 20),
        "last_fetched": datetime.now().isoformat(),
        "profile_url": url
    }

    print("SCRAPED:", profile)
    return profile


# ---------- RUN SERVER ----------
if __name__ == "__main__":
    print("🚀 SkillRack Analyzer Running…")
    app.run(debug=True, host="0.0.0.0", port=5000)

