from flask import Flask, request
import os, json, traceback, time, random
import gspread
from yt_dlp import YoutubeDL
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.service_account import Credentials

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']
YOUTUBE_API_KEY = os.environ['YOUTUBE_API_KEY']
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
]

def get_credentials():
    info = json.loads(os.environ['SERVICE_ACCOUNT_JSON'])
    return Credentials.from_service_account_info(info, scopes=SCOPES)

@app.route('/', methods=['POST'])
def process_audio_sheet():
    try:
        creds = get_credentials()
        gc = gspread.authorize(creds)
        sheet = gc.open("SS").worksheet("M")
        drive = build('drive', 'v3', credentials=creds)
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)

        command = sheet.acell("D1").value.strip().lower()
        if command == "purge":
            deleted = purge_all_media_files(drive_service)
            sheet.update_acell("D2", f"🧹 Purged {deleted} audio file(s)")
            return f"Purged {deleted} audio files", 200

        rows = sheet.get_all_values()
        for i, row in enumerate(rows[1:], start=2):
            artist, title, link = (row + ["", "", ""])[:3]
            if link.strip():
                continue
            if not artist or not title:
                sheet.update_cell(i, 3, "⚠️ Missing artist/title")
                continue

            query = f"{artist} - {title}"
            filename_base = query.replace("/", "-")
            filepath = f"/tmp/{filename_base}.%(ext)s"

            search = youtube.search().list(q=query, part='id', maxResults=1, type='video').execute()
            items = search.get('items', [])
            if not items:
                sheet.update_cell(i, 3, "❌ No results found")
                continue

            video_id = items[0]['id']['videoId']
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            sheet.update_cell(i, 3, "🔄 Downloading...")

            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': filepath,
                'quiet': True,
                'noplaylist': True,
                'ffmpeg_location': '/usr/bin/ffmpeg',
                'postprocessors': [
                    {'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '5'},
                    {'key': 'FFmpegMetadata'}
                ],
                'postprocessor_args': ['-metadata', f'title={title}', '-metadata', f'artist={artist}'],
                'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
            }

            success = False
            for attempt in range(3):
                try:
                    with YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(video_url, download=True)
                        filename = ydl.prepare_filename(info)
                    success = True
                    break
                except Exception as e:
                    print(f"Retry {attempt+1} failed:", e)
                    time.sleep(3 * (attempt + 1))

            if not success or not os.path.exists(filename.replace(".webm", ".mp3")):
                sheet.update_cell(i, 3, "❌ Download failed")
                continue

            file_path = filename.replace(".webm", ".mp3")
            sheet.update_cell(i, 3, "⬆️ Uploading...")

            metadata = {'name': os.path.basename(file_path), 'mimeType': 'audio/mpeg'}
            media = MediaFileUpload(file_path, mimetype='audio/mpeg')
            file = drive.files().create(body=metadata, media_body=media, fields='id').execute()
            drive.permissions().create(fileId=file['id'], body={'role': 'reader', 'type': 'anyone'}).execute()

            link = f"https://drive.google.com/file/d/{file['id']}/view"
            sheet.update_cell(i, 3, link)
            os.remove(file_path)
            time.sleep(random.uniform(2.5, 5))

        return "✅ Audio Process Done", 200
    except Exception as e:
        traceback.print_exc()
        return f"❌ Error: {e}", 500

@app.route('/download', methods=['POST'])
def download_video():
    try:
        creds = get_credentials()
        drive = build('drive', 'v3', credentials=creds)
        video_id = request.get_json().get('videoId')
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        info = get_video_info(video_id)

        title_safe = f"{info['title']} - {info['channel']}".replace("/", "-")
        outtmpl = f"/tmp/{title_safe}.%(ext)s"

        ydl_opts = {
            'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
            'outtmpl': outtmpl,
            'quiet': True,
            'merge_output_format': 'mp4',
            'ffmpeg_location': '/usr/bin/ffmpeg',
            'postprocessors': [
                {'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'},
                {'key': 'FFmpegMetadata'}
            ],
            'http_headers': {'User-Agent': random.choice(USER_AGENTS)}
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            file_path = ydl.prepare_filename(info).replace(".webm", ".mp4")

        if not os.path.exists(file_path):
            return "❌ File not found", 500

        if os.path.getsize(file_path) > 500 * 1024 * 1024:
            os.remove(file_path)
            return "❌ File too large", 400

        metadata = {'name': os.path.basename(file_path), 'mimeType': 'video/mp4'}
        media = MediaFileUpload(file_path, mimetype='video/mp4')
        uploaded = drive.files().create(body=metadata, media_body=media, fields='id').execute()
        drive.permissions().create(fileId=uploaded['id'], body={'role': 'reader', 'type': 'anyone'}).execute()
        os.remove(file_path)

        return f"https://drive.google.com/file/d/{uploaded['id']}/view", 200
    except Exception as e:
        traceback.print_exc()
        return f"❌ Error: {e}", 500

def get_video_info(video_id):
    yt = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    response = yt.videos().list(part='snippet,status,contentDetails', id=video_id).execute()
    item = response['items'][0]

    if item['status']['privacyStatus'] != 'public' or not item['status'].get('embeddable', True):
        raise Exception("Video is private or not embeddable")
    if item['contentDetails'].get('contentRating', {}).get('ytRating') == 'ytAgeRestricted':
        raise Exception("Age-restricted")

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
            pageToken=page_token
        ).execute()

        for file in response.get('files', []):
            drive_service.files().delete(fileId=file['id']).execute()
            deleted += 1

        page_token = response.get('nextPageToken')
        if not page_token:
            break

    return deleted


if __name__ == '__main__':
    from os import environ
    app.run(host='0.0.0.0', port=int(environ.get('PORT', 5000)))
    
