# app.py
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import os
import time

app = Flask(__name__)
CORS(app)

# Supabase configuration
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

def supabase_request(method, endpoint, data=None):
    """Make direct HTTP requests to Supabase"""
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal'
    }
    
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    
    try:
        if method == 'GET':
            response = requests.get(url, headers=headers, params=data)
        elif method == 'POST':
            response = requests.post(url, headers=headers, json=data)
        elif method == 'PUT':
            response = requests.put(url, headers=headers, json=data)
        elif method == 'DELETE':
            response = requests.delete(url, headers=headers)
        else:
            raise ValueError("Invalid HTTP method")
        
        response.raise_for_status()
        return response.json() if response.content else None
        
    except requests.exceptions.RequestException as e:
        print(f"Supabase request error: {e}")
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/profile', methods=['POST'])
def fetch_profile():
    start_time = time.time()
    data = request.get_json()
    url = data.get('url', '').strip()
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    try:
        # Check if profile exists in database (fast query)
        existing_data = supabase_request('GET', 'skillrack_profiles', {
            'profile_url': f'eq.{url}',
            'select': '*'
        })
        
        # If exists and fetched recently (within 1 hour), return cached data
        if existing_data and len(existing_data) > 0:
            profile = existing_data[0]
            last_fetched = datetime.fromisoformat(profile['last_fetched'].replace('Z', '+00:00'))
            if (datetime.now().astimezone() - last_fetched).total_seconds() < 3600:
                print(f"✅ Returning cached data ({(time.time() - start_time):.2f}s)")
                return jsonify(profile)
        
        # Fetch fresh data from SkillRack
        html_content = fetch_page(url)
        if not html_content:
            return jsonify({'error': 'Failed to fetch profile data from SkillRack'}), 400
            
        lines = clean_html(html_content)
        profile_data = extract_data(url, lines)
        
        # Upsert to Supabase using direct request - ONLY REQUIRED FIELDS
        upsert_data = {
            'id': profile_data['id'],
            'name': profile_data['name'],
            'college': profile_data['college'],
            'points': profile_data['points'],
            'last_fetched': profile_data['last_fetched'],
            'dc': profile_data['dc'],
            'dt': profile_data['dt'],
            'profile_url': profile_data['profile_url']
        }
        
        supabase_request('POST', 'skillrack_profiles', {
            'on_conflict': 'id',
            **upsert_data
        })
        
        print(f"✅ Profile processed in {(time.time() - start_time):.2f}s")
        return jsonify(profile_data)
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({'error': str(e)}), 500

def fetch_page(url):
    """Fetch webpage with timeout and error handling"""
    try:
        response = requests.get(url, timeout=10)
        return response.text if response.status_code == 200 else None
    except requests.exceptions.RequestException:
        return None

def clean_html(html_content):
    """Clean HTML and extract text lines"""
    if not html_content:
        return []
        
    soup = BeautifulSoup(html_content, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
        tag.decompose()
    
    text = soup.get_text(separator='\n')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines

def to_int(value):
    """Safely convert to integer"""
    if isinstance(value, int):
        return value
    try:
        return int(re.sub(r'[^\d]', '', str(value)))
    except:
        return 0

def extract_data(url, lines):
    """Extract and structure profile data - ONLY REQUIRED FIELDS"""
    profile_id = re.search(r"id=(\d+)", url)
    
    # Safe indexing with fallbacks
    def get_line(index, default=''):
        return lines[index] if len(lines) > index else default
    
    data = {
        "id": profile_id.group(1) if profile_id else str(hash(url)),
        "name": get_line(9),
        "college": get_line(12),
        "dc": to_int(get_line(31)),
        "dt": to_int(get_line(33)),
        "points": (to_int(get_line(29)) + to_int(get_line(31))) * 2 + to_int(get_line(33)) * 20,
        "last_fetched": datetime.now().isoformat(),
        "profile_url": url
    }
    return data

if __name__ == '__main__':
    app.run(debug=True)