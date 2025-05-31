import os
import base64
from email.message import EmailMessage
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
import io
import logging

# Scopes for Gmail send and Drive file access
SCOPES = [
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/drive',
]

def _load_credentials(scopes, token_path='token.json', creds_path='credentials.json'):
    try:
        creds = None

        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, scopes)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    with open(token_path, 'w') as token:
                        token.write(creds.to_json())
                except Exception as e:
                    logging.warning("Refresh failed. Removing invalid token file.")
                    os.remove(token_path)
                    return _load_credentials(scopes, token_path, creds_path)  # retry
            else:
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, scopes)
                creds = flow.run_local_server(port=8080)
                with open(token_path, 'w') as token:
                    token.write(creds.to_json())

        return creds

    except Exception as e:
        logging.error(f"OAuth credential error: {e}")


def send_verification_email(to_email, subject, body_text):
    creds = _load_credentials(SCOPES)
    service = build('gmail', 'v1', credentials=creds)

    message = EmailMessage()
    message.set_content(body_text)
    message['To'] = to_email
    message['From'] = 'me'
    message['Subject'] = subject

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    message_body = {'raw': raw_message}

    sent_message = service.users().messages().send(userId='me', body=message_body).execute()
    return sent_message

def upload_blob(local_path="instance/discoursesecure.db", remote_name="discoursesecure.db"):
    creds = _load_credentials(SCOPES)
    service = build('drive', 'v3', credentials=creds)

    if not os.path.exists(local_path):
        logging.error(f"Local file {local_path} does NOT exist!")
        return

    query = f"name='{remote_name}' and trashed=false"
    try:
        results = service.files().list(q=query, spaces='drive', fields="files(id, name)").execute()
        files = results.get('files', [])
        media = MediaFileUpload(local_path, resumable=True)

        if files:
            file_id = files[0]['id']
            logging.info(f"Found existing file '{remote_name}' with ID {file_id}, updating...")
            updated_file = service.files().update(fileId=file_id, media_body=media).execute()
            logging.info(f"File updated: {updated_file}")
            return updated_file
        else:
            logging.info(f"No existing file named '{remote_name}', uploading new...")
            file_metadata = {'name': remote_name}
            new_file = service.files().create(body=file_metadata, media_body=media).execute()
            logging.info(f"File uploaded: {new_file}")
            return new_file

    except Exception as e:
        logging.error(f"Error during upload: {e}")
        return None

def download_blob(local_path="instance/discoursesecure.db", remote_name="discoursesecure.db"):
    creds = _load_credentials(SCOPES)
    service = build('drive', 'v3', credentials=creds)

    query = f"name='{remote_name}' and trashed=false"
    results = service.files().list(q=query, spaces='drive', fields="files(id, name)").execute()
    files = results.get('files', [])
    if not files:
        print(f"No file named {remote_name} found in Drive.")
        return

    file_id = files[0]['id']

    request = service.files().get_media(fileId=file_id)
    with io.FileIO(local_path, 'wb') as fh:
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            status, done = downloader.next_chunk()
            # Optional: print progress
            # print(f"Download {int(status.progress() * 100)}%.")
