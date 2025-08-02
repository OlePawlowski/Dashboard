from flask import Flask, jsonify
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import os
import datetime

app = Flask(__name__)
TOKEN_PATH = "token.json"

@app.route('/api/emails')
def get_emails():
    creds = Credentials.from_authorized_user_file(
        TOKEN_PATH, ['https://www.googleapis.com/auth/gmail.readonly']
    )
    service = build('gmail', 'v1', credentials=creds)

    results = service.users().messages().list(userId='me', maxResults=5).execute()
    messages = results.get('messages', [])

    email_list = []
    for msg in messages:
        msg_data = service.users().messages().get(userId='me', id=msg['id']).execute()
        headers = msg_data['payload']['headers']

        email_info = {
            'from': next((h['value'] for h in headers if h['name'] == 'From'), 'Unbekannt'),
            'subject': next((h['value'] for h in headers if h['name'] == 'Subject'), '(Kein Betreff)'),
            'time': datetime.datetime.fromtimestamp(
                int(msg_data['internalDate']) / 1000
            ).strftime('%d.%m.%Y – %H:%M')
        }
        email_list.append(email_info)

    return jsonify(email_list)

if __name__ == '__main__':
    app.run(debug=True)
