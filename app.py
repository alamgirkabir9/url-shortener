from flask import Flask, request, redirect, render_template, jsonify
import string
import random
import re
from urllib.parse import urlparse
from datetime import datetime
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-default-secret-key')

# Database configuration - handle Render's PostgreSQL URL format
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

def init_db():
    """Initialize the database with required tables"""
    if not DATABASE_URL:
        print("No DATABASE_URL found")
        return
        
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        
        # Create urls table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS urls (
                id SERIAL PRIMARY KEY,
                short_id VARCHAR(10) UNIQUE NOT NULL,
                original_url TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                clicks INTEGER DEFAULT 0
            )
        ''')
        
        # Create clicks table for tracking individual clicks
        cur.execute('''
            CREATE TABLE IF NOT EXISTS clicks (
                id SERIAL PRIMARY KEY,
                short_id VARCHAR(10) NOT NULL,
                clicked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (short_id) REFERENCES urls(short_id)
            )
        ''')
        
        # Create indexes for better performance
        cur.execute('CREATE INDEX IF NOT EXISTS idx_urls_short_id ON urls(short_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_clicks_short_id ON clicks(short_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_clicks_clicked_at ON clicks(clicked_at)')
        
        conn.commit()
        cur.close()
        conn.close()
        print("Database initialized successfully")
        
    except Exception as e:
        print(f"Error initializing database: {e}")
        print(f"DATABASE_URL: {DATABASE_URL[:50]}..." if DATABASE_URL else "DATABASE_URL is None")

@contextmanager
def get_db_connection():
    """Context manager for database connections"""
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not configured")
        
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        yield conn
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Database connection error: {e}")
        raise e
    finally:
        if conn:
            conn.close()

def generate_short_id(length=6):
    """Generate a random short ID that doesn't already exist"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        
        while True:
            short_id = ''.join(random.choices(string.ascii_letters + string.digits, k=length))
            
            # Check if short_id already exists
            cur.execute("SELECT 1 FROM urls WHERE short_id = %s", (short_id,))
            if not cur.fetchone():
                cur.close()
                return short_id

def validate_url(url):
    """Validate if the provided URL is properly formatted"""
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc]) and url
    except:
        return False

# Initialize database when the app starts (moved outside of __main__ block)
if DATABASE_URL:
    init_db()

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/api/shorten', methods=['POST'])
def api_shorten():
    """API endpoint for creating short URLs"""
    data = request.get_json()
    
    if not data or 'url' not in data:
        return jsonify({'error': 'URL is required'}), 400
    
    long_url = data['url'].strip()
    validated_url = validate_url(long_url)
    
    if not validated_url:
        return jsonify({'error': 'Invalid URL format'}), 400
    
    try:
        with get_db_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            # Generate short ID
            short_id = generate_short_id()
            
            # Insert URL into database
            cur.execute(
                "INSERT INTO urls (short_id, original_url) VALUES (%s, %s) RETURNING created_at",
                (short_id, validated_url)
            )
            result = cur.fetchone()
            conn.commit()
            cur.close()
            
            short_url = request.host_url + short_id
            
            return jsonify({
                'short_url': short_url,
                'original_url': validated_url,
                'short_id': short_id,
                'created_at': result['created_at'].isoformat()
            })
            
    except Exception as e:
        return jsonify({'error': 'Database error occurred'}), 500

@app.route('/favicon.ico')
def favicon():
    """Handle favicon requests to prevent 404 errors"""
    return '', 404

@app.route('/<short_id>')
def redirect_url(short_id):
    """Redirect to the original URL and track clicks"""
    try:
        with get_db_connection() as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            # Get the original URL
            cur.execute("SELECT original_url FROM urls WHERE short_id = %s", (short_id,))
            result = cur.fetchone()
            
            if result:
                # Update click count
                cur.execute(
                    "UPDATE urls SET clicks = clicks + 1 WHERE short_id = %s",
                    (short_id,)
                )
                
                # Record the click
                cur.execute(
                    "INSERT INTO clicks (short_id) VALUES (%s)",
                    (short_id,)
                )
                
                conn.commit()
                cur.close()
                
                return redirect(result['original_url'])
            else:
                cur.close()
                return "Short URL not found", 404
                
    except Exception as e:
        return "Server error", 500

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy'})

if __name__ == '__main__':
    # Get port from environment variable for deployment
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
