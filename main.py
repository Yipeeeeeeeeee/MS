from flask import Flask, request
import os
import json
import traceback
import time
import random
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

YOUTUBE_API_KEY = os.environ['YOUTUBE_API_KEY']

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
]

def get_credentials():
    service_account_info = json.loads(os.environ['SERVICE_ACCOUNT_JSON'])
    return Credentials.from_service_account_info(service_account_info, scopes=SCOPES)

@app.route('/download', methods=['POST'])
def download_by_video_id():
    try:
        creds = get_credentials()
        drive_service = build('drive', 'v3', credentials=creds)
        data = request.get_json()
        video_id = data.get('videoId')
        if not video_id:
            return "Missing videoId", 400

        video_url = f"https://www.youtube.com/watch?v={video_id}"
        title_info = get_video_title(video_id)
        query = f"{title_info['title']} - {title_info['channel']}"
        filename_base = query.replace("/", "-").replace("\\", "-")
        filepath = f"/tmp/{filename_base}.%(ext)s"

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4',
            'outtmpl': filepath,
            'noplaylist': True,
            'quiet': True,
            'merge_output_format': 'mp4',
            'ffmpeg_location': '/usr/bin/ffmpeg',
            'http_headers': {
                'User-Agent': random.choice(USER_AGENTS)
            },
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            dl_filename = ydl.prepare_filename(info)

        filename = None
        for ext in [".mp4"]:
            test_file = os.path.splitext(dl_filename)[0] + ext
            if os.path.exists(test_file):
                filename = test_file
                break

        if not filename:
            return "❌ File not found", 500

        file_metadata = {'name': os.path.basename(filename), 'mimeType': 'video/mp4'}
        media = MediaFileUpload(filename, mimetype='video/mp4')
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

        drive_service.permissions().create(
            fileId=file['id'],
            body={'role': 'reader', 'type': 'anyone'}
        ).execute()

        os.remove(filename)
        return f"https://drive.google.com/file/d/{file['id']}/view", 200

    except Exception as e:
        traceback.print_exc()
        return f"❌ Error: {e}", 500

def get_video_title(video_id):
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    response = youtube.videos().list(part='snippet', id=video_id).execute()
    item = response['items'][0]['snippet']
    return {
        "title": item['title'],
        "channel": item['channelTitle']
    }

if __name__ == '__main__':
    from os import environ
    app.run(host='0.0.0.0', port=int(environ.get('PORT', 5000)))
