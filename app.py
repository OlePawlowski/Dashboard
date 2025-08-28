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

# 📬 Mehrere Gmail-Accounts via Environment (TOKEN_JSON_1..3)
def load_credentials_by_id(account_id: int):
    env_key = f"TOKEN_JSON_{account_id}"
    token_str = os.getenv(env_key)
    if not token_str:
        return None
    try:
        token_data = json.loads(token_str)
        return Credentials.from_authorized_user_info(token_data, SCOPES)
    except Exception:
        return None

# 📥 Anfrage empfangen
@app.route("/api/externe-anfrage", methods=["POST"])
def externe_anfrage():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Ungültige Daten"}), 400

    try:
        with open("anfragen.json", "r") as f:
            anfragen = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        anfragen = []

    anfragen.insert(0, data)

    with open("anfragen.json", "w") as f:
        json.dump(anfragen, f)

    return jsonify({"success": True})

@app.route("/api/anfrage", methods=["POST"])
def neue_anfrage():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Ungültige Daten"}), 400

    try:
        with open("anfragen.json", "r") as f:
            anfragen = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        anfragen = []

    anfragen.insert(0, data)

    with open("anfragen.json", "w") as f:
        json.dump(anfragen, f)

    return jsonify({"success": True})

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

# 📧 Gmail API (Multi-Account)
@app.route("/api/emails/<int:account_id>")
def get_emails_account(account_id: int):
    if "user" not in session:
        return jsonify({"error": "Nicht eingeloggt"}), 401

    creds = load_credentials_by_id(account_id)
    if creds is None:
        return jsonify({
            "connected": False,
            "emails": []
        })

    try:
        service = build('gmail', 'v1', credentials=creds)
        results = service.users().messages().list(userId='me', maxResults=5).execute()
        messages = results.get('messages', [])
    except Exception as e:
        return jsonify({
            "connected": False,
            "error": f"Fehler bei Gmail API: {str(e)}"
        })

    email_list = []
    for msg in messages:
        try:
            msg_data = service.users().messages().get(userId='me', id=msg['id']).execute()
            headers = msg_data['payload']['headers']
            email_info = {
                "from": next((h['value'] for h in headers if h['name'] == 'From'), 'Unbekannt'),
                "subject": next((h['value'] for h in headers if h['name'] == 'Subject'), '(Kein Betreff)'),
                "time": datetime.datetime.fromtimestamp(
                    int(msg_data['internalDate']) / 1000).strftime('%d.%m.%Y – %H:%M')
            }
            email_list.append(email_info)
        except Exception:
            continue

    return jsonify({
        "connected": True,
        "emails": email_list
    })

# 📲 Webhook von Chatwoot empfangen
@app.route("/webhook/chatwoot", methods=["POST"])
def chatwoot_webhook():
    data = request.get_json()

    # 👉 Zeige alles schön formatiert im Terminal (für Debug)
    print("📦 Webhook-Payload:")
    print(json.dumps(data, indent=2, ensure_ascii=False))

    if data.get("event") != "message_created":
        return "Ignored", 200

    if data.get("message_type") != "incoming":
        return "Ignored", 200

    # Kontakt-Infos extrahieren
    contact = data.get("contact", {})
    contact_id = contact.get("id", "Unbekannt")
    contact_name = contact.get("name", "Unbekannt")
    contact_identifier = contact.get("identifier", "Unbekannt")

    # Du kannst hier z. B. die ID oder Identifier oder beides verwenden
    new_message = {
        "contact": f"{contact_name} ({contact_identifier})",  # oder nur identifier
        "text": data.get("content", "[Leere Nachricht]"),
        "time": data.get("created_at")
    }

    try:
        with open("chatwoot_messages.json", "r") as f:
            messages = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        messages = []

    messages.insert(0, new_message)
    messages = messages[:100]

    with open("chatwoot_messages.json", "w") as f:
        json.dump(messages, f, indent=2)

    return jsonify({"success": True})


# 📤 Letzte WhatsApp-Nachrichten abrufen
@app.route("/api/whatsapp-messages")
def whatsapp_messages():
    try:
        with open("chatwoot_messages.json", "r") as f:
            messages = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        messages = []

    return jsonify(messages[:10])

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