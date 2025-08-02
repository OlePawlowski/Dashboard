from flask import Flask, render_template, request, redirect, session, jsonify, url_for
from flask_cors import CORS
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import os
import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "fallback")

CORS(app)

USERS = {
    os.getenv("USER_1_NAME", "Ole"): os.getenv("USER_1_PASS", "Helpcare2025!"),
    os.getenv("USER_2_NAME", "partner"): os.getenv("USER_2_PASS", "Helpcare2025!")
}

TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "token.json")
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']


# 🔐 LOGIN-SEITE
@app.route("/api/anfrage", methods=["POST"])
def neue_anfrage():
    if "user" not in session:
        return jsonify({"error": "Nicht eingeloggt"}), 401

    data = request.get_json()
    if not data:
        return jsonify({"error": "Ungültige Daten"}), 400

    # Temporär in globaler Liste speichern (oder später DB etc.)
    if "anfragen" not in session:
        session["anfragen"] = []
    anfragen = session["anfragen"]
    anfragen.insert(0, data)  # Neueste oben
    session["anfragen"] = anfragen

    return jsonify({"success": True})

@app.route("/api/get-anfragen")
def get_anfragen():
    if "user" not in session:
        return jsonify([])
    return jsonify(session.get("anfragen", []))


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if USERS.get(username) == password:
            session["user"] = username
            return redirect("/dashboard")
        else:
            return "❌ Falscher Login", 401
    return render_template("login.html")  # Richtig gerendertes HTML

# 📊 DASHBOARD
@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/")
    return render_template("index.html")

# 📬 E-MAIL API
@app.route("/api/emails")
def get_emails():
    if "user" not in session:
        return jsonify({"error": "Nicht eingeloggt"}), 401

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    service = build('gmail', 'v1', credentials=creds)

    results = service.users().messages().list(userId='me', maxResults=5).execute()
    messages = results.get('messages', [])

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

# 🔓 LOGOUT
@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/")

# ▶️ APP STARTEN + automatisch Browser öffnen
if __name__ == "__main__":
    import webbrowser
    import threading

    def open_browser():
        webbrowser.open_new("http://127.0.0.1:5000")

    threading.Timer(1.5, open_browser).start()
    app.run(debug=True)
