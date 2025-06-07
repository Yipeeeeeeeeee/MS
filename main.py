from flask import Flask, request
import os
import json
import traceback
import gspread
from threading import Thread
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


@app.route('/ping', methods=['GET'])
def ping():
    return "pong", 200


@app.route('/', methods=['POST'])
def trigger_script():
    Thread(target=run_background_task).start()
    return '🕒 Started background task', 200


def run_background_task():
    try:
        creds = get_credentials()
        gc = gspread.authorize(creds)
        sheet = gc.open("SS").sheet1
        drive_service = build('drive', 'v3', credentials=creds)

        rows = sheet.get_all_values()

        for i, row in enumerate(rows[1:], start=2):  # Skip header
            try:
                artist, title, link = (row + ["", "", ""])[:3]

                if link.strip():
                    continue  # Skip if already has a link

                if not artist or not title:
                    sheet.update_cell(i, 3, "⚠️ Missing artist/title")
                    continue

                query = f"{artist} - {title}"
                filename_base = query.replace("/", "-").replace("\\", "-")
                filepath = f"/tmp/{filename_base}.%(ext)s"

                sheet.update_cell(i, 3, "🔄 Downloading...")

                ydl_opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': filepath,
                    'noplaylist': True,
                    'quiet': True,
                    'ffmpeg_location': '/nix/store/3zc5jbvqzrn8zmva4fx5p0nh4yy03wk4-ffmpeg-6.1.1-bin/bin',
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': 'mp3',
                        'preferredquality': '5'
                    }]
                }

                with YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(f"ytsearch1:{query}", download=True)
                    entry = info['entries'][0]
                    dl_filename = ydl.prepare_filename(entry)

                # Find the file
                filename = None
                for ext in [".mp3", ".m4a", ".webm", ".mp4"]:
                    test_file = os.path.splitext(dl_filename)[0] + ext
                    if os.path.exists(test_file):
                        filename = test_file
                        break

                if not filename:
                    sheet.update_cell(i, 3, "❌ File not found")
                    continue

                sheet.update_cell(i, 3, "⬆️ Uploading...")

                file_metadata = {
                    'name': os.path.basename(filename),
                    'mimeType': 'audio/mpeg'
                }
                media = MediaFileUpload(filename, mimetype='audio/mpeg')
                file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

                # Make public
                drive_service.permissions().create(
                    fileId=file['id'],
                    body={'role': 'reader', 'type': 'anyone'}
                ).execute()

                link = f"https://drive.google.com/file/d/{file['id']}/view"
                sheet.update_cell(i, 3, link)

                # Cleanup
                drive_service.files().delete(fileId=file['id']).execute()
                os.remove(filename)

            except Exception as e:
                error = f"❌ Error: {str(e).splitlines()[0]}"
                sheet.update_cell(i, 3, error)
                traceback.print_exc()

    except Exception as e:
        print("CRITICAL ERROR:", e)
        traceback.print_exc()


# ✅ Replit-compatible app start
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=81)
