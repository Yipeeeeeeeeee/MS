from flask import Flask, request
import os
import json
import traceback
import gspread
from yt_dlp import YoutubeDL
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.service_account import Credentials

app = Flask(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]

def get_credentials():
    service_account_info = json.loads(os.environ['SERVICE_ACCOUNT_JSON'])
    creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    return creds

@app.route('/', methods=['POST'])
def run_script():
    try:
        creds = get_credentials()
        gc = gspread.authorize(creds)
        sheet = gc.open("SS").sheet1
        drive_service = build('drive', 'v3', credentials=creds)

        # Get trigger command from D1
        command = sheet.acell("D1").value.strip().lower()

        if command == "purge":
            deleted = purge_all_audio_files(drive_service)
            sheet.update_acell("D2", f"🧹 Purged {deleted} audio file(s)")
            sheet.update_acell("D1", "Done")
            return f"Purged {deleted} audio file(s)", 200

        # Normal mode: download/upload
        rows = sheet.get_all_values()

        for i, row in enumerate(rows[1:], start=2):
            try:
                artist, title, link = (row + ["", "", ""])[:3]

                if link.strip():
                    continue

                if not artist or not title:
                    sheet.update_cell(i, 3, "⚠️ Missing artist/title")
                    continue

                query = f"{artist} - {title}"
                filename_base = query.replace("/", "-").replace("\\", "-")
                filepath = f'/tmp/{filename_base}.%(ext)s'

                sheet.update_cell(i, 3, "🔄 Downloading...")

                ydl_opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': filepath,
                    'noplaylist': True,
                    'quiet': True,
                    'ffmpeg_location': '/usr/bin/ffmpeg',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '5'
                    }],
                }

                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(f"ytsearch1:{query}", download=True)
                    entry = info['entries'][0]
                    dl_filename = ydl.prepare_filename(entry)

                # Look for downloaded audio file
                filename = None
                for ext in [".mp3", ".m4a", ".webm", ".mp4"]:
                    test_file = os.path.splitext(dl_filename)[0] + ext
                    if os.path.exists(test_file):
                        filename = test_file
                        break

                if not filename:
                    sheet.update_cell(i, 3, "❌ File not found after download")
                    continue

                sheet.update_cell(i, 3, "⬆️ Uploading to Drive...")

                file_metadata = {'name': os.path.basename(filename), 'mimeType': 'audio/mpeg'}
                media = MediaFileUpload(filename, mimetype='audio/mpeg')
                file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

                drive_service.permissions().create(
                    fileId=file['id'],
                    body={'role': 'reader', 'type': 'anyone'}
                ).execute()

                link = f"https://drive.google.com/file/d/{file['id']}/view"
                sheet.update_cell(i, 3, link)

                os.remove(filename)

            except Exception as e:
                sheet.update_cell(i, 3, f"❌ Error: {str(e).splitlines()[0]}")
                traceback.print_exc()
                continue

        return '✅ Process completed', 200

    except Exception as e:
        print("Critical Error:", e)
        traceback.print_exc()
        return f"❌ Critical Error: {e}", 500

def purge_all_audio_files(drive_service):
    deleted = 0
    page_token = None
    query = "mimeType contains 'audio/'"
    while True:
        response = drive_service.files().list(q=query, spaces='drive', fields='nextPageToken, files(id, name)', pageToken=page_token).execute()
        for file in response.get('files', []):
            drive_service.files().delete(fileId=file['id']).execute()
            deleted += 1
        page_token = response.get('nextPageToken', None)
        if not page_token:
            break
    return deleted

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=81)
