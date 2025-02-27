from flask import Flask, Response, json, render_template, request, jsonify, send_file, after_this_request
import yt_dlp
import time
import os
import re
import traceback
from uuid import uuid4
from datetime import datetime

app = Flask(__name__)

DOWNLOAD_DIR = "temp_downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

latest_video_url = None
video_details = {}
download_progress = {}

def sanitize_filename(filename):
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1F]', '', filename)
    return filename[:100]

def create_safe_filename(title, ext="mp4", max_length=50):
    sanitized_title = sanitize_filename(title)
    if len(sanitized_title) > max_length:
        sanitized_title = sanitized_title[:max_length]
    return f'{sanitized_title}-{uuid4().hex[:8]}.{ext}'

def extract_video_details(video_url, quality_format="bv*+ba/b"):
    try:
        ydl_opts = {
            'format': quality_format,
            'noplaylist': True,
            'quiet': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(video_url, download=False)
            best_format = info_dict.get('requested_formats', [info_dict])[0]

        video_height = best_format.get('height', 'Unknown')

        if video_height == 'Unknown':
            video_quality = "Unknown"
        elif video_height >= 1080:
            video_quality = "1080p"
        elif video_height >= 720:
            video_quality = "720p"
        elif video_height >= 480:
            video_quality = "480p"
        elif video_height >= 360:
            video_quality = "360p"
        else:
            video_quality = "Unknown"

        upload_date = info_dict.get('upload_date', '')
        if upload_date:
            formatted_upload_date = datetime.strptime(upload_date, '%Y%m%d').strftime('%B %d, %Y')
        else:
            formatted_upload_date = "Unknown"

        details = {
            "title": info_dict.get('title', ''),
            "thumbnail": info_dict.get('thumbnail', ''),
            "quality": video_quality,
            "duration": info_dict.get('duration', 0),
            "url": video_url,
            "date": formatted_upload_date
        }
        return details
    except Exception as e:
        print(f"Error extracting video details: {e}")
        return None

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/video_details", methods=["POST"])
def get_video_details():
    global latest_video_url, video_details
    video_url = request.form.get("video_url")
    
    if not video_url:
        return jsonify({"error": "No URL provided"}), 400

    if video_url not in video_details:
        details = extract_video_details(video_url)
        if not details:
            return jsonify({"error": "Failed to fetch video details"}), 400
        video_details[video_url] = details
        latest_video_url = video_url

    return jsonify(video_details[video_url])

@app.route("/download", methods=["POST"])
def download_video():
    global latest_video_url, video_details, download_progress

    video_url = request.form.get("video_url")
    quality_format = request.form.get("quality", "worst")
    download_type = request.form.get("type", "video")

    if not video_url:
        return jsonify({"error": "No video URL provided."}), 400

    if video_url not in video_details:
        video_details[video_url] = extract_video_details(video_url, quality_format)

    latest_video_url = video_url

    download_progress = {}

    def progress_hook(d):
        global download_progress

        if d['status'] == 'downloading':
            download_progress = {
                'filename': d.get('filename', 'Unknown'),
                'downloaded_bytes': d.get('downloaded_bytes', 0),
                'total_bytes': d.get('total_bytes', 0),
                'download_speed': d.get('speed', 0),
                'percentage': d.get('downloaded_bytes', 0) / max(d.get('total_bytes', 1), 1),
                'eta': d.get('eta', 0),
            }
        elif d['status'] == 'finished':
            download_progress['percentage'] = 1.0

    try:
        details = video_details[video_url]
        temp_filename = create_safe_filename(details["title"], ext="mp4" if download_type == "video" else "mp3")
        temp_filepath = os.path.join(DOWNLOAD_DIR, temp_filename)

        video_ydl_opts = {
            'format': quality_format,
            'noplaylist': True,
            'quiet': True,
            'merge_output_format': 'mp4',
            'outtmpl': temp_filepath,
            'progress_hooks': [progress_hook],
        }

        audio_ydl_opts = {
            'format': '140',
            'noplaylist': True,
            'quiet': True,
            'outtmpl': temp_filepath,
            'progress_hooks': [progress_hook],
        }

        ydl_opts = audio_ydl_opts if download_type == "audio" else video_ydl_opts

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])

        @after_this_request
        def cleanup(response):
            try:
                remove_download_folder()
            except Exception as e:
                print(f"Error during cleanup: {e}")
            return response

        return send_file(temp_filepath, as_attachment=True, download_name=temp_filename)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "Failed to download video"}), 500

@app.route("/utilis", methods=["GET"])
def get_download_progress():
    global download_progress
    return jsonify(download_progress)    
    
@app.route("/stream", methods=["GET"])
def stream_progress():
    def event_stream():
        global download_progress
        try:
            while True:
                if download_progress:
                    yield f"data: {json.dumps(download_progress)}\n\n"
                time.sleep(0.5)
        finally:
            remove_download_folder()

    return Response(event_stream(), content_type="text/event-stream")

def remove_download_folder():
    try:
        for filename in os.listdir(DOWNLOAD_DIR):
            file_path = os.path.join(DOWNLOAD_DIR, filename)
            if os.path.isfile(file_path):
                retries = 3
                while retries > 0:
                    try:
                        os.remove(file_path)
                        break
                    except PermissionError:
                        time.sleep(1)
                        retries -= 1
    except Exception as e:
        print(f"Error removing files or folder: {e}")

@app.route("/cleanup", methods=["POST"])
def cleanup():
    try:
        remove_download_folder()
        return jsonify({"message": "Download folder and files cleaned up."}), 200
    except Exception as e:
        return jsonify({"error": f"Failed to clean up: {e}"}), 500

if __name__ == "__main__":
    app.run(debug=True)
