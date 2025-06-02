import os
import json
import time
import random
import string
import threading
from gevent import monkey, spawn
monkey.patch_all()
import sqlite3
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from gmail_api import download_blob, upload_blob
from werkzeug.middleware.proxy_fix import ProxyFix
from datetime import datetime
import signal
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
# --- Load .env if exists ---
if os.path.exists(".env"):
    load_dotenv()

BOT_USERNAME = os.getenv("BOT_USERNAME")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
DISCOURSE_URL = os.getenv("DISCOURSE_URL")
BOT_EMAIL = os.getenv('BOT_EMAIL')

if not all([BOT_USERNAME, BOT_PASSWORD, DISCOURSE_URL]):
    print("Missing BOT_USERNAME, BOT_PASSWORD, or DISCOURSE_URL in .env")
    exit(1)



app = Flask(__name__)
app.secret_key = os.urandom(64)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

DB_PATH = 'instance/discoursesecure.db'
REMOTE_DB_NAME = 'discoursesecure.db'

# Thread synchronization
db_dirty_event2 = threading.Event()
db_lock2 = threading.Lock()

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=[],
)

# Initialize DB and download if missing
def init_db():
    if not os.path.exists(DB_PATH):
        app.logger.info("Downloading DB from Drive...")
        try:
            download_blob(local_path=DB_PATH, remote_name=REMOTE_DB_NAME)
        except Exception as e:
            app.logger.warning(f"Failed to download DB: {e}")

    with db_lock2, sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS keys (
                username TEXT PRIMARY KEY,
                rsa TEXT,
                secret TEXT
            )
        ''')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def generate_secret(length=10):
    return ''.join(random.choices(string.digits, k=length))

def mark_db_dirty():
    db_dirty_event2.set()

def db_upload_watcher():
    while True:
        db_dirty_event2.wait()
        app.logger.info("DB marked dirty. Starting upload process...")

        while db_dirty_event2.is_set():
            db_dirty_event2.clear()
            app.logger.info("Uploading DB to Google Drive...")

            with db_lock2:
                try:
                    upload_blob(local_path=DB_PATH, remote_name=REMOTE_DB_NAME)
                    app.logger.info("Database upload complete.")
                except Exception as e:
                    app.logger.warning(f"Upload failed: {e}")


reqs = requests.Session()

def csrf():
    csrf = reqs.get("https://x-camp.discourse.group/session/csrf.json").json().get("csrf")
    print(csrf)
    return csrf

def send_pm(content:str, topic_title:str,recipents:list):
    recipents=",".join(recipents)
    print(recipents)
    headers = {
        'Accept': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
        'X-CSRF-Token': csrf(),  # Only if required
        'User-Agent': os.getenv(BOT_EMAIL),
        'Referer': 'https://x-camp.discourse.group/',
        'Origin': 'https://x-camp.discourse.group/',
    }

    cookies = {
        '_forum_session': os.getenv("_fs"),
        '_t':os.getenv("_t"),
    }
    data = {
        'title': topic_title,
        'raw': content,
        'target_recipients':recipents,
        'unlist_topic':'false',
        'archetype':'private_message',
    }
    post_p =reqs.post("https://x-camp.discourse.group/posts.json", headers=headers, cookies=cookies, data=data)
    print(post_p.json())

def load_cookies(cookie_path):
    with open(cookie_path, "r") as f:
        return json.load(f)

# --- Flask endpoints ---

@app.route('/discoursesecure/getRSA', methods=['POST'])
@limiter.limit("1 per 10 seconds")
def get_rsa():
    with db_lock2:
        conn = get_db_connection()
        rows = conn.execute('SELECT username, rsa FROM keys WHERE rsa IS NOT NULL').fetchall()
        conn.close()
    result = {row['username']: row['rsa'] for row in rows}
    return jsonify(result)

@app.route('/discoursesecure/getSecret', methods=['POST'])
@limiter.limit("1 per 20 minutes")
def get_secret():
    print("secret request")
    data = request.get_json()
    username = data.get('username')

    if not username:
        return jsonify({'error': 'Username is required'}), 400

    secret = generate_secret()

    with db_lock2:
        conn = get_db_connection()
        conn.execute('REPLACE INTO keys (username, secret) VALUES (?, ?)', (username, secret))
        conn.commit()
        conn.close()

    mark_db_dirty()

    try:
        send_pm(f"Your verification code is {secret}.", "Verify your identity", [username])
    except Exception as e:
        app.logger.warning(f"Failed to send PM: {e}")

    return jsonify({'message': f'Secret generated and PM sent for {username}'})

@app.route('/discoursesecure/addRSA', methods=['POST'])
@limiter.limit("1 per 20 minutes")
def add_rsa():

    data = request.get_json()
    username = data.get('username')
    secret = data.get('secret')
    rsa_key = data.get('RSA')

    if not all([username, secret, rsa_key]):
        return jsonify({'error': 'username, secret, and RSA key are required'}), 400

    with db_lock2:
        conn = get_db_connection()
        row = conn.execute('SELECT secret FROM keys WHERE username = ?', (username,)).fetchone()

        if not row or row['secret'] != secret:
            conn.close()
            return jsonify({'error': 'Invalid secret'}), 403

        conn.execute('UPDATE keys SET rsa = ?, secret = NULL WHERE username = ?', (rsa_key, username))
        conn.commit()
        conn.close()

    mark_db_dirty()

    return jsonify({'message': f'RSA key for {username} added successfully'})

# --- Startup ---

os.makedirs('instance', exist_ok=True)
init_db()
def cleanup():
    upload_blob()
    app.logger.info("Database uploaded before shutdown.")
    for handler in app.logger.handlers:
        handler.flush()
        handler.close()
    
    os._exit(0)

shutdown_in_progress= False
cleanup_thread_running = True
def shutdown_handler(signum, frame):
    global cleanup_thread_running
    global shutdown_in_progress
    cleanup_thread_running = False
    if not shutdown_in_progress:
        shutdown_in_progress = True
        print(f"Received signal {signum}, uploading db...")
        spawn(cleanup)

watcher_thread = threading.Thread(target=db_upload_watcher, daemon=True)
watcher_thread.start()
signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

print("ready")

if __name__ == '__main__':
    app.run(debug=False)
