from flask import Flask, render_template, request, redirect, session, jsonify, url_for
from flask_cors import CORS
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import os
import datetime
from dotenv import load_dotenv
import json

# 🔃 .env laden (lokal)
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "fallback")
CORS(app)

# 👥 Benutzer
USERS = {
    os.getenv("USER_1_NAME"): os.getenv("USER_1_PASS"),
    os.getenv("USER_2_NAME"): os.getenv("USER_2_PASS")
}

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# 📬 E-Mail Auth über Environment
def load_credentials_from_env():
    token_str = os.getenv("TOKEN_JSON")
    if not token_str:
        raise Exception("TOKEN_JSON nicht gesetzt")
    token_data = json.loads(token_str)
    return Credentials.from_authorized_user_info(token_data, SCOPES)

# 📥 Anfrage empfangen
@app.route("/api/anfrage", methods=["POST"])
def neue_anfrage():
    if "user" not in session:
        return jsonify({"error": "Nicht eingeloggt"}), 401

    data = request.get_json()
    if not data:
        return jsonify({"error": "Ungültige Daten"}), 400

    if "anfragen" not in session:
        session["anfragen"] = []
    session["anfragen"].insert(0, data)
    return jsonify({"success": True})

# 📤 Anfragen ausgeben
@app.route("/api/get-anfragen")
def get_anfragen():
    if "user" not in session:
        return jsonify([])
    return jsonify(session.get("anfragen", []))

# 🔐 Login
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if USERS.get(username) == password:
            session["user"] = username
            return redirect("/dashboard")
        return "❌ Falscher Login", 401
    return render_template("login.html")

# 📊 Dashboard
@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/")
    return render_template("index.html")

# 📧 Gmail API
@app.route("/api/emails")
def get_emails():
    if "user" not in session:
        return jsonify({"error": "Nicht eingeloggt"}), 401

    try:
        creds = load_credentials_from_env()
        service = build('gmail', 'v1', credentials=creds)
        results = service.users().messages().list(userId='me', maxResults=5).execute()
        messages = results.get('messages', [])
    except Exception as e:
        return jsonify({"error": f"Fehler bei Gmail API: {str(e)}"}), 500

    email_list = []
    for msg in messages:
        msg_data = service.users().messages().get(userId='me', id=msg['id']).execute()
        headers = msg_data['payload']['headers']
        email_info = {
            "from": next((h['value'] for h in headers if h['name'] == 'From'), 'Unbekannt'),
            "subject": next((h['value'] for h in headers if h['name'] == 'Subject'), '(Kein Betreff)'),
            "time": datetime.datetime.fromtimestamp(
                int(msg_data['internalDate']) / 1000).strftime('%d.%m.%Y – %H:%M')
        }
        email_list.append(email_info)

    return jsonify(email_list)

# 🔓 Logout
@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/")

# ▶️ Nur lokal öffnen
if __name__ == "__main__":
    import webbrowser, threading
    threading.Timer(1.5, lambda: webbrowser.open_new("http://127.0.0.1:5000")).start()
    app.run(debug=True)
