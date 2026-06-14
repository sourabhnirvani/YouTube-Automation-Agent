# FILE: src/uploader.py
# Full YouTube management: upload, delete, list, update, set visibility

import os
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from pathlib import Path

CLIENT_SECRETS_FILE = Path('client_secrets.json')
CREDENTIALS_FILE = Path('credentials.json')
# Full management scope (upload + read + delete + update)
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload"
]

def get_authenticated_service():
    """Handles OAuth2 and returns authenticated YouTube service.
    Proactively refreshes tokens expiring within 5 minutes to prevent mid-upload failures.
    """
    credentials = None
    if CREDENTIALS_FILE.exists():
        try:
            credentials = Credentials.from_authorized_user_file(str(CREDENTIALS_FILE), YOUTUBE_SCOPES)
        except Exception as e:
            print(f"WARNING: Failed to load credentials file: {e}. Re-authenticating...")
            credentials = None

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            print("INFO: Refreshing expired credentials...")
            credentials.refresh(Request())
        else:
            print("INFO: No valid credentials found. Starting new authentication flow...")
            if not CLIENT_SECRETS_FILE.exists():
                raise FileNotFoundError(f"CRITICAL ERROR: {CLIENT_SECRETS_FILE} not found.")
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), scopes=YOUTUBE_SCOPES)
            credentials = flow.run_local_server(port=0)
        with open(CREDENTIALS_FILE, 'w') as f:
            f.write(credentials.to_json())
        print(f"INFO: Credentials saved to {CREDENTIALS_FILE}")
    else:
        # Proactively refresh if token expires within 5 minutes
        import datetime
        if credentials.expiry and credentials.refresh_token:
            time_left = (credentials.expiry - datetime.datetime.utcnow()).total_seconds()
            if time_left < 300:
                print(f"INFO: Token expires in {time_left:.0f}s — proactively refreshing...")
                try:
                    credentials.refresh(Request())
                    with open(CREDENTIALS_FILE, 'w') as f:
                        f.write(credentials.to_json())
                    print("INFO: Token refreshed and saved.")
                except Exception as e:
                    print(f"WARNING: Proactive refresh failed: {e}. Will try with current token.")

    return build('youtube', 'v3', credentials=credentials)


def upload_to_youtube(video_path, title, description, tags, thumbnail_path=None):
    """Uploads a video to YouTube with metadata and optional thumbnail."""
    print(f"Uploading '{video_path}' to YouTube...")
    try:
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        tag_list = tags if isinstance(tags, list) else [tag.strip() for tag in str(tags).split(',') if tag.strip()]

        youtube = get_authenticated_service()
        request_body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': tag_list,
                'categoryId': '28'
            },
            'status': {
                'privacyStatus': 'public',
                'selfDeclaredMadeForKids': False
            }
        }
        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
        request = youtube.videos().insert(
            part=','.join(request_body.keys()),
            body=request_body,
            media_body=media
        )
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"Uploaded {int(status.progress() * 100)}%.")
        video_id = response.get('id')
        print(f"Video uploaded! ID: {video_id}")
        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail_path))).execute()
                print("Thumbnail uploaded!")
            except Exception as e:
                print(f"Thumbnail upload failed: {e}")
        return video_id
    except Exception as e:
        print(f"Upload failed: {e}")
        raise


def delete_youtube_video(video_id: str) -> bool:
    """Deletes a YouTube video by its ID. Returns True on success."""
    if not video_id:
        return False
    try:
        youtube = get_authenticated_service()
        youtube.videos().delete(id=video_id).execute()
        print(f"Deleted video: {video_id}")
        return True
    except Exception as e:
        print(f"Failed to delete video {video_id}: {e}")
        return False


def list_channel_videos(max_results: int = 50) -> list:
    """Lists videos on the authenticated channel. Returns list of dicts."""
    try:
        youtube = get_authenticated_service()
        # Get channel's uploads playlist
        ch = youtube.channels().list(part="contentDetails", mine=True).execute()
        playlist_id = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        
        videos = []
        next_page_token = None
        while True:
            items = youtube.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=next_page_token
            ).execute()
            
            for item in items.get("items", []):
                snip = item["snippet"]
                videos.append({
                    "video_id": snip["resourceId"]["videoId"],
                    "title": snip["title"],
                    "published_at": snip.get("publishedAt", ""),
                })
                
            next_page_token = items.get("nextPageToken")
            if not next_page_token or len(videos) >= max_results:
                break
                
        return videos[:max_results]
    except Exception as e:
        print(f"Failed to list videos: {e}")
        return []


def update_youtube_video(video_id: str, title: str = None, description: str = None, tags: list = None) -> bool:
    """Updates a YouTube video's title, description or tags."""
    try:
        youtube = get_authenticated_service()
        current = youtube.videos().list(part="snippet", id=video_id).execute()
        if not current.get("items"):
            return False
        snippet = current["items"][0]["snippet"]
        if title:
            snippet["title"] = title
        if description:
            snippet["description"] = description
        if tags:
            snippet["tags"] = tags
        youtube.videos().update(part="snippet", body={"id": video_id, "snippet": snippet}).execute()
        print(f"Updated video {video_id}")
        return True
    except Exception as e:
        print(f"Failed to update video {video_id}: {e}")
        return False


def set_video_visibility(video_id: str, status: str) -> bool:
    """Sets video visibility: 'public', 'private', or 'unlisted'."""
    if status not in {"public", "private", "unlisted"}:
        raise ValueError("status must be one of: public, private, unlisted")
    try:
        youtube = get_authenticated_service()
        youtube.videos().update(
            part="status",
            body={"id": video_id, "status": {"privacyStatus": status}}
        ).execute()
        print(f"Set video {video_id} to {status}")
        return True
    except Exception as e:
        print(f"Failed to set visibility: {e}")
        return False
