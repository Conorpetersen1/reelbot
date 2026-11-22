"""Google Drive access as YOU (OAuth refresh token), so uploads count against your own Drive storage.
(Service accounts have no storage of their own on personal Google accounts, so they can't upload.)

Secrets: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN, GDRIVE_FOLDER_ID.
"""
import io
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials
from .common import CFG, env

_svc = None
_folders = {}


def svc():
    global _svc
    if _svc is None:
        creds = Credentials(None, refresh_token=env("GOOGLE_REFRESH_TOKEN"),
                            client_id=env("GOOGLE_CLIENT_ID"), client_secret=env("GOOGLE_CLIENT_SECRET"),
                            token_uri="https://oauth2.googleapis.com/token",
                            scopes=["https://www.googleapis.com/auth/drive"])
        _svc = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _svc


def folder(key):
    """Id of a named subfolder (config drive.<key>), created if missing."""
    if key in _folders:
        return _folders[key]
    root, name = env("GDRIVE_FOLDER_ID"), CFG["drive"][key]
    q = (f"'{root}' in parents and name = '{name}' and trashed = false "
         "and mimeType = 'application/vnd.google-apps.folder'")
    res = svc().files().list(q=q, fields="files(id)", supportsAllDrives=True,
                             includeItemsFromAllDrives=True).execute()["files"]
    if res:
        fid = res[0]["id"]
    else:
        fid = svc().files().create(body={"name": name, "parents": [root],
                                         "mimeType": "application/vnd.google-apps.folder"},
                                   fields="id", supportsAllDrives=True).execute()["id"]
    _folders[key] = fid
    return fid


def list_files(key):
    out, token = [], None
    while True:
        r = svc().files().list(q=f"'{folder(key)}' in parents and trashed = false",
                               fields="nextPageToken, files(id,name,mimeType,size,createdTime)",
                               pageToken=token, pageSize=200, supportsAllDrives=True,
                               includeItemsFromAllDrives=True).execute()
        out += [f for f in r["files"] if f["mimeType"] != "application/vnd.google-apps.folder"]
        token = r.get("nextPageToken")
        if not token:
            return out


def exists(file_id):
    try:
        f = svc().files().get(fileId=file_id, fields="trashed", supportsAllDrives=True).execute()
        return not f.get("trashed", False)
    except Exception:
        return False


def download(file_id, path):
    req = svc().files().get_media(fileId=file_id, supportsAllDrives=True)
    with io.FileIO(str(path), "wb") as fh:
        dl = MediaIoBaseDownload(fh, req, chunksize=16 * 1024 * 1024)
        done = False
        while not done:
            _, done = dl.next_chunk()
    return path


def upload(path, key, name=None, mime="video/mp4"):
    media = MediaFileUpload(str(path), mimetype=mime, resumable=True)
    return svc().files().create(body={"name": name or path.name, "parents": [folder(key)]},
                                media_body=media, fields="id", supportsAllDrives=True).execute()["id"]


def move(file_id, key):
    f = svc().files().get(fileId=file_id, fields="parents", supportsAllDrives=True).execute()
    svc().files().update(fileId=file_id, addParents=folder(key),
                         removeParents=",".join(f.get("parents", [])), supportsAllDrives=True).execute()


def trash(file_id):
    svc().files().update(fileId=file_id, body={"trashed": True}, supportsAllDrives=True).execute()


def quota():
    """(used_bytes, limit_bytes or None)."""
    q = svc().about().get(fields="storageQuota").execute()["storageQuota"]
    return int(q.get("usage", 0)), (int(q["limit"]) if q.get("limit") else None)


# ---- Local stand-in for testing without Google (set LOCAL_DRIVE=/some/folder) ----
import os as _os, shutil as _sh, pathlib as _pl
if _os.environ.get("LOCAL_DRIVE"):
    _LD = _pl.Path(_os.environ["LOCAL_DRIVE"])

    def folder(key):
        p = _LD / CFG["drive"][key]; p.mkdir(parents=True, exist_ok=True); return str(p)

    def list_files(key):
        import datetime as _dt
        return [{"id": str(f), "name": f.name, "mimeType": "x",
                 "createdTime": _dt.datetime.fromtimestamp(f.stat().st_mtime, _dt.timezone.utc).isoformat()}
                for f in sorted(_pl.Path(folder(key)).iterdir())]

    def exists(file_id):
        return _pl.Path(file_id).exists()

    def download(file_id, path):
        _sh.copy(file_id, path); return path

    def upload(path, key, name=None, mime=None):
        dst = _pl.Path(folder(key)) / (name or _pl.Path(path).name); _sh.copy(path, dst); return str(dst)

    def move(file_id, key):
        _sh.move(file_id, _pl.Path(folder(key)) / _pl.Path(file_id).name)

    def trash(file_id):
        _pl.Path(file_id).unlink(missing_ok=True)

    def quota():
        return 0, None
