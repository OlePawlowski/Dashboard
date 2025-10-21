from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash


db = SQLAlchemy()


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default='employee', nullable=False)  # 'admin' | 'manager' | 'employee'
    avatar = db.Column(db.String(512), nullable=True)
    must_reset_password = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    def to_public_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'role': self.role,
            'avatar': self.avatar,
            'created_at': self.created_at.isoformat()
        }


class Anfrage(db.Model):
    __tablename__ = 'anfragen'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    tel = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'tel': self.tel,
            'created_at': self.created_at.isoformat(),
        }


class TeamNote(db.Model):
    __tablename__ = 'team_notes'

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    author = db.Column(db.String(120), nullable=True)  # optional: Nutzername aus Session
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Neu: Threading & Reaktionen
    parent_id = db.Column(db.Integer, db.ForeignKey('team_notes.id'), nullable=True)
    reactions_json = db.Column(db.Text, nullable=True, default='[]')

    def to_dict(self):
        data = {
            'id': self.id,
            'content': self.content,
            'author': self.author,
            'created_at': self.created_at.isoformat(),
        }
        # Optional-Felder sicher anhängen (falls Spalten in älteren DBs fehlen)
        try:
            data['parent_id'] = getattr(self, 'parent_id', None)
        except Exception:
            data['parent_id'] = None
        try:
            data['reactions'] = getattr(self, 'reactions_json', '[]')
        except Exception:
            data['reactions'] = '[]'
        return data


class GmailCredential(db.Model):
    __tablename__ = 'gmail_credentials'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    username = db.Column(db.String(80), nullable=True, index=True)
    token_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Kooperationspartner(db.Model):
    __tablename__ = 'kooperationspartner'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }

class Customer(db.Model):
    __tablename__ = 'customers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(64), nullable=True)
    company = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_contact = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text, nullable=True)
    
    # Angebot-Variablen (JSON für Flexibilität)
    offer_data_json = db.Column(db.Text, nullable=True, default='{}')
    
    # Befragungsbogen-Daten
    questionnaire_data_json = db.Column(db.Text, nullable=True, default='{}')
    
    # Kontakthistorie
    contact_history_json = db.Column(db.Text, nullable=True, default='[]')

    def to_dict(self):
        import json
        data = {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'company': self.company,
            'created_at': self.created_at.isoformat(),
            'last_contact': self.last_contact.isoformat(),
            'notes': self.notes
        }
        
        # JSON-Felder sicher parsen
        try:
            data['offer_data'] = json.loads(self.offer_data_json or '{}')
        except:
            data['offer_data'] = {}
            
        try:
            data['questionnaire_data'] = json.loads(self.questionnaire_data_json or '{}')
        except:
            data['questionnaire_data'] = {}
            
        try:
            data['contact_history'] = json.loads(self.contact_history_json or '[]')
        except:
            data['contact_history'] = []
            
        return data
    
    def add_contact_entry(self, contact_type, details=None):
        """Fügt einen neuen Kontakteintrag hinzu"""
        import json
        try:
            history = json.loads(self.contact_history_json or '[]')
        except:
            history = []
            
        entry = {
            'type': contact_type,  # 'offer_sent', 'questionnaire_sent', 'manual_note'
            'timestamp': datetime.utcnow().isoformat(),
            'details': details or {}
        }
        
        history.append(entry)
        self.contact_history_json = json.dumps(history)
        self.last_contact = datetime.utcnow()


class PdfDocument(db.Model):
    __tablename__ = 'pdf_documents'

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), unique=True, nullable=False)
    uploaded_by = db.Column(db.String(80), nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'filename': self.filename,
            'uploaded_by': self.uploaded_by,
            'uploaded_at': self.uploaded_at.isoformat()
        }


class Caregiver(db.Model):
    __tablename__ = 'caregivers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text, nullable=True)
    # Signierte Verträge (letzter/aktueller) – flexibel per JSON
    contract_data_json = db.Column(db.Text, nullable=True, default='{}')

    def to_dict(self):
        import json
        try:
            contract_data = json.loads(self.contract_data_json or '{}')
        except Exception:
            contract_data = {}
        return {
            'id': self.id,
            'name': self.name,
            'email': self.email,
            'phone': self.phone,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'notes': self.notes,
            'contract_data': contract_data,
        }