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
from models import db, User, Anfrage, TeamNote, GmailCredential, PdfDocument
from werkzeug.middleware.proxy_fix import ProxyFix
from base64 import urlsafe_b64encode
from werkzeug.utils import secure_filename

# 🔃 .env laden (lokal)
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "fallback")

# 📦 Persistente Ablage – Basisverzeichnis (standard: ./data)
APP_DATA_DIR = os.getenv('APP_DATA_DIR') or os.path.join(os.getcwd(), 'data')
os.makedirs(APP_DATA_DIR, exist_ok=True)

# Hinter Proxy (Railway) korrekte Host/Proto übernehmen
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config['PREFERRED_URL_SCHEME'] = 'https'

# DB-Config (SQLite default im APP_DATA_DIR, Postgres via DATABASE_URL)
database_url = os.getenv("DATABASE_URL")
if not database_url:
    sqlite_path = os.path.join(APP_DATA_DIR, 'app.db')
    database_url = f"sqlite:///{sqlite_path}"
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
SEND_SCOPES = ['https://www.googleapis.com/auth/gmail.send']
SETTINGS_SCOPES = ['https://www.googleapis.com/auth/gmail.settings.basic']

# 📬 Google OAuth Flow Builder (env or credentials.json fallback)
def build_google_flow(redirect_uri: str, state: str = None, scopes=None) -> Flow:
    use_scopes = scopes or SCOPES
    client_config_json = os.getenv('GOOGLE_CLIENT_CONFIG_JSON')
    client_config_path = os.getenv('GOOGLE_CLIENT_CONFIG_PATH') or 'credentials.json'
    # 1) Try JSON from env (robust to accidental extra quotes and double-encoded JSON)
    if client_config_json:
        def normalize_json_text(t: str) -> str:
            s = (t or '').strip()
            for _ in range(2):
                if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
                    s = s[1:-1]
            s = s.replace('\\"', '"')
            return s
        txt = normalize_json_text(client_config_json)
        for _ in range(3):
            try:
                maybe = json.loads(txt)
                if isinstance(maybe, dict):
                    return Flow.from_client_config(maybe, scopes=use_scopes, redirect_uri=redirect_uri, state=state)
                if isinstance(maybe, str):
                    txt = normalize_json_text(maybe)
                    continue
                break
            except Exception:
                break
    # 2) Try credentials file
    if os.path.exists(client_config_path):
        return Flow.from_client_secrets_file(client_config_path, scopes=use_scopes, redirect_uri=redirect_uri, state=state)
    # 3) Fallback to individual ID/SECRET
    client_id = os.getenv('GOOGLE_CLIENT_ID')
    client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
    if client_id and client_secret:
        client_config = {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs"
            }
        }
        return Flow.from_client_config(client_config, scopes=use_scopes, redirect_uri=redirect_uri, state=state)
    raise RuntimeError("Google OAuth ist nicht konfiguriert. Setze GOOGLE_CLIENT_CONFIG_JSON (als reines JSON ohne zusätzliche Anführungszeichen), oder lege credentials.json ab, oder setze GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET.")

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
        # Allow send scope if token already has it
        scopes = token_data.get('scopes') or token_data.get('_scopes') or []
        want = list({*SCOPES, *([s for s in SEND_SCOPES if any('gmail.send' in sc for sc in scopes)]), *([s for s in SETTINGS_SCOPES if any('gmail.settings' in sc for sc in scopes)])})
        return Credentials.from_authorized_user_info(token_data, want)
    except Exception:
        return None

# 📬 Gmail OAuth start
@app.route('/gmail/connect')
def gmail_connect():
    if "user" not in session:
        return redirect("/")
    try:
        # Optional: which slot (1..3) is being connected from the UI
        slot = request.args.get('slot')
        if slot in {"1", "2", "3"}:
            session['gmail_connect_slot'] = slot
        redirect_uri = url_for('gmail_callback', _external=True)
        # Request both read and send scopes to enable emailing PDFs
        requested_scopes = list({*SCOPES, *SEND_SCOPES, *SETTINGS_SCOPES})
        flow = build_google_flow(redirect_uri, scopes=requested_scopes)
        # Validate client type and redirect URI allowlist to prevent Google "invalid request"
        try:
            if getattr(flow, 'client_type', None) != 'web':
                return (
                    "Zugriff blockiert: Falscher OAuth-Clienttyp. Bitte in der Google Cloud Console einen 'Webanwendung'-OAuth-Client verwenden (nicht 'Installed').",
                    400,
                )
            allowed = (flow.client_config or {}).get('redirect_uris', [])
            if allowed and redirect_uri not in allowed:
                return (
                    f"Zugriff blockiert: Redirect URI nicht erlaubt. Füge {redirect_uri} in der Google Cloud Console unter 'Authorized redirect URIs' hinzu.",
                    400,
                )
        except Exception:
            pass
        authorization_url, state = flow.authorization_url(
            access_type='offline', include_granted_scopes='true', prompt='consent'
        )
        session['oauth_state'] = state
        # Persist PKCE code_verifier for the callback token exchange
        if getattr(flow, 'code_verifier', None):
            session['oauth_code_verifier'] = flow.code_verifier
        return redirect(authorization_url)
    except Exception as e:
        return f"Google OAuth Fehler ({url_for('gmail_callback', _external=True)}): {str(e)}", 400

# 📬 Gmail OAuth callback
@app.route('/gmail/callback')
def gmail_callback():
    if "user" not in session:
        return redirect("/")
    # optional: validate state
    expected_state = session.get('oauth_state')
    incoming_state = request.args.get('state')
    if expected_state and incoming_state and expected_state != incoming_state:
        return "Ungültiger OAuth-Status (state mismatch)", 400
    redirect_uri = url_for('gmail_callback', _external=True)
    try:
        # Recreate flow with same state and same scopes as initial request (read + send)
        requested_scopes = list({*SCOPES, *SEND_SCOPES, *SETTINGS_SCOPES})
        flow = build_google_flow(redirect_uri, state=expected_state, scopes=requested_scopes)
        code_verifier = session.get('oauth_code_verifier')
        if code_verifier:
            flow.code_verifier = code_verifier
        flow.fetch_token(authorization_response=request.url)
    except Exception as e:
        return f"Google OAuth Fehler: {str(e)}", 400
    creds: Credentials = flow.credentials
    token_json = creds.to_json()

    entry = GmailCredential(username=session.get('user'), token_json=token_json)
    db.session.add(entry)
    db.session.commit()
    session.pop('oauth_state', None)
    session.pop('oauth_code_verifier', None)
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

# 📄 PDF Ablage – Konfiguration
UPLOAD_FOLDER = os.getenv('PDF_UPLOAD_DIR') or os.path.join(APP_DATA_DIR, 'uploaded_pdfs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_PDF_EXTENSIONS = {'.pdf'}

def _is_pdf_filename(name: str) -> bool:
    _, ext = os.path.splitext(name.lower())
    return ext in ALLOWED_PDF_EXTENSIONS

# 📄 Liste der PDFs
@app.route('/api/pdfs', methods=['GET'])
def list_pdfs():
    if "user" not in session:
        return jsonify({"error": "Nicht eingeloggt"}), 401
    docs = PdfDocument.query.order_by(PdfDocument.id.desc()).limit(500).all()
    return jsonify([d.to_dict() for d in docs])

# 📄 PDF hochladen
@app.route('/api/pdfs', methods=['POST'])
def upload_pdf():
    if "user" not in session:
        return jsonify({"error": "Nicht eingeloggt"}), 401
    if 'file' not in request.files:
        return jsonify({"error": "Keine Datei übermittelt"}), 400
    f = request.files['file']
    if not f or not f.filename:
        return jsonify({"error": "Ungültige Datei"}), 400
    original = secure_filename(f.filename)
    if not _is_pdf_filename(original):
        return jsonify({"error": "Nur PDF-Dateien sind erlaubt"}), 400
    unique = uuid.uuid4().hex + '.pdf'
    path = os.path.join(UPLOAD_FOLDER, unique)
    f.save(path)
    doc = PdfDocument(filename=original, stored_filename=unique, uploaded_by=session.get('user'))
    db.session.add(doc)
    db.session.commit()
    return jsonify(doc.to_dict()), 201

# 📄 PDF herunterladen
@app.route('/api/pdfs/<int:doc_id>', methods=['GET'])
def download_pdf(doc_id: int):
    if "user" not in session:
        return jsonify({"error": "Nicht eingeloggt"}), 401
    doc = PdfDocument.query.get(doc_id)
    if not doc:
        return jsonify({"error": "Nicht gefunden"}), 404
    path = os.path.join(UPLOAD_FOLDER, doc.stored_filename)
    if not os.path.exists(path):
        return jsonify({"error": "Datei fehlt auf dem Server"}), 410
    from flask import send_file
    return send_file(path, as_attachment=True, download_name=doc.filename, mimetype='application/pdf')

# 📧 Gmail API
@app.route("/api/emails")
def get_emails():
    if "user" not in session:
        return jsonify({"error": "Nicht eingeloggt"}), 401
    # Support 3 slots by selecting the Nth most recent credential for the user
    # slot=1 -> most recent, slot=2 -> second, slot=3 -> third
    slot_param = request.args.get('slot', '1')
    try:
        slot_index = int(slot_param)
    except Exception:
        slot_index = 1
    if slot_index < 1:
        slot_index = 1
    if slot_index > 3:
        slot_index = 3

    try:
        cred_row = (
            GmailCredential.query
            .filter_by(username=session['user'])
            .order_by(GmailCredential.id.desc())
            .offset(slot_index - 1)
            .first()
        )
        if not cred_row:
            return jsonify({"error": "Kein Gmail-Konto für diesen Slot verbunden. Bitte verbinden."}), 400
        token_data = json.loads(cred_row.token_json)
        creds = Credentials.from_authorized_user_info(token_data, SCOPES)
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

# 📧 Angebot per E-Mail versenden (PDF Base64)
@app.route('/api/send-offer', methods=['POST'])
def send_offer():
    if "user" not in session:
        return jsonify({"error": "Nicht eingeloggt"}), 401
    data = request.get_json() or {}
    to_email = data.get('to')
    # Subject/Body defaults (keeps your latest wording) with last name interpolation
    name_full = (data.get('sms_name') or '').strip()
    last_name = (data.get('lastName') or (name_full.split()[-1] if name_full else '')).strip()
    subject = data.get('subject') or "Ihr unverbindliches Angebot"
    body = data.get('body') or (
        f"Sehr geehrte Familie {last_name},\n\n"
        "vielen Dank für das freundliche Gespräch. Wie vereinbart, übersende ich Ihnen im Anhang unser Angebot.\n\n"
        "Sollten Sie noch Fragen haben oder weitere Details benötigen, stehe ich Ihnen gerne zur Verfügung.\n\n"
        "Mit besten Grüßen  \n"
        "Team HelpCare  \n\n"
    )

    pdf_b64 = data.get('pdf_base64')
    filename = data.get('filename') or 'Angebot.pdf'
    sms_number = data.get('sms_number')
    sms_name = data.get('sms_name')
    sms_info_email = to_email
    if not to_email or not pdf_b64:
        return jsonify({"error": "to und pdf_base64 erforderlich"}), 400

    # Load credentials with send scope if possible
    creds = load_user_gmail_credentials(session['user'])
    if not creds:
        return jsonify({"error": "Kein Gmail-Konto verbunden."}), 400
    try:
        service = build('gmail', 'v1', credentials=creds)

        # Try to fetch Gmail signature (HTML) and append to message
        signature_html = None
        try:
            settings = service.users().settings().sendAs().list(userId='me').execute() or {}
            send_as_list = settings.get('sendAs', []) or []
            primary = None
            for sa in send_as_list:
                if sa.get('isPrimary') or sa.get('isDefault'):
                    primary = sa
                    break
            if not primary and send_as_list:
                primary = send_as_list[0]
            if primary:
                signature_html = (primary.get('signature') or '').strip() or None
        except Exception:
            signature_html = None

        # Prepare text and HTML bodies
        def html_to_text(html:str) -> str:
            try:
                # basic tag replacement for line breaks
                repl = html.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')
                import re
                repl = re.sub(r'<[^>]+>', '', repl)
                # unescape common entities
                repl = repl.replace('&nbsp;', ' ').replace('&amp;', '&')
                return repl
            except Exception:
                return ''

        body_text_final = body
        body_html_final = (
            "<div style=\"font-family:Arial,Helvetica,sans-serif;white-space:pre-wrap\">" +
            (body or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;') +
            "</div>"
        )
        if signature_html:
            sig_text = html_to_text(signature_html).strip()
            if sig_text:
                body_text_final = body_text_final.rstrip() + "\n\n" + sig_text
            body_html_final = body_html_final + "<div>" + signature_html + "</div>"

        # Build MIME message with multipart/alternative (text + HTML) and PDF attachment
        mixed_boundary = 'mixed_boundary'
        alt_boundary = 'alt_boundary'
        message_parts = []
        message_parts.append(f"Content-Type: multipart/mixed; boundary={mixed_boundary}\r\n")
        message_parts.append(f"MIME-Version: 1.0\r\n")
        message_parts.append(f"to: {to_email}\r\n")
        message_parts.append(f"subject: {subject}\r\n\r\n")
        # Alternative part (text + HTML)
        message_parts.append(f"--{mixed_boundary}\r\n")
        message_parts.append(f"Content-Type: multipart/alternative; boundary={alt_boundary}\r\n\r\n")
        # Text
        message_parts.append(f"--{alt_boundary}\r\n")
        message_parts.append("Content-Type: text/plain; charset=UTF-8\r\n\r\n")
        message_parts.append(body_text_final + "\r\n\r\n")
        # HTML
        message_parts.append(f"--{alt_boundary}\r\n")
        message_parts.append("Content-Type: text/html; charset=UTF-8\r\n\r\n")
        message_parts.append(body_html_final + "\r\n\r\n")
        # End alternative
        message_parts.append(f"--{alt_boundary}--\r\n")
        # Attachment part
        message_parts.append(f"--{mixed_boundary}\r\n")
        message_parts.append(f"Content-Type: application/pdf; name={filename}\r\n")
        message_parts.append("Content-Transfer-Encoding: base64\r\n")
        message_parts.append(f"Content-Disposition: attachment; filename={filename}\r\n\r\n")
        # Strip header if present
        if pdf_b64.startswith('data:application/pdf;base64,'):
            pdf_b64 = pdf_b64.split(',', 1)[1]
        message_parts.append(pdf_b64 + "\r\n")
        message_parts.append(f"--{mixed_boundary}--")
        raw_message = ''.join(message_parts).encode('utf-8')
        raw = urlsafe_b64encode(raw_message).decode('utf-8')
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        # Optional: Send SMS via Link Mobility if number present
        if sms_number:
            try:
                linkmobility_token = os.getenv('LINKMOBILITY_TOKEN') or 'bb2d6280-fbfe-4b73-9421-b2ca7a76c896'
                link_base = os.getenv('LINKMOBILITY_BASE_URL') or 'https://api.linkmobility.eu/rest/smsmessaging/simple'
                # E.164 normalize (Germany default)
                num = ''.join([c for c in (sms_number or '') if c.isdigit() or c=='+'])
                num = num.replace('+','')
                if num.startswith('00'):
                    num = num[2:]
                if num.startswith('0'):
                    num = '49' + num[1:]
                if not num.startswith('49'):
                    # keep as-is or extend mapping for other countries
                    pass
                recipient = '+' + num
                customer_message = (
                    f"Herzlich Willkommen {sms_name or ''},\n\n"
                    "Wir danken Ihnen für Ihr Vertrauen,\n"
                    "dass wir Sie bei Ihrer Suche nach\n"
                    "einer passenden 24 Stunden\n"
                    "Betreuungskraft unterstützen dürfen.\n\n"
                    f"Ihr persönliches Angebot wurde per\nE-Mail an: {sms_info_email}\nzugestellt.\n\n"
                    "Bitte prüfen Sie auch Ihren\n"
                    "Spam-Ordner, falls Sie unsere\n"
                    "E-Mail nicht im Posteingang finden.\n\n"
                    "Für Fragen erreichen Sie uns jederzeit\n"
                    "kostenlos unter 0800 000 9178.\n\n"
                    "Beste Grüße\nIhr HelpCare Team"
                )
                payload = {
                    'access_token': linkmobility_token,
                    'recipientAddressList': recipient,
                    'messageContent': customer_message,
                }
                import requests as _requests
                _requests.post(link_base, data=payload, headers={'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'}, timeout=30)
            except Exception:
                pass
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": f"Senden fehlgeschlagen: {str(e)}"}), 500

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
        with open(os.path.join(APP_DATA_DIR, "chatwoot_messages.json"), "r") as f:
            messages = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        messages = []

    messages.insert(0, new_message)
    messages = messages[:100]

    with open(os.path.join(APP_DATA_DIR, "chatwoot_messages.json"), "w") as f:
        json.dump(messages, f, indent=2)

    return jsonify({"success": True})


# 📤 Letzte WhatsApp-Nachrichten abrufen
@app.route("/api/whatsapp-messages")
def whatsapp_messages():
    try:
        with open(os.path.join(APP_DATA_DIR, "chatwoot_messages.json"), "r") as f:
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
    # Only the author may delete their own notes
    current_user = session.get('user')
    if not current_user or (note.author and note.author != current_user):
        return jsonify({"error": "Keine Berechtigung zum Löschen dieser Notiz"}), 403
    db.session.delete(note)
    db.session.commit()
    return jsonify({"success": True})

# 🔓 Logout
@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/")

def _mask(value: str, show: int = 6) -> str:
    if not value:
        return ""
    if len(value) <= show:
        return value
    return value[:show] + "…"

@app.route("/debug/oauth")
def debug_oauth():
    if "user" not in session:
        return redirect("/")
    redirect_uri = url_for('gmail_callback', _external=True)
    info = {"redirect_uri": redirect_uri}

    # Try env JSON
    src = None
    raw = os.getenv('GOOGLE_CLIENT_CONFIG_JSON')
    if raw:
        txt = raw.strip()
        try:
            cfg = json.loads(txt)
            src = "env_json"
        except Exception:
            try:
                if (txt.startswith('"') and txt.endswith('"')) or (txt.startswith("'") and txt.endswith("'")):
                    txt = txt[1:-1]
                txt = txt.replace('\\"', '"')
                cfg = json.loads(txt)
                src = "env_json_unquoted"
            except Exception:
                cfg = None
        if cfg:
            web = cfg.get('web', {})
            info.update({
                "source": src,
                "client_id": _mask(web.get('client_id', '')),
                "has_client_secret": bool(web.get('client_secret')),
                "authorized_redirect_uris": web.get('redirect_uris', [])
            })
            return jsonify(info)

    # Try file
    path = os.getenv('GOOGLE_CLIENT_CONFIG_PATH') or 'credentials.json'
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                cfg = json.load(f)
            web = cfg.get('web', {})
            info.update({
                "source": f"file:{path}",
                "client_id": _mask(web.get('client_id', '')),
                "has_client_secret": bool(web.get('client_secret')),
                "authorized_redirect_uris": web.get('redirect_uris', [])
            })
            return jsonify(info)
        except Exception as e:
            info.update({"source": f"file:{path}", "error": str(e)})
            return jsonify(info), 200

    # Fallback to id/secret
    client_id = os.getenv('GOOGLE_CLIENT_ID')
    client_secret = os.getenv('GOOGLE_CLIENT_SECRET')
    if client_id and client_secret:
        info.update({
            "source": "env_id_secret",
            "client_id": _mask(client_id),
            "has_client_secret": True,
            "authorized_redirect_uris": ["(configured in Google Cloud console)"]
        })
        return jsonify(info)

    info.update({
        "source": "none",
        "error": "No Google OAuth config found"
    })
    return jsonify(info), 200

# ▶️ Nur lokal öffnen
if __name__ == "__main__":
    import webbrowser, threading
    threading.Timer(1.5, lambda: webbrowser.open_new("http://127.0.0.1:5000")).start()
    app.run(debug=True)
