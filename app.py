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
    
    # Validate URL format
    if not is_valid_skillrack_url(url):
        logger.error(f"Invalid SkillRack URL format: {url}")
        return jsonify({'error': 'Invalid SkillRack URL format. Please use a valid SkillRack profile URL.'}), 400
    
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
            return jsonify({'error': 'Failed to fetch profile data from SkillRack. Please check if the profile exists and is public.'}), 400
            
        lines = clean_html(html_content)
        logger.debug(f"Extracted {len(lines)} lines from HTML")
        
        # Debug: Print first 40 lines to see what we're getting
        for i, line in enumerate(lines[:40]):
            logger.debug(f"Line {i}: {line}")
        
        if len(lines) < 20:
            logger.error(f"Not enough data extracted. Expected at least 20 lines, got {len(lines)}")
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

def is_valid_skillrack_url(url):
    """Check if the URL is a valid SkillRack profile URL"""
    patterns = [
        r'https?://(www\.)?skillrack\.com/profile/\d+/[a-f0-9]+',  # New format
        r'https?://(www\.)?skillrack\.com/faces/candidate/profile\.xhtml\?id=\d+',  # Old format
    ]
    return any(re.search(pattern, url, re.IGNORECASE) for pattern in patterns)

def extract_profile_id(url):
    """Extract profile ID from both URL formats"""
    # New format: http://www.skillrack.com/profile/500444/c3456f579d36f135ec68b08338299ebc1276f723
    new_format_match = re.search(r'skillrack\.com/profile/(\d+)', url)
    if new_format_match:
        return new_format_match.group(1)
    
    # Old format: https://www.skillrack.com/faces/candidate/profile.xhtml?id=12345
    old_format_match = re.search(r'[?&]id=(\d+)', url)
    if old_format_match:
        return old_format_match.group(1)
    
    # Fallback: use hash of URL
    return str(hash(url))

def fetch_page(url):
    """Fetch webpage with timeout and error handling"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        response = requests.get(url, timeout=15, headers=headers)
        logger.debug(f"SkillRack response status: {response.status_code}")
        if response.status_code == 200:
            logger.debug("Successfully fetched SkillRack page")
            return response.text
        else:
            logger.error(f"SkillRack returned status code: {response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Request exception: {e}")
        return None

def clean_html(html_content):
    """Clean HTML and extract text lines"""
    if not html_content:
        return []
        
    soup = BeautifulSoup(html_content, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'meta', 'link']):
        tag.decompose()
    
    text = soup.get_text(separator='\n')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines

def to_int(value):
    """Safely convert to integer"""
    if isinstance(value, int):
        return value
    try:
        # Remove any non-digit characters and convert
        cleaned = re.sub(r'[^\d]', '', str(value))
        return int(cleaned) if cleaned else 0
    except:
        return 0

def find_pattern_in_lines(lines, patterns):
    """Find value by multiple possible patterns"""
    for pattern in patterns:
        for i, line in enumerate(lines):
            if pattern in line.lower():
                # Return the next line or extract number from current line
                if i + 1 < len(lines):
                    return lines[i + 1]
                else:
                    return line
    return '0'

def extract_data(url, lines):
    """Extract and structure profile data - handles new SkillRack format"""
    profile_id = extract_profile_id(url)
    
    logger.debug("Searching for profile data in lines...")
    
    # More flexible pattern matching for the new SkillRack format
    name = find_pattern_in_lines(lines, ['name:', 'candidate name:', 'profile of'])
    college = find_pattern_in_lines(lines, ['college:', 'institution:', 'university:'])
    
    # Find numbers - look for patterns in the text
    dc = 0
    dt = 0
    code_track = 0
    
    for i, line in enumerate(lines):
        line_lower = line.lower()
        
        # Daily Challenge
        if any(pattern in line_lower for pattern in ['daily challenge', 'dc solved']):
            dc = to_int(line)
            # Also check next line if current doesn't have number
            if dc == 0 and i + 1 < len(lines):
                dc = to_int(lines[i + 1])
        
        # Daily Test  
        elif any(pattern in line_lower for pattern in ['daily test', 'dt solved']):
            dt = to_int(line)
            if dt == 0 and i + 1 < len(lines):
                dt = to_int(lines[i + 1])
        
        # Code Track (problems solved)
        elif any(pattern in line_lower for pattern in ['problems solved', 'total solved', 'code track']):
            code_track = to_int(line)
            if code_track == 0 and i + 1 < len(lines):
                code_track = to_int(lines[i + 1])
    
    # Calculate points based on SkillRack formula
    points = (code_track + dc) * 2 + dt * 20
    
    data = {
        "id": profile_id,
        "name": name if name != '0' else 'Unknown',
        "college": college if college != '0' else 'Unknown',
        "dc": dc,
        "dt": dt,
        "points": points,
        "last_fetched": datetime.now().isoformat(),
        "profile_url": url
    }
    
    logger.debug(f"Extracted data - Name: {data['name']}, College: {data['college']}, Points: {data['points']}")
    logger.debug(f"DC: {dc}, DT: {dt}, Code Track: {code_track}")
    
    return data

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
