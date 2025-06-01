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
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as ec
from dotenv import load_dotenv
from gmail_api import download_blob, upload_blob
from werkzeug.middleware.proxy_fix import ProxyFix
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import signal

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



options = webdriver.ChromeOptions()
options.add_argument("--headless=new")  # use the new headless mode
options.add_argument("--disable-gpu")
options.add_argument("--disable-software-rasterizer")
options.add_argument("--use-gl=swiftshader")  # force software OpenGL
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--window-size=1920,1080")

#service = Service(chromedriver_path)
browser = webdriver.Chrome(options=options)
browser.get('https://x-camp.discourse.group/')

# Login
WebDriverWait(browser,
              15).until(ec.presence_of_element_located(
                  (By.ID, "username"))).send_keys(BOT_EMAIL)
WebDriverWait(browser,
              15).until(ec.presence_of_element_located(
                  (By.ID, "password"))).send_keys(BOT_PASSWORD)
signin = WebDriverWait(browser, 15).until(
    ec.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))
signin.click()
WebDriverWait(browser, 20).until(
    ec.presence_of_element_located((By.CSS_SELECTOR, ".current-user")))
reqs = requests.Session()
for cookie in browser.get_cookies():
    reqs.cookies.set(cookie['name'], cookie['value'])

app = Flask(__name__)
app.secret_key = os.urandom(64)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

DB_PATH = 'discoursesecure.db'
REMOTE_DB_NAME = 'discoursesecure.db'

# Thread synchronization
db_dirty_event2 = threading.Event()
db_lock2 = threading.Lock()

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

# --- Selenium login and PM sending with cookies ---


def load_cookies(cookie_path):
    with open(cookie_path, "r") as f:
        return json.load(f)

def send_pm_via_selenium(recipient_username, secret_code):
    print("sending")
    global browser
    try:
        # Go to messages page
        browser.get(f"{DISCOURSE_URL}/u/cubicbrick/messages")
        time.sleep(3)
        print("at page")

        # Click to compose new message
        new_msg_btn = browser.find_element(By.XPATH,
            "/html/body/section/div[1]/div[3]/div[2]/div[2]/section/section/div/div/section/ul/li[1]/button")
        new_msg_btn.click()
        time.sleep(2)

        # Find the <details> element for recipients
        details_elem = WebDriverWait(browser, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "details.select-kit.multi-select.user-chooser"))
        )
        summary_elem = details_elem.find_element(By.CSS_SELECTOR, "summary")
        summary_elem.click()

        # Fill in recipient
        user_input = browser.find_element(By.CSS_SELECTOR, 'details#private-message-users input[type="search"]')
        user_input.clear()
        user_input.send_keys(recipient_username)
        time.sleep(2)  # allow dropdown to populate
        user_input.send_keys(Keys.ENTER)
        time.sleep(1)

        # Fill in title
        title_input = browser.find_element(By.ID, "reply-title")
        title_input.send_keys("Your Verification Code")
        time.sleep(1)

        # Fill in body (message content)
        body_textarea = browser.find_element(By.CSS_SELECTOR, 'textarea.d-editor-input')
        body_textarea.send_keys(f"Hello @{recipient_username}, your verification code is: **{secret_code}**")
        time.sleep(1)

        # Send the message
        send_button = browser.find_element(By.XPATH,
            "/html/body/section/div[1]/div[9]/div[3]/div[3]/div/button[1]")
        send_button.click()
        time.sleep(3)
        print("send message")

        app.logger.info(f"PM sent to {recipient_username} with secret {secret_code}.")

    except Exception as e:
        app.logger.error(f"Selenium error while sending PM: {e}")

# --- Flask endpoints ---

@app.route('/discoursesecure/getRSA', methods=['POST'])
def get_rsa():
    with db_lock2:
        conn = get_db_connection()
        rows = conn.execute('SELECT username, rsa FROM keys WHERE rsa IS NOT NULL').fetchall()
        conn.close()
    result = {row['username']: row['rsa'] for row in rows}
    return jsonify(result)

@app.route('/discoursesecure/getSecret', methods=['POST'])
def get_secret():
    print("secret request")
    data = request.get_json()
    username = data.get('username')

    if not username:
        return jsonify({'error': 'Username is required'}), 400

    secret = generate_secret()

    with db_lock2:
        conn = get_db_connection()
        # Insert or update secret for username (clear RSA so they re-add key)
        conn.execute('REPLACE INTO keys (username, secret, rsa) VALUES (?, ?, NULL)', (username, secret))
        conn.commit()
        conn.close()

    mark_db_dirty()

    # Send PM with the secret code via Selenium bot
    try:
        send_pm_via_selenium(username, secret)
    except Exception as e:
        app.logger.warning(f"Failed to send PM: {e}")

    return jsonify({'message': f'Secret generated and PM sent for {username}'})

@app.route('/discoursesecure/addRSA', methods=['POST'])
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
