from flask import Flask, render_template, request, redirect, session, jsonify, url_for
from flask_cors import CORS
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import Flow
import os
import datetime
from dotenv import load_dotenv
import json
import requests
import uuid
from models import db, User, Anfrage, TeamNote, GmailCredential
from werkzeug.middleware.proxy_fix import ProxyFix

# 🔃 .env laden (lokal)
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "fallback")

# Hinter Proxy (Railway) korrekte Host/Proto übernehmen
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config['PREFERRED_URL_SCHEME'] = 'https'

# DB-Config (SQLite default, Postgres via DATABASE_URL)
database_url = os.getenv("DATABASE_URL", "sqlite:///app.db")
# Heroku-Style postgres:// → postgresql://
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

CORS(app)

db.init_app(app)

with app.app_context():
    db.create_all()

# 👥 Benutzer (Session-basierter Zugang für aktuelles Template)
USERS = {}
for i in range(1, 4):
    name = os.getenv(f"USER_{i}_NAME")
    pw = os.getenv(f"USER_{i}_PASS")
    if name and pw:
        USERS[name] = pw

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

# 📬 E-Mail Auth über Environment (Fallback)
def load_credentials_from_env():
    token_str = os.getenv("TOKEN_JSON")
    if not token_str:
        raise Exception("TOKEN_JSON nicht gesetzt")
    token_data = json.loads(token_str)
    return Credentials.from_authorized_user_info(token_data, SCOPES)

# 📬 Nutzerbezogene Gmail-Creds
def load_user_gmail_credentials(username: str):
    cred = GmailCredential.query.filter_by(username=username).order_by(GmailCredential.id.desc()).first()
    if not cred:
        return None
    try:
        token_data = json.loads(cred.token_json)
        return Credentials.from_authorized_user_info(token_data, SCOPES)
    except Exception:
        return None

# 📬 Gmail OAuth start
@app.route('/gmail/connect')
def gmail_connect():
    if "user" not in session:
        return redirect("/")
    client_config_json = os.getenv('GOOGLE_CLIENT_CONFIG_JSON')
    if not client_config_json:
        return "Fehlende GOOGLE_CLIENT_CONFIG_JSON in .env", 500
    client_config = json.loads(client_config_json)
    redirect_uri = url_for('gmail_callback', _external=True)
    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)
    authorization_url, state = flow.authorization_url(
        access_type='offline', include_granted_scopes='true', prompt='consent'
    )
    session['oauth_state'] = state
    return redirect(authorization_url)

# 📬 Gmail OAuth callback
@app.route('/gmail/callback')
def gmail_callback():
    if "user" not in session:
        return redirect("/")
    client_config_json = os.getenv('GOOGLE_CLIENT_CONFIG_JSON')
    if not client_config_json:
        return "Fehlende GOOGLE_CLIENT_CONFIG_JSON in .env", 500
    client_config = json.loads(client_config_json)
    redirect_uri = url_for('gmail_callback', _external=True)
    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)
    flow.fetch_token(authorization_response=request.url)
    creds: Credentials = flow.credentials
    token_json = creds.to_json()

    entry = GmailCredential(username=session.get('user'), token_json=token_json)
    db.session.add(entry)
    db.session.commit()
    return redirect('/dashboard')

# 📥 Anfrage empfangen (extern)
@app.route("/api/externe-anfrage", methods=["POST"])
def externe_anfrage():
    data = request.get_json() or {}
    name = data.get("name")
    tel = data.get("tel")
    if not name:
        return jsonify({"error": "Ungültige Daten"}), 400
    anfrage = Anfrage(name=name, tel=tel)
    db.session.add(anfrage)
    db.session.commit()
    return jsonify({"success": True, "id": anfrage.id})

# 📥 Anfrage anlegen (intern)
@app.route("/api/anfrage", methods=["POST"])
def neue_anfrage():
    data = request.get_json() or {}
    name = data.get("name")
    tel = data.get("tel")
    if not name:
        return jsonify({"error": "Ungültige Daten"}), 400
    anfrage = Anfrage(name=name, tel=tel)
    db.session.add(anfrage)
    db.session.commit()
    return jsonify({"success": True, "id": anfrage.id})

@app.route("/api/get-anfragen")
def get_anfragen():
    anfragen = Anfrage.query.order_by(Anfrage.id.desc()).limit(100).all()
    return jsonify([a.to_dict() for a in anfragen])

# 🔐 Login (Session)
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
    aktuelles_datum = datetime.datetime.now().strftime('%A, %d. %B %Y')
    username = session.get("user")
    return render_template("index.html", aktuelles_datum=aktuelles_datum, username=username)

# 📧 Gmail API
@app.route("/api/emails")
def get_emails():
    if "user" not in session:
        return jsonify({"error": "Nicht eingeloggt"}), 401

    try:
        creds = load_user_gmail_credentials(session['user'])
        if not creds:
            return jsonify({"error": "Kein Gmail-Konto verbunden. Bitte zuerst verbinden."}), 400
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

# 👤 Aktueller Nutzer (Session)
@app.route("/api/me")
def who_am_i():
    if "user" not in session:
        return jsonify({"authenticated": False}), 200
    return jsonify({"authenticated": True, "username": session["user"]})

# 👥 Mitarbeiter-API (DB-gestützt)
@app.route("/api/users", methods=["GET", "POST"])
def users_api():
    if "user" not in session:
        return jsonify({"error": "Nicht eingeloggt"}), 401

    if request.method == "GET":
        users = User.query.order_by(User.id.desc()).all()
        return jsonify([u.to_public_dict() for u in users])

    # POST anlegen
    payload = request.get_json() or {}
    name = payload.get("name")
    email = payload.get("email")
    role = payload.get("role") or "employee"
    avatar = payload.get("avatar")

    if not name or not email:
        return jsonify({"error": "name und email sind erforderlich"}), 400

    username = payload.get("username") or name.lower().replace(" ", ".")
    # Generisches Initialpasswort (sollte via Reset-Flow geändert werden)
    initial_password = payload.get("password") or uuid.uuid4().hex[:10]

    user = User(username=username, email=email, role=role, avatar=avatar or (
        "https://ui-avatars.com/api/?name=" + name.replace(" ", "+")
    ))
    user.set_password(initial_password)
    db.session.add(user)
    db.session.commit()

    response = user.to_public_dict()
    response.update({"initial_password": initial_password})
    return jsonify(response), 201

# 🗒️ Teamnotizen API
@app.route('/api/team-notes', methods=['GET', 'POST'])
def team_notes():
    if "user" not in session:
        return jsonify({"error": "Nicht eingeloggt"}), 401

    if request.method == 'GET':
        notes = TeamNote.query.order_by(TeamNote.id.desc()).limit(200).all()
        return jsonify([n.to_dict() for n in notes])

    payload = request.get_json() or {}
    content = payload.get('content')
    if not content:
        return jsonify({"error": "content erforderlich"}), 400
    author = session.get('user')
    note = TeamNote(content=content, author=author)
    db.session.add(note)
    db.session.commit()
    return jsonify(note.to_dict()), 201

@app.route('/api/team-notes/<int:note_id>', methods=['DELETE'])
def delete_team_note(note_id: int):
    if "user" not in session:
        return jsonify({"error": "Nicht eingeloggt"}), 401
    note = TeamNote.query.get(note_id)
    if not note:
        return jsonify({"error": "Notiz nicht gefunden"}), 404
    db.session.delete(note)
    db.session.commit()
    return jsonify({"success": True})

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