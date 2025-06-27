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

SHARED_DRIVE_ID = '1L-HAhdWKIqn4bTteHZ0UwKtNF9QA9EaZ'  # Your Shared Drive ID here

def get_credentials():
    service_account_info = json.loads(os.environ['SERVICE_ACCOUNT_JSON'])
    return Credentials.from_service_account_info(service_account_info, scopes=SCOPES)

@app.route('/', methods=['POST'])
def process_audio_sheet():
    try:
        creds = get_credentials()
        gc = gspread.authorize(creds)
        sheet = gc.open("SS").worksheet("M")
        drive_service = build('drive', 'v3', credentials=creds)
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

        command = sheet.acell("D1").value.strip().lower()
        if command == "purge":
            deleted = purge_all_media_files(drive_service)
            sheet.update_acell("D2", f"🧹 Purged {deleted} file(s)")
            sheet.update_acell("D1", "Done")
            return f"Purged {deleted} file(s)", 200

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
                filepath = f"/tmp/{filename_base}.%(ext)s"

                search_response = youtube.search().list(
                    q=query, part='id', maxResults=1, type='video'
                ).execute()

                items = search_response.get('items', [])
                if not items:
                    sheet.update_cell(i, 3, "❌ No results found")
                    continue

                video_id = items[0].get('id', {}).get('videoId')
                if not video_id:
                    sheet.update_cell(i, 3, "❌ Invalid video result")
                    continue

                video_url = f"https://www.youtube.com/watch?v={video_id}"
                sheet.update_cell(i, 3, "🔄 Downloading...")

                ydl_opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': filepath,
                    'noplaylist': True,
                    'quiet': True,
                    'ffmpeg_location': '/usr/bin/ffmpeg',
                    'postprocessors': [
                        {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '5'},
                        {'key': 'FFmpegMetadata', 'add_metadata': True}
                    ],
                    'postprocessor_args': [
                        '-metadata', f'title={title}',
                        '-metadata', f'artist={artist}'
                    ],
                    'http_headers': {'User-Agent': random.choice(USER_AGENTS)}
                }

                success = False
                for attempt in range(3):
                    try:
                        with YoutubeDL(ydl_opts) as ydl:
                            info = ydl.extract_info(video_url, download=True)
                            dl_filename = ydl.prepare_filename(info)
                        success = True
                        break
                    except Exception as e:
                        print(f"Attempt {attempt + 1} failed: {e}")
                        if attempt == 2:
                            sheet.update_cell(i, 3, f"❌ Failed: {str(e).splitlines()[0]}")
                        time.sleep(3 * (attempt + 1))

                if not success:
                    continue

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

                file_metadata = {
                    'name': os.path.basename(filename),
                    'mimeType': 'audio/mpeg',
                    'parents': [SHARED_DRIVE_ID]
                }
                media = MediaFileUpload(filename, mimetype='audio/mpeg')
                file = drive_service.files().create(
                    body=file_metadata,
                    media_body=media,
                    supportsAllDrives=True,
                    fields='id'
                ).execute()

                drive_service.permissions().create(
                    fileId=file['id'],
                    body={'role': 'reader', 'type': 'anyone'},
                    supportsAllDrives=True
                ).execute()

                link = f"https://drive.google.com/file/d/{file['id']}/view"
                sheet.update_cell(i, 3, link)
                os.remove(filename)
                time.sleep(random.uniform(2.5, 5))

            except Exception as e:
                sheet.update_cell(i, 3, f"❌ Error: {str(e).splitlines()[0]}")
                traceback.print_exc()
                continue

        return '✅ Process completed', 200

    except Exception as e:
        print("Critical Error:", e)
        traceback.print_exc()
        return f"❌ Critical Error: {e}", 500


@app.route('/download', methods=['POST'])
def download_video():
    try:
        creds = get_credentials()
        drive_service = build('drive', 'v3', credentials=creds)
        data = request.get_json()
        video_id = data.get('videoId')

        if not video_id:
            return "Missing videoId", 400

        info = get_video_title(video_id)
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        safe_title = f"{info['title']} - {info['channel']}".replace("/", "-").replace("\\", "-")
        filepath_template = f"/tmp/{safe_title}.%(ext)s"

        ydl_opts = {
            'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
            'outtmpl': filepath_template,
            'noplaylist': True,
            'quiet': True,
            'merge_output_format': 'mp4',
            'ffmpeg_location': '/usr/bin/ffmpeg',
            'ignoreerrors': True,
            'nocheckcertificate': True,
            'sleep_interval': 1,
            'concurrent_fragment_downloads': 1,
            'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
            'postprocessors': [
                {'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'},
                {'key': 'FFmpegMetadata'}
            ]
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            if not info:
                return "❌ Video could not be downloaded (no info returned)", 500
            downloaded_path = ydl.prepare_filename(info)

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
        uploaded_file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            supportsAllDrives=True,
            fields='id'
        ).execute()

        drive_service.permissions().create(
            fileId=uploaded_file['id'],
            body={'role': 'reader', 'type': 'anyone'},
            supportsAllDrives=True
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


def purge_all_media_files(drive_service):
    deleted = 0
    page_token = None
    query = "mimeType contains 'audio/' or mimeType contains 'video/'"
    while True:
        response = drive_service.files().list(
            q=query,
            spaces='drive',
            fields='nextPageToken, files(id, name)',
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        for file in response.get('files', []):
            drive_service.files().delete(fileId=file['id'], supportsAllDrives=True).execute()
            deleted += 1
        page_token = response.get('nextPageToken', None)
        if not page_token:
            break
    return deleted


if __name__ == '__main__':
    from os import environ
    app.run(host='0.0.0.0', port=int(environ.get('PORT', 5000)))
