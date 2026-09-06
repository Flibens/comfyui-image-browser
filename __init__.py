"""
ComfyUI Image Browser - Fixed Metadata Version
"""

import os
import json
import re
import shutil
import subprocess
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from aiohttp import web
import server
import folder_paths

try:
    from PIL import Image, ExifTags
except ImportError:
    Image = None

# --- Constants ---
WEB_DIRECTORY = "./web"
__version__ = "1.7.1"
MODULE_DIR = Path(__file__).parent
LEGACY_ADDITIONAL_FOLDERS_FILE = MODULE_DIR / "folders.json"
LEGACY_FAVORITES_FILE = MODULE_DIR / "favorites.json"

def _get_persistent_state_dir():
    candidates = []

    get_user_directory = getattr(folder_paths, "get_user_directory", None)
    if callable(get_user_directory):
        try:
            candidates.append(Path(get_user_directory()))
        except Exception:
            pass

    user_directory = getattr(folder_paths, "user_directory", None)
    if user_directory:
        try:
            candidates.append(Path(user_directory))
        except Exception:
            pass

    base_path = getattr(folder_paths, "base_path", None)
    if base_path:
        try:
            candidates.append(Path(base_path) / "user")
        except Exception:
            pass

    for candidate in candidates:
        try:
            state_dir = candidate.expanduser().resolve() / "comfyui-image-browser"
            state_dir.mkdir(parents=True, exist_ok=True)
            return state_dir
        except Exception:
            continue

    fallback_dir = MODULE_DIR / ".state"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    return fallback_dir

STATE_DIRECTORY = _get_persistent_state_dir()
LEGACY_STATE_DIRECTORY = STATE_DIRECTORY.parent / "gemini-image-browser"
ADDITIONAL_FOLDERS_FILE = STATE_DIRECTORY / "folders.json"
FAVORITES_FILE = STATE_DIRECTORY / "favorites.json"


def _get_shared_browser_state_file():
    override = os.environ.get("LUMAVAULT_SHARED_BROWSER_STATE_FILE")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "LumaVault" / "shared-comfyui-image-browser.json"
    return STATE_DIRECTORY / "shared-comfyui-image-browser.json"


SHARED_BROWSER_STATE_FILE = _get_shared_browser_state_file()
_SHARED_STATE_THREAD_LOCK = threading.RLock()
_SHARED_STATE_LOCK_DEPTH = threading.local()


@contextmanager
def _shared_state_file_lock():
    depth = getattr(_SHARED_STATE_LOCK_DEPTH, "value", 0)
    if depth:
        _SHARED_STATE_LOCK_DEPTH.value = depth + 1
        try:
            yield
        finally:
            _SHARED_STATE_LOCK_DEPTH.value = depth
        return

    with _SHARED_STATE_THREAD_LOCK:
        lock_path = SHARED_BROWSER_STATE_FILE.with_suffix(SHARED_BROWSER_STATE_FILE.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _SHARED_STATE_LOCK_DEPTH.value = 1
            try:
                yield
            finally:
                _SHARED_STATE_LOCK_DEPTH.value = 0
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _normalize_shared_collections(value):
    if not isinstance(value, dict):
        return None
    raw_folders = value.get("folders", [])
    raw_favorites = value.get("favorites", [])
    if not isinstance(raw_folders, list) or not isinstance(raw_favorites, list):
        return None
    folders = []
    known_ids = set()
    for folder in raw_folders:
        if not isinstance(folder, dict):
            continue
        folder_id = str(folder.get("id") or "").strip()
        name = str(folder.get("name") or "").strip()
        path = str(folder.get("path") or "").strip()
        if not folder_id or folder_id == "default" or not name or not path or folder_id in known_ids:
            continue
        folders.append({"id": folder_id, "name": name, "path": path})
        known_ids.add(folder_id)
    favorites = list(dict.fromkeys(
        str(item).strip() for item in raw_favorites
        if isinstance(item, str) and str(item).strip()
    ))
    return {"folders": folders, "favorites": favorites}


def _read_shared_collections():
    try:
        if not SHARED_BROWSER_STATE_FILE.exists():
            return None
        return _normalize_shared_collections(json.loads(SHARED_BROWSER_STATE_FILE.read_text(encoding="utf-8")))
    except Exception:
        return None


def _write_shared_collections(payload):
    normalized = _normalize_shared_collections(payload)
    if normalized is None:
        return
    try:
        with _shared_state_file_lock():
            SHARED_BROWSER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=SHARED_BROWSER_STATE_FILE.parent,
                    prefix=f".{SHARED_BROWSER_STATE_FILE.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as handle:
                    temporary = Path(handle.name)
                    json.dump({"version": 1, **normalized}, handle, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, SHARED_BROWSER_STATE_FILE)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    except Exception as error:
        print(f"Error saving shared LumaVault browser state: {error}")
        raise


def _is_shared_collection_file(file_path):
    try:
        return Path(file_path).resolve() in {ADDITIONAL_FOLDERS_FILE.resolve(), FAVORITES_FILE.resolve()}
    except Exception:
        return False

# --- Helper Functions ---
def _is_relative_to(path_obj, base_obj):
    try:
        path_obj.relative_to(base_obj)
        return True
    except ValueError:
        return False

def _get_allowed_roots():
    roots = []
    for getter_name in ["get_output_directory", "get_input_directory", "get_temp_directory"]:
        getter = getattr(folder_paths, getter_name, None)
        if not callable(getter):
            continue
        try:
            root = Path(getter()).expanduser().resolve()
            roots.append(root)
        except Exception:
            continue
    return roots

def _is_under_allowed_roots(path_obj):
    for root in _get_allowed_roots():
        if _is_relative_to(path_obj, root):
            return True
    return False

def _build_folder_map():
    output_dir = Path(folder_paths.get_output_directory()).expanduser().resolve()
    folder_map = {"default": output_dir}

    for folder in load_json(ADDITIONAL_FOLDERS_FILE):
        folder_id = folder.get("id")
        folder_path = folder.get("path")
        if not folder_id or not folder_path:
            continue
        try:
            resolved = Path(folder_path).expanduser().resolve()
        except Exception:
            continue
        if resolved.exists() and resolved.is_dir():
            folder_map[folder_id] = resolved

    return folder_map

def _resolve_folder_path(folder_id):
    folder_map = _build_folder_map()
    return folder_map.get(folder_id)

def _resolve_file_from_request(base_dir, request_path):
    if not isinstance(request_path, str) or not request_path.strip():
        return None

    requested = Path(request_path)
    if requested.is_absolute() or requested.drive:
        return None

    try:
        full_path = (base_dir / requested).resolve()
    except Exception:
        return None

    if not _is_relative_to(full_path, base_dir):
        return None

    return full_path

def load_json(file_path):
    if _is_shared_collection_file(file_path):
        shared = _read_shared_collections()
        if shared is not None:
            return shared["folders"] if Path(file_path).resolve() == ADDITIONAL_FOLDERS_FILE.resolve() else shared["favorites"]
    if not file_path.exists():
        return []

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading {file_path}: {e}")
        return []

    # Treat empty/whitespace files as empty arrays and auto-heal to valid JSON.
    if not content.strip():
        try:
            save_json([], file_path)
        except Exception:
            pass
        return []

    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, list) else []
    except Exception as e:
        # Auto-heal invalid JSON to prevent repeated parse spam in logs.
        print(f"Error loading {file_path}: {e} (resetting to [])")
        try:
            save_json([], file_path)
        except Exception:
            pass
        return []

def save_json(data, file_path):
    if _is_shared_collection_file(file_path):
        with _shared_state_file_lock():
            shared = _read_shared_collections()
            if shared is None:
                shared = {
                    "folders": _read_local_json_list(ADDITIONAL_FOLDERS_FILE),
                    "favorites": _read_local_json_list(FAVORITES_FILE),
                }
            if Path(file_path).resolve() == ADDITIONAL_FOLDERS_FILE.resolve():
                shared["folders"] = data
            else:
                shared["favorites"] = data
            _write_shared_collections(shared)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        return
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def _read_local_json_list(file_path):
    try:
        if not Path(file_path).exists():
            return []
        parsed = json.loads(Path(file_path).read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _write_local_json_list(data, file_path):
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)
    Path(file_path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def _initialize_persistent_state():
    migrations = [
        (LEGACY_STATE_DIRECTORY / "folders.json", ADDITIONAL_FOLDERS_FILE),
        (LEGACY_STATE_DIRECTORY / "favorites.json", FAVORITES_FILE),
        (LEGACY_ADDITIONAL_FOLDERS_FILE, ADDITIONAL_FOLDERS_FILE),
        (LEGACY_FAVORITES_FILE, FAVORITES_FILE),
    ]

    for legacy_file, persistent_file in migrations:
        try:
            persistent_file.parent.mkdir(parents=True, exist_ok=True)
            if persistent_file.exists():
                continue

            if legacy_file.exists():
                shutil.copy2(legacy_file, persistent_file)
                print(f"[ComfyUI Image Browser] Migrated {legacy_file.name} -> {persistent_file}")
            else:
                _write_local_json_list([], persistent_file)
        except Exception as e:
            print(f"[ComfyUI Image Browser] Failed to initialize {persistent_file}: {e}")

    with _shared_state_file_lock():
        local = {
            "folders": _read_local_json_list(ADDITIONAL_FOLDERS_FILE),
            "favorites": _read_local_json_list(FAVORITES_FILE),
        }
        shared = _read_shared_collections()
        if shared is None:
            shared = local
        _write_shared_collections(shared)
        _write_local_json_list(shared["folders"], ADDITIONAL_FOLDERS_FILE)
        _write_local_json_list(shared["favorites"], FAVORITES_FILE)

# Metadata parsing is kept in a standalone module so the same hardened parser can
# be exercised without importing ComfyUI. The UI intentionally consumes only the
# compact Details and Nodes payloads; no visual Workflow or Raw tabs are added.
try:
    from .metadata import (
        extract_metadata, extract_workflow_payloads_from_file, extract_media_dimensions,
        parse_comfy_metadata, extract_workflow_nodes,
    )
except ImportError:  # Support the isolated regression-test loader.
    import importlib.util
    _metadata_spec = importlib.util.spec_from_file_location(
        "comfyui_image_browser_metadata", MODULE_DIR / "metadata.py"
    )
    _metadata_module = importlib.util.module_from_spec(_metadata_spec)
    _metadata_spec.loader.exec_module(_metadata_module)
    extract_metadata = _metadata_module.extract_metadata
    extract_workflow_payloads_from_file = _metadata_module.extract_workflow_payloads_from_file
    extract_media_dimensions = _metadata_module.extract_media_dimensions
    parse_comfy_metadata = _metadata_module.parse_comfy_metadata
    extract_workflow_nodes = _metadata_module.extract_workflow_nodes

# --- API Endpoints ---
@server.PromptServer.instance.routes.get("/gemini-image-browser/list")
async def list_images(request):
    print("\n--- ComfyUI Image Browser: Received list request ---")
    try:
        page = int(request.query.get('page', 0))
        per_page = int(request.query.get('per_page', 50))
        sort = request.query.get('sort', 'date_desc')
        search = request.query.get('search', '').lower()
        folder_filter = request.query.get('folder', 'default')
        show_favorites = request.query.get('favorites', 'false') == 'true'
        print(f"Params: page={page}, sort='{sort}', search='{search}', folder='{folder_filter}', favorites={show_favorites}")

        output_dir = Path(folder_paths.get_output_directory()).expanduser().resolve()
        favorites = load_json(FAVORITES_FILE)
        additional_folders = load_json(ADDITIONAL_FOLDERS_FILE)

        scan_targets = []
        folder_map = _build_folder_map()
        
        print(f"Folder map contains {len(folder_map)} folder(s).")

        if folder_filter == 'all':
            scan_targets = list(folder_map.items())
        elif folder_filter in folder_map and folder_map[folder_filter].exists():
            scan_targets.append((folder_filter, folder_map[folder_filter]))
        else: # Fallback to default
            scan_targets.append(('default', output_dir))
        
        print(f"Scanning {len(scan_targets)} folder target(s).")

        all_files = []
        for folder_id, scan_path in scan_targets:
            if not scan_path.exists(): 
                print(f"Warning: Scan path {scan_path} does not exist.")
                continue
            for ext in ['*.png', '*.jpg', '*.jpeg', '*.webp', '*.gif', '*.mp4', '*.webm', '*.mov', '*.mp3', '*.wav', '*.ogg', '*.flac', '*.m4a']:
                for p in scan_path.glob(ext):
                    all_files.append({'folder_id': folder_id, 'path_obj': p})
        
        print(f"Found {len(all_files)} total files before filtering.")
        
        file_info = []
        for item in all_files:
            folder_id, path_obj = item['folder_id'], item['path_obj']
            base_dir = folder_map.get(folder_id)
            if not base_dir: continue
            
            relative_path = str(path_obj.resolve().relative_to(base_dir))
            fav_key = f"{folder_id}:{relative_path}"

            if show_favorites and fav_key not in favorites: continue
            if search and search not in path_obj.name.lower(): continue
            
            stat = path_obj.stat()
            file_info.append({
                'name': path_obj.name, 'path': relative_path, 'size': stat.st_size,
                'modified': stat.st_mtime, 'is_favorite': fav_key in favorites,
                'folder_id': folder_id
            })
        
        print(f"Returning {len(file_info)} files after filtering.")

        if sort == 'date_desc': file_info.sort(key=lambda x: x['modified'], reverse=True)
        elif sort == 'date_asc': file_info.sort(key=lambda x: x['modified'])
        elif sort == 'name_asc': file_info.sort(key=lambda x: x['name'].lower())
        elif sort == 'name_desc': file_info.sort(key=lambda x: x['name'].lower(), reverse=True)

        start_index = page * per_page
        paginated_files = file_info[start_index : start_index + per_page]

        print("-------------------------------------------------")
        
        return web.json_response({
            'images': paginated_files,
            'total': len(file_info),
            'has_more': (start_index + per_page) < len(file_info),
            'folders': [{'id': 'default', 'name': 'ComfyUI Output', 'path': str(output_dir)}] + [
                f for f in additional_folders if f.get('id') in folder_map and f.get('id') != 'default'
            ]
        })
    except Exception as e: 
        print(f"Error in list_images: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({'error': str(e)}, status=500)

@server.PromptServer.instance.routes.get("/gemini-image-browser/metadata")
async def get_metadata_endpoint(request):
    try:
        filename = request.query.get('filename')
        folder_id = request.query.get('folder', 'default')
        
        if not filename:
            return web.json_response({'error': 'Missing filename'}, status=400)
        
        base_dir = _resolve_folder_path(folder_id)
        if not base_dir:
            return web.json_response({'error': 'Folder not found'}, status=404)

        file_path = _resolve_file_from_request(base_dir, filename)
        if not file_path:
            return web.json_response({'error': 'Invalid file path'}, status=400)
        
        if not file_path.exists():
            print(f"Metadata: File not found at {file_path}")
            return web.json_response({'error': 'File not found'}, status=404)
        
        # Check if it's a video file
        is_video = file_path.suffix.lower() in ['.mp4', '.webm', '.mov']
        # Check if it's an audio file
        is_audio = file_path.suffix.lower() in ['.mp3', '.wav', '.ogg', '.flac', '.m4a']
        
        # Images expose their text chunks directly. Video/audio files can contain both
        # the saved UI workflow and API prompt graph; keep both because the UI graph is
        # best for node coverage while the API graph is best for generation details.
        raw_metadata = extract_metadata(file_path) if not is_video and not is_audio else {}
        payloads = extract_workflow_payloads_from_file(file_path)
        if payloads.get('ui'):
            raw_metadata['workflow'] = payloads['ui']
        if payloads.get('api'):
            raw_metadata['prompt'] = payloads['api']

        parsed_metadata = parse_comfy_metadata(raw_metadata)
        workflow_nodes = extract_workflow_nodes(raw_metadata)

        dimensions = {'width': None, 'height': None}
        if not is_video and not is_audio and Image:
            try:
                with Image.open(file_path) as img:
                    dimensions = {'width': img.width, 'height': img.height}
            except Exception:
                pass
        elif is_video:
            dimensions = extract_media_dimensions(file_path)

        if dimensions.get('width') and not parsed_metadata.get('width'):
            parsed_metadata['width'] = dimensions['width']
        if dimensions.get('height') and not parsed_metadata.get('height'):
            parsed_metadata['height'] = dimensions['height']
        
        # Debug logging (disabled)
        # print(f"\n{'='*60}")
        # print(f"Metadata Debug for: {filename}")
        # print(f"{'='*60}")
        # print(f"File path: {file_path}")
        # print(f"File exists: {file_path.exists()}")
        # print(f"Raw metadata keys: {list(raw_metadata.keys())}")
        # print(f"Has workflow: {'Yes' if 'workflow' in raw_metadata else 'No'}")
        # print(f"Has parameters: {'Yes' if 'parameters' in raw_metadata else 'No'}")
        # 
        # print(f"\nParsed Metadata:")
        # print(f"  Prompt: {'Found (' + str(len(parsed_metadata['prompt'])) + ' chars)' if parsed_metadata['prompt'] else 'None'}")
        # print(f"  Negative: {'Found (' + str(len(parsed_metadata['negative_prompt'])) + ' chars)' if parsed_metadata['negative_prompt'] else 'None'}")
        # print(f"  Model: {parsed_metadata['model']}")
        # print(f"  Seed: {parsed_metadata['seed']}")
        # print(f"  Steps: {parsed_metadata['steps']}")
        # print(f"  CFG: {parsed_metadata['cfg']}")
        # print(f"  Sampler: {parsed_metadata['sampler']}")
        # print(f"  Scheduler: {parsed_metadata['scheduler']}")
        # print(f"  Dimensions: {parsed_metadata['width']}x{parsed_metadata['height']}")
        # print(f"  LoRAs found: {len(parsed_metadata['loras'])}")
        # for lora in parsed_metadata['loras']:
        #     print(f"    - {lora['name']} (Model: {lora['strength_model']}, CLIP: {lora['strength_clip']})")
        # print(f"{'='*60}\n")
        
        # Return comprehensive metadata response
        return web.json_response({
            'parsed': parsed_metadata,
            'extra': parsed_metadata.get('extras', {}),
            'workflow_nodes': workflow_nodes,
            'dimensions': dimensions,
            'file_info': {
                'size': file_path.stat().st_size, 
                'modified': file_path.stat().st_mtime
            },
            'is_favorite': f"{folder_id}:{filename}" in load_json(FAVORITES_FILE),
            'is_video': is_video,
            'is_audio': is_audio
        })
        
    except Exception as e:
        print(f"Error getting metadata: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({'error': str(e)}, status=500)

def _toggle_favorite(fav_key):
    with _shared_state_file_lock():
        favorites = load_json(FAVORITES_FILE)
        is_favorite = fav_key not in favorites
        if is_favorite:
            favorites.append(fav_key)
        else:
            favorites.remove(fav_key)
        save_json(favorites, FAVORITES_FILE)
    return is_favorite


@server.PromptServer.instance.routes.post("/gemini-image-browser/favorite")
async def toggle_favorite_endpoint(request):
    data = await request.json()
    folder_id = data.get('folder_id')
    filename = data.get('filename')
    if not filename or not folder_id: return web.json_response({'error': 'Missing filename or folder ID'}, status=400)
    
    fav_key = f"{folder_id}:{filename}"
    is_favorite = _toggle_favorite(fav_key)
    return web.json_response({'success': True, 'is_favorite': is_favorite})

@server.PromptServer.instance.routes.post("/gemini-image-browser/delete")
async def delete_image_endpoint(request):
    """Delete an image file"""
    try:
        data = await request.json()
        filename = data.get('filename')
        folder_id = data.get('folder_id', 'default')
        
        if not filename:
            return web.json_response({'error': 'Missing filename'}, status=400)
        
        base_dir = _resolve_folder_path(folder_id)
        if not base_dir:
            return web.json_response({'error': 'Folder not found'}, status=404)

        file_path = _resolve_file_from_request(base_dir, filename)
        if not file_path:
            return web.json_response({'error': 'Invalid file path'}, status=400)
        
        # Check if file exists
        if not file_path.exists():
            return web.json_response({'error': 'File not found'}, status=404)
        
        # Delete the file
        file_path.unlink()
        
        # Also remove from favorites if it was favorited
        fav_key = f"{folder_id}:{filename}"
        with _shared_state_file_lock():
            favorites = load_json(FAVORITES_FILE)
            if fav_key in favorites:
                favorites.remove(fav_key)
                save_json(favorites, FAVORITES_FILE)

        print(f"Deleted file: {file_path}")
        return web.json_response({'success': True})
        
    except Exception as e:
        print(f"Error deleting file: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({'error': str(e)}, status=500)

@server.PromptServer.instance.routes.post("/gemini-image-browser/add_folder")
async def add_folder_endpoint(request):
    data = await request.json()
    path = data.get('path')
    name = data.get('name')
    if not path or not name: return web.json_response({'error': 'Missing path or name'}, status=400)
    try:
        normalized_path = Path(path).expanduser().resolve()
    except Exception:
        return web.json_response({'error': 'Invalid folder path'}, status=400)
    if not normalized_path.exists() or not normalized_path.is_dir():
        return web.json_response({'error': 'Folder does not exist'}, status=400)
    with _shared_state_file_lock():
        folders = load_json(ADDITIONAL_FOLDERS_FILE)
        folder_id = f"folder_{len(folders)}_{os.urandom(2).hex()}"
        new_folder = {'id': folder_id, 'name': name, 'path': str(normalized_path)}
        folders.append(new_folder)
        save_json(folders, ADDITIONAL_FOLDERS_FILE)
    return web.json_response({'success': True, 'folder': new_folder})

@server.PromptServer.instance.routes.post("/gemini-image-browser/remove_folder")
async def remove_folder_endpoint(request):
    data = await request.json()
    folder_id = data.get('folder_id')
    if not folder_id: return web.json_response({'error': 'Missing folder_id'}, status=400)
    
    with _shared_state_file_lock():
        folders = load_json(ADDITIONAL_FOLDERS_FILE)
        folders = [f for f in folders if f.get('id') != folder_id]
        save_json(folders, ADDITIONAL_FOLDERS_FILE)
    return web.json_response({'success': True})

@server.PromptServer.instance.routes.get("/gemini-image-browser/thumbnail")
async def get_thumbnail(request):
    filename = request.query.get('filename')
    folder_id = request.query.get('folder', 'default')
    if not filename: return web.Response(status=400)

    base_dir = _resolve_folder_path(folder_id)
    if not base_dir:
        return web.Response(status=404)

    file_path = _resolve_file_from_request(base_dir, filename)
    if not file_path:
        return web.Response(status=400)
    if not file_path.exists():
        return web.Response(status=404)

    return web.FileResponse(file_path)

# --- UI Routes ---
@server.PromptServer.instance.routes.get("/gemini-image-browser/ui")
async def get_ui(request):
    ui_path = Path(__file__).parent / "ui.html"
    return web.FileResponse(ui_path)

@server.PromptServer.instance.routes.get("/gemini-image-browser/js")
async def get_js(request):
    js_path = Path(__file__).parent / "browser.js"
    return web.FileResponse(js_path)

# --- Node Mappings ---
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

_initialize_persistent_state()

print("--- ComfyUI Image Browser ---")
print(f"Loaded v{__version__}. UI available via button in menu.")
print(f"Data directory: {STATE_DIRECTORY}")
