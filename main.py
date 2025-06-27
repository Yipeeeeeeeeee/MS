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
from pprint import pprint

app = Flask(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]

YOUTUBE_API_KEY = os.environ['YOUTUBE_API_KEY']
SHARED_DRIVE_ID = '1L-HAhdWKIqn4bTteHZ0UwKtNF9QA9EaZ'

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
]

class YDLLogger:
    def debug(self, msg): print(msg)
    def warning(self, msg): print("⚠️", msg)
    def error(self, msg): print("❌", msg)

def get_credentials():
    service_account_info = json.loads(os.environ['SERVICE_ACCOUNT_JSON'])
    return Credentials.from_service_account_info(service_account_info, scopes=SCOPES)

@app.route('/download', methods=['POST'])
def download_video():
    try:
        creds = get_credentials()
        drive_service = build('drive', 'v3', credentials=creds)
        data = request.get_json()
        video_id = data.get('videoId')

        if not video_id:
            return "Missing videoId", 400

        try:
            info = get_video_title(video_id)
        except Exception as e:
            return f"❌ Skipped: {e}", 400

        video_url = f"https://www.youtube.com/watch?v={video_id}"
        safe_title = f"{info['title']} - {info['channel']}".replace("/", "-").replace("\\", "-")
        filepath_template = f"/tmp/{safe_title}.%(ext)s"

        def run_download(force_generic=False):
            opts = {
                'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
                'outtmpl': filepath_template,
                'noplaylist': True,
                'merge_output_format': 'mp4',
                'ffmpeg_location': '/usr/bin/ffmpeg',
                'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
                'postprocessors': [
                    {'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'},
                    {'key': 'FFmpegMetadata'}
                ],
                'logger': YDLLogger(),
                'quiet': False,
                'extractor_args': {
                    'youtube': [
                        'player_client=web',  # mimic standard web player to avoid bot check
                        'check_formats=selected'
                    ]
                }
            }
            if force_generic:
                opts['force_generic_extractor'] = True

            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
                print("ℹ️ yt-dlp info dump:")
                pprint(info)
                return info, ydl.prepare_filename(info) if info else None

        info, downloaded_path = run_download()
        if not info or not downloaded_path:
            print("⚠️ Attempting fallback with generic extractor...")
            info, downloaded_path = run_download(force_generic=True)

        if not info or not downloaded_path:
            return "❌ Video could not be downloaded (no info returned)", 500

        filename = os.path.splitext(downloaded_path)[0] + ".mp4"

        if not os.path.exists(filename):
            return "❌ File not found", 500

        if os.path.getsize(filename) < 1024:
            os.remove(filename)
            return "❌ File is empty", 500

        if os.path.getsize(filename) > 500 * 1024 * 1024:
            os.remove(filename)
            return "❌ Video too large (>500MB)", 400

        file_metadata = {
            'name': os.path.basename(filename),
            'mimeType': 'video/mp4',
            'parents': [SHARED_DRIVE_ID]
        }
        media = MediaFileUpload(filename, mimetype='video/mp4', resumable=True)
        uploaded_file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()

        drive_service.permissions().create(
            fileId=uploaded_file['id'], body={'role': 'reader', 'type': 'anyone'}
        ).execute()

        os.remove(filename)

        return f"https://drive.google.com/file/d/{uploaded_file['id']}/view", 200

    except Exception as e:
        traceback.print_exc()
        return f"❌ Error: {e}", 500

def get_video_title(video_id):
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    response = youtube.videos().list(part='snippet,status,contentDetails', id=video_id).execute()

    if not response['items']:
        raise Exception("Video not found or private")

    item = response['items'][0]
    status = item['status']
    details = item['contentDetails']

    if status.get('privacyStatus') != 'public' or not status.get('embeddable', True):
        raise Exception("Video is private or not embeddable")

    if details.get('contentRating', {}).get('ytRating') == 'ytAgeRestricted':
        raise Exception("Video is age-restricted")

    return {
        "title": item['snippet']['title'],
        "channel": item['snippet']['channelTitle']
    }

if __name__ == '__main__':
    from os import environ
    app.run(host='0.0.0.0', port=int(environ.get('PORT', 5000)))
