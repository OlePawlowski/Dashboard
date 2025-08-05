from flask import Flask, render_template, request, redirect, session, jsonify, url_for
from flask_cors import CORS
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import os
import datetime
from dotenv import load_dotenv
import json
import requests


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

@app.route("/api/externe-anfrage", methods=["POST"])
def externe_anfrage():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Ungültige Daten"}), 400

    # Datei lesen
    try:
        with open("anfragen.json", "r") as f:
            anfragen = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        anfragen = []

    # Neue Anfrage einfügen
    anfragen.insert(0, data)

    # Datei speichern
    with open("anfragen.json", "w") as f:
        json.dump(anfragen, f)

    return jsonify({"success": True})

@app.route("/api/anfrage", methods=["POST"])
def neue_anfrage():
    # Entferne diese Zeile:
    # if "user" not in session:
    #     return jsonify({"error": "Nicht eingeloggt"}), 401

    data = request.get_json()
    if not data:
        return jsonify({"error": "Ungültige Daten"}), 400

    # Globale Speicherung in Datei oder Liste
    try:
        with open("anfragen.json", "r") as f:
            anfragen = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        anfragen = []

    anfragen.insert(0, data)

    with open("anfragen.json", "w") as f:
        json.dump(anfragen, f)

    return jsonify({"success": True})


# 📤 Anfragen ausgeben
@app.route("/api/get-anfragen")
def get_anfragen():
    try:
        with open("anfragen.json", "r") as f:
            return jsonify(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return jsonify([])


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

# 📲 WhatsApp-Nachrichten abrufen
@app.route("/api/whatsapp-messages")
def whatsapp_messages():
    RESPOND_API_TOKEN = os.getenv("RESPOND_API_TOKEN")
    if not RESPOND_API_TOKEN:
        return jsonify({"error": "Kein API-Token gesetzt"}), 500

    headers = {
        "Authorization": f"Bearer {RESPOND_API_TOKEN}"
    }

    seit = (datetime.datetime.utcnow() - datetime.timedelta(hours=12)).isoformat() + "Z"

    params = {
        "channel_id": "389861",
        "direction": "inbound",
        "after": seit
    }


    try:
        res = requests.get("https://api.respond.io/v2/messages", headers=headers, params=params)
        data = res.json().get("data", [])
        messages = [{
            "name": m.get("contact", {}).get("name", "Unbekannt"),
            "text": m.get("content", "[Leere Nachricht]"),
            "time": m.get("created_at", "")
        } for m in data[:10]]
        return jsonify(messages)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ▶️ Nur lokal öffnen
if __name__ == "__main__":
    import webbrowser, threading
    threading.Timer(1.5, lambda: webbrowser.open_new("http://127.0.0.1:5000")).start()
    app.run(debug=True)
