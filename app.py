# app.py
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import os
import time
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

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
        logger.debug(f"Supabase {method} request to {endpoint}")
        
        if method == 'GET':
            response = requests.get(url, headers=headers, params=data, timeout=10)
        elif method == 'POST':
            response = requests.post(url, headers=headers, json=data, timeout=10)
        elif method == 'PUT':
            response = requests.put(url, headers=headers, json=data, timeout=10)
        elif method == 'DELETE':
            response = requests.delete(url, headers=headers, timeout=10)
        else:
            raise ValueError("Invalid HTTP method")
        
        response.raise_for_status()
        return response.json() if response.content else None
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Supabase request error: {e}")
        return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/profile', methods=['POST'])
def fetch_profile():
    start_time = time.time()
    data = request.get_json()
    
    if not data:
        logger.error("No JSON data received")
        return jsonify({'error': 'No data received'}), 400
        
    url = data.get('url', '').strip()
    
    logger.debug(f"Received URL: {url}")
    
    if not url:
        logger.error("URL is empty")
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
            last_fetched_str = profile['last_fetched']
            # Handle both with and without timezone
            if last_fetched_str.endswith('Z'):
                last_fetched = datetime.fromisoformat(last_fetched_str.replace('Z', '+00:00'))
            else:
                last_fetched = datetime.fromisoformat(last_fetched_str)
                
            if (datetime.now().astimezone() - last_fetched).total_seconds() < 3600:
                logger.info(f"✅ Returning cached data ({(time.time() - start_time):.2f}s)")
                return jsonify(profile)
        
        # Fetch fresh data from SkillRack
        logger.info(f"Fetching fresh data from: {url}")
        html_content = fetch_page(url)
        if not html_content:
            logger.error("Failed to fetch HTML content from SkillRack")
            return jsonify({'error': 'Failed to fetch profile data from SkillRack. Please check the URL.'}), 400
            
        lines = clean_html(html_content)
        logger.debug(f"Extracted {len(lines)} lines from HTML")
        
        if len(lines) < 30:
            logger.error(f"Not enough data extracted. Expected at least 30 lines, got {len(lines)}")
            return jsonify({'error': 'Incomplete profile data received. The profile might be private or the URL is incorrect.'}), 400
            
        profile_data = extract_data(url, lines)
        logger.debug(f"Extracted profile data: {profile_data}")
        
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
        
        result = supabase_request('POST', 'skillrack_profiles', {
            'on_conflict': 'id',
            **upsert_data
        })
        
        logger.info(f"✅ Profile processed in {(time.time() - start_time):.2f}s")
        return jsonify(profile_data)
        
    except Exception as e:
        logger.error(f"❌ Error in fetch_profile: {str(e)}", exc_info=True)
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

def fetch_page(url):
    """Fetch webpage with timeout and error handling"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, timeout=15, headers=headers)
        logger.debug(f"SkillRack response status: {response.status_code}")
        return response.text if response.status_code == 200 else None
    except requests.exceptions.RequestException as e:
        logger.error(f"Request exception: {e}")
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
    
    logger.debug(f"Extracted data - Name: {data['name']}, College: {data['college']}, Points: {data['points']}")
    return data

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
