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

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
CORS(app)

# Supabase configuration from environment variables
SUPABASE_URL = os.environ.get('SUPABASE_URL')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')

# Validate environment variables
if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Error: SUPABASE_URL and SUPABASE_KEY must be set in .env file")
    print("Please check your .env file configuration")
    exit(1)

print(f"✅ Supabase configured: {SUPABASE_URL[:20]}...")

def supabase_request(method, endpoint, data=None):
    """Make direct HTTP requests to Supabase"""
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'resolution=merge-duplicates'
    }
    
    url = f"{SUPABASE_URL}/rest/v1/{endpoint}"
    
    try:
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
        return response.json() if response.content else {'success': True}
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Supabase request error: {e}")
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
    
    # Simple URL validation
    if 'skillrack.com' not in url:
        return jsonify({'error': 'Please enter a valid SkillRack profile URL'}), 400
    
    try:
        print(f"🔍 Processing profile: {url}")
        
        # Check if profile exists in database
        existing_data = supabase_request('GET', 'skillrack_profiles', {
            'profile_url': f'eq.{url}',
            'select': '*'
        })
        
        # If exists and fetched recently (within 1 hour), return cached data
        if existing_data and len(existing_data) > 0:
            profile = existing_data[0]
            last_fetched_str = profile['last_fetched']
            
            # Handle timestamp format
            if last_fetched_str.endswith('Z'):
                last_fetched = datetime.fromisoformat(last_fetched_str.replace('Z', '+00:00'))
            else:
                last_fetched = datetime.fromisoformat(last_fetched_str)
                
            cache_age = (datetime.now().astimezone() - last_fetched).total_seconds()
            
            if cache_age < 3600:  # 1 hour cache
                print(f"✅ Returning cached data ({cache_age:.0f}s old, processed in {(time.time() - start_time):.2f}s)")
                return jsonify(profile)
        
        # Fetch fresh data from SkillRack
        print("🌐 Fetching fresh data from SkillRack...")
        html_content = fetch_page(url)
        if not html_content:
            return jsonify({'error': 'Failed to fetch profile data from SkillRack. Please check if the URL is correct and the profile is public.'}), 400
            
        lines = clean_html(html_content)
        
        # Debug: Print line count for troubleshooting
        print(f"📄 Extracted {len(lines)} lines from HTML")
        
        if len(lines) < 30:
            return jsonify({'error': 'Incomplete profile data received. The profile might be private or the URL format has changed.'}), 400
            
        profile_data = extract_data(url, lines)
        
        # Store data in Supabase
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
        
        print("💾 Saving data to Supabase...")
        db_result = supabase_request(
            'POST',
            'skillrack_profiles?on_conflict=id',
            upsert_data
        )

        
        if db_result is None:
            print("⚠️  Could not save to database, but returning profile data")
        else:
            print("✅ Data saved to Supabase successfully")
        
        total_time = time.time() - start_time
        print(f"✅ Profile processed in {total_time:.2f}s")
        return jsonify(profile_data)
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({'error': f'Server error: {str(e)}'}), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        # Test Supabase connection
        test_result = supabase_request('GET', 'skillrack_profiles', {'limit': '1'})
        db_status = 'connected' if test_result is not None else 'disconnected'
        
        return jsonify({
            'status': 'healthy',
            'database': db_status,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500

def fetch_page(url):
    """Fetch webpage with proper headers"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
        }
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            return response.text
        else:
            print(f"❌ Failed to fetch page. Status: {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Request error: {e}")
        return None

def clean_html(html_content):
    """Clean HTML and extract useful text lines"""
    if not html_content:
        return []
        
    soup = BeautifulSoup(html_content, 'html.parser')

    # Remove unwanted tags
    for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
        tag.decompose()

    text = soup.get_text(separator='\n')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines

def to_int(value):
    """Safely convert to integer"""
    try:
        return int(value)
    except:
        return 0

def extract_data(url, lines):
    """Extract profile data from lines with better error handling"""
    # Extract ID from URL
    id_match = re.search(r"id=(\d+)", url)
    if not id_match:
        id_match = re.search(r"/profile/(\d+)", url)
    
    # Safe data extraction with bounds checking
    try:
        data = {
            "id": id_match.group(1) if id_match else str(hash(url)),
            "name": lines[9] if len(lines) > 9 else "Not Available",
            "college": lines[12] if len(lines) > 12 else "Not Available",
            "solved": to_int(lines[25]) if len(lines) > 25 else 0,
            "code_tutor": to_int(lines[35]) if len(lines) > 35 else 0,
            "code_track": to_int(lines[29]) if len(lines) > 29 else 0,
            "dc": to_int(lines[31]) if len(lines) > 31 else 0,
            "dt": to_int(lines[33]) if len(lines) > 33 else 0,
            "points": (to_int(lines[29]) + to_int(lines[31])) * 2 + to_int(lines[33]) * 20,
            "last_fetched": datetime.now().isoformat(),
            "profile_url": url
        }
        
        print(f"📊 Extracted data - Name: {data['name']}, College: {data['college']}, Points: {data['points']}")
        return data
        
    except Exception as e:
        print(f"❌ Error extracting data: {e}")
        # Return minimal data structure
        return {
            "id": str(hash(url)),
            "name": "Error extracting data",
            "college": "Unknown",
            "solved": 0,
            "code_tutor": 0,
            "code_track": 0,
            "dc": 0,
            "dt": 0,
            "points": 0,
            "last_fetched": datetime.now().isoformat(),
            "profile_url": url
        }

if __name__ == '__main__':
    print("🚀 Starting SkillRack Profile Analyzer...")
    print("📁 Make sure your .env file has SUPABASE_URL and SUPABASE_KEY")
    app.run(debug=True, host='0.0.0.0', port=5000)
