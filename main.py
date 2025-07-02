from flask import Flask, request
import os, json, random, traceback
from yt_dlp import YoutubeDL
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.service_account import Credentials

app = Flask(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive']
YOUTUBE_API_KEY = os.environ['YOUTUBE_API_KEY']
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
]

def get_credentials():
    info = json.loads(os.environ['SERVICE_ACCOUNT_JSON'])
    return Credentials.from_service_account_info(info, scopes=SCOPES)

def get_video_title(video_id):
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    response = youtube.videos().list(
        part='snippet,status,contentDetails',
        id=video_id
    ).execute()

    if not response['items']:
        raise Exception("Video not found")

    item = response['items'][0]

    # Ensure the video is public and embeddable
    if item['status']['privacyStatus'] != 'public':
        raise Exception("Video is not public")
    if not item['status'].get('embeddable', True):
        raise Exception("Video is not embeddable")

    # Block age-restricted content
    if item['contentDetails'].get('contentRating', {}).get('ytRating') == 'ytAgeRestricted':
        raise Exception("Video is age-restricted")

    return {
        "title": item['snippet']['title'],
        "channel": item['snippet']['channelTitle']
    }

@app.route('/download', methods=['POST'])
def download_video():
    try:
        creds = get_credentials()
        drive = build('drive', 'v3', credentials=creds)
        data = request.get_json()
        video_id = data.get('videoId')

        if not video_id:
            return "Missing videoId", 400

        # Validate the video before downloading.
        info = get_video_title(video_id)
        title_safe = f"{info['title']} - {info['channel']}".replace("/", "-").replace("\\", "-")
        filepath = f"/tmp/{title_safe}.%(ext)s"
        final_path = f"/tmp/{title_safe}.mp4"

        ydl_opts = {
            'format': 'bestvideo[height<=720]+bestaudio/best/best',
            'outtmpl': filepath,
            'merge_output_format': 'mp4',
            'ffmpeg_location': '/usr/bin/ffmpeg',
            'quiet': True,
            'noplaylist': True,
            'http_headers': {'User-Agent': random.choice(USER_AGENTS)},
            'postprocessors': [
                {'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'},
                {'key': 'FFmpegMetadata'}
            ]
        }

        with YoutubeDL(ydl_opts) as ydl:
            try:
                # Attempt to download the video.
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
            except Exception as ytdl_error:
                return f"❌ Skipped: {ytdl_error}", 400

        if not os.path.exists(final_path):
            return "❌ File not found after yt-dlp", 500
        if os.path.getsize(final_path) < 1024:
            os.remove(final_path)
            return "❌ File too small", 500
        if os.path.getsize(final_path) > 500 * 1024 * 1024:
            os.remove(final_path)
            return "❌ File too large (>500MB)", 400

        file_metadata = {
            'name': os.path.basename(final_path),
            'mimeType': 'video/mp4'
        }
        media = MediaFileUpload(final_path, mimetype='video/mp4', resumable=True)
        uploaded_file = drive.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()

        # Set the file's permission to be readable by anyone.
        drive.permissions().create(
            fileId=uploaded_file['id'],
            body={'role': 'reader', 'type': 'anyone'}
        ).execute()

        os.remove(final_path)

        return f"https://drive.google.com/file/d/{uploaded_file['id']}/view", 200

    except Exception as e:
        traceback.print_exc()
        return f"❌ Error: {e}", 500

@app.route('/test-access', methods=['GET'])
def test_access():
    try:
        creds = get_credentials()
        drive = build('drive', 'v3', credentials=creds)
        results = drive.files().list(pageSize=10, fields="files(id, name)").execute()
        return json.dumps(results.get('files', [])), 200
    except Exception as e:
        return f"❌ Error: {e}", 500

if __name__ == '__main__':
    from os import environ
    app.run(host='0.0.0.0', port=int(environ.get('PORT', 5000)))
