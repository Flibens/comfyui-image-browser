"""
ComfyUI Gemini Image Browser - Fixed Metadata Version
"""

import os
import json
import re
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
ADDITIONAL_FOLDERS_FILE = Path(__file__).parent / "folders.json"
FAVORITES_FILE = Path(__file__).parent / "favorites.json"

# --- Helper Functions ---
def load_json(file_path):
    if file_path.exists():
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading {file_path}: {e}")
            return []
    return []

def save_json(data, file_path):
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def _safe_str(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="ignore")
        except Exception:
            return f"<{len(value)} bytes>"
    return str(value)

def extract_metadata(image_path):
    """Extract metadata from image (PNG/JPEG/WEBP/etc)"""
    try:
        if not Image or not image_path.exists():
            return {}
        
        with Image.open(image_path) as img:
            metadata = {}
            
            # Extract PNG text chunks (ComfyUI stores workflow here)
            if hasattr(img, 'text'):
                for key, value in img.text.items():
                    metadata[key] = value
            
            # Also check PNG info dict
            if hasattr(img, 'info'):
                for key, value in img.info.items():
                    if key not in metadata:
                        metadata[key] = value

            # EXIF (JPEG/WEBP/etc)
            exif_data = {}
            try:
                if hasattr(img, "_getexif") and img._getexif():
                    for tag, val in img._getexif().items():
                        name = ExifTags.TAGS.get(tag, str(tag)) if Image else str(tag)
                        exif_data[name] = val
            except Exception:
                pass
            if exif_data:
                metadata["EXIF"] = exif_data

            # XMP (if present)
            if "XML:com.adobe.xmp" in metadata:
                metadata["XMP"] = metadata.get("XML:com.adobe.xmp")
            elif "xmp" in metadata:
                metadata["XMP"] = metadata.get("xmp")

            # If ICC profile exists, include a summary only
            if "icc_profile" in metadata:
                icc = metadata.get("icc_profile")
                metadata["ICC Profile"] = f"<{len(icc)} bytes>" if isinstance(icc, (bytes, bytearray)) else _safe_str(icc)
            
            # Try to decode 'prompt' and 'workflow' if they are JSON strings
            for key in ['prompt', 'workflow']:
                if key in metadata and isinstance(metadata[key], str):
                    try:
                        metadata[key] = json.loads(metadata[key])
                    except:
                        pass
            
            return metadata
    except Exception as e:
        print(f"Error extracting metadata from {image_path}: {e}")
        return {}

def _parse_parameters_text(params_text, parsed):
    if not params_text or not isinstance(params_text, str):
        return
    text = params_text.strip()
    if not text:
        return

    # Positive prompt
    pos_match = re.search(r'(?:^|\n)Positive prompt:\s*(.+?)(?=\n(?:Negative prompt:|Steps:|Seed:|Sampler:|CFG|Size:|Model:|$))', text, re.DOTALL)
    if pos_match and not parsed['prompt']:
        parsed['prompt'] = pos_match.group(1).strip()

    # Fallback: first line as prompt if it doesn't look like a param line
    if not parsed['prompt']:
        first_line = text.split('\n', 1)[0].strip()
        if first_line and not re.search(r'^(Steps:|Seed:|Sampler:|CFG|Size:|Model:)', first_line):
            parsed['prompt'] = first_line

    # Negative prompt
    neg_match = re.search(r'(?:^|\n)Negative prompt:\s*(.+?)(?=\n(?:Steps:|Seed:|Sampler:|CFG|Size:|Model:|$))', text, re.DOTALL)
    if neg_match and not parsed['negative_prompt']:
        parsed['negative_prompt'] = neg_match.group(1).strip()

    # Standard parameters
    steps_match = re.search(r'Steps:\s*(\d+)', text)
    if steps_match and not parsed['steps']:
        parsed['steps'] = int(steps_match.group(1))

    sampler_match = re.search(r'Sampler:\s*([^,\n]+)', text)
    if sampler_match and not parsed['sampler']:
        parsed['sampler'] = sampler_match.group(1).strip()

    scheduler_match = re.search(r'Scheduler:\s*([^,\n]+)', text)
    if scheduler_match and not parsed['scheduler']:
        parsed['scheduler'] = scheduler_match.group(1).strip()

    cfg_match = re.search(r'CFG scale:\s*([\d.]+)', text) or re.search(r'CfgScale:\s*([\d.]+)', text)
    if cfg_match and not parsed['cfg']:
        parsed['cfg'] = float(cfg_match.group(1))

    seed_match = re.search(r'Seed:\s*(\d+)', text)
    if seed_match and not parsed['seed']:
        parsed['seed'] = int(seed_match.group(1))

    size_match = re.search(r'Size:\s*(\d+)x(\d+)', text)
    if size_match and not parsed['width'] and not parsed['height']:
        parsed['width'] = int(size_match.group(1))
        parsed['height'] = int(size_match.group(2))

    model_match = re.search(r'Model:\s*([^,\n]+)', text)
    if model_match and not parsed['model']:
        parsed['model'] = model_match.group(1).strip()

    # Extra fields
    extra_fields = {
        "Model hash": r'Model hash:\s*([^,\n]+)',
        "VAE": r'VAE:\s*([^,\n]+)',
        "VAE hash": r'VAE hash:\s*([^,\n]+)',
        "Clip skip": r'Clip skip:\s*([^,\n]+)',
        "Denoising strength": r'Denoising strength:\s*([\d.]+)',
        "Hires steps": r'Hires steps:\s*([^,\n]+)',
        "Hires upscale": r'Hires upscale:\s*([^,\n]+)',
        "Hires resize": r'Hires resize:\s*([^,\n]+)',
        "Refiner": r'Refiner:\s*([^,\n]+)',
        "Refiner switch": r'Refiner switch:\s*([^,\n]+)',
        "Version": r'Version:\s*([^,\n]+)',
        "RNG": r'RNG:\s*([^,\n]+)'
    }
    for label, pattern in extra_fields.items():
        m = re.search(pattern, text)
        if m:
            parsed['extras'][label] = m.group(1).strip()

    # Extract LoRAs from prompt text
    if parsed['prompt']:
        lora_tags = re.findall(r'<lora:([^:>]+):([\d.]+)>', parsed['prompt'])
        for lora_name, strength in lora_tags:
            lora_name = lora_name.strip()
            if lora_name and lora_name not in ['None', '', 'ComfyUI']:
                if not any(l['name'] == lora_name for l in parsed['loras']):
                    parsed['loras'].append({
                        'name': lora_name,
                        'strength_model': float(strength),
                        'strength_clip': float(strength)
                    })

def parse_comfy_metadata(metadata):
    """Parse ComfyUI/SD metadata with broad heuristics"""
    parsed = {
        'prompt': None, 
        'negative_prompt': None, 
        'seed': None,
        'steps': None, 
        'cfg': None, 
        'sampler': None,
        'scheduler': None, 
        'model': None, 
        'loras': [],
        'width': None, 
        'height': None,
        'extras': {}
    }
    
    try:
        # Try any known text-like fields first
        for key in ['parameters', 'Comment', 'Description', 'UserComment', 'ImageDescription', 'Software', 'Prompt', 'prompt', 'notes']:
            if key in metadata and isinstance(metadata[key], str):
                _parse_parameters_text(metadata[key], parsed)

        # If there is EXIF, scan for prompt-like text
        exif = metadata.get('EXIF')
        if isinstance(exif, dict):
            for k, v in exif.items():
                if isinstance(v, str) and any(x in v for x in ['Steps:', 'Sampler:', 'Negative prompt:', 'CFG']):
                    _parse_parameters_text(v, parsed)

        # First check if there's a 'parameters' field (Automatic1111/Forge/WebUI format)
        if 'parameters' in metadata:
            params_text = metadata['parameters']
            if isinstance(params_text, str):
                lines = params_text.split('\n')
                full_text = '\n'.join(lines[1:]) if len(lines) > 1 else ''

                # Also extract from Lora hashes field
                lora_match = re.search(r'Lora hashes:\s*"([^"]+)"', full_text)
                if lora_match:
                    lora_text = lora_match.group(1)
                    lora_pairs = lora_text.split(',')
                    for pair in lora_pairs:
                        if ':' in pair:
                            lora_name = pair.split(':')[0].strip()
                            # Only add valid LoRA names and avoid duplicates
                            if lora_name and lora_name not in ['None', '', 'ComfyUI']:
                                if not any(l['name'] == lora_name for l in parsed['loras']):
                                    # Try to extract strength from prompt
                                    strength_match = re.search(rf'<lora:{re.escape(lora_name)}:([\d.]+)>', parsed['prompt'] or '')
                                    strength = float(strength_match.group(1)) if strength_match else 1.0
                                    
                                    parsed['loras'].append({
                                        'name': lora_name,
                                        'strength_model': strength,
                                        'strength_clip': strength
                                    })
        
        # If parameters parsing didn't work, try ComfyUI workflow format
        if not parsed['prompt'] or len(parsed['loras']) == 0:
            workflow = metadata.get('workflow', {})
            if isinstance(workflow, str):
                workflow = json.loads(workflow)
            
            if workflow and isinstance(workflow, dict):
                nodes = workflow.get('nodes', [])
                
                for node in nodes:
                    node_type = node.get('type', '')
                    widgets = node.get('widgets_values', [])
                    inputs = node.get('inputs', {})
                    title = node.get('title', '').lower()
                    
                    # Extract prompts
                    if 'CLIPTextEncode' in node_type or 'Conditioning' in node_type or 'PromptText' in node_type:
                        text = widgets[0] if widgets and isinstance(widgets[0], str) else inputs.get('text')
                        if text:
                            # Check if it's negative prompt
                            is_negative = any(word in title for word in ['negative', 'neg'])
                            if is_negative and not parsed['negative_prompt']:
                                parsed['negative_prompt'] = text
                            elif not is_negative and not parsed['prompt']:
                                parsed['prompt'] = text
                    
                    # Extract sampler settings
                    elif 'KSampler' in node_type:
                        if not parsed['seed']:
                            parsed['seed'] = widgets[0] if widgets else inputs.get('seed')
                        if not parsed['steps']:
                            parsed['steps'] = widgets[1] if len(widgets) > 1 else inputs.get('steps')
                        if not parsed['cfg']:
                            parsed['cfg'] = widgets[2] if len(widgets) > 2 else inputs.get('cfg')
                        if not parsed['sampler']:
                            parsed['sampler'] = widgets[3] if len(widgets) > 3 else inputs.get('sampler_name')
                        if not parsed['scheduler']:
                            parsed['scheduler'] = widgets[4] if len(widgets) > 4 else inputs.get('scheduler')
                    
                    # Extract model
                    elif 'CheckpointLoader' in node_type or 'CheckpointLoaderSimple' in node_type:
                        if not parsed['model']:
                            parsed['model'] = widgets[0] if widgets else inputs.get('ckpt_name')
                    
                    # Extract LoRAs - COMPREHENSIVE DETECTION
                    elif any(kw in node_type.lower() for kw in ['lora', 'loraloader', 'lora stacker', 'lora_stack']):
                        try:
                            # LoRA Manager format - JSON objects with 'active' flag
                            if widgets and isinstance(widgets[0], list):
                                lora_list = widgets[0]
                                for lora_obj in lora_list:
                                    if isinstance(lora_obj, dict):
                                        lora_name = lora_obj.get('name')
                                        is_active = lora_obj.get('active', True)
                                        strength = lora_obj.get('strength', 1.0)
                                        clip_strength = lora_obj.get('clipStrength', 1.0)
                                        
                                        # Only add if active, has a valid name, and avoid invalid names
                                        if (lora_name and 
                                            lora_name not in ['None', '', 'ComfyUI'] and 
                                            is_active):
                                            # Check if already added
                                            if not any(l['name'] == lora_name for l in parsed['loras']):
                                                parsed['loras'].append({
                                                    'name': lora_name,
                                                    'strength_model': strength,
                                                    'strength_clip': clip_strength
                                                })
                            
                            # Standard LoRA Loader format (name, strength_model, strength_clip)
                            else:
                                lora_name = widgets[0] if widgets else inputs.get('lora_name')
                                # Only add valid LoRA names
                                if lora_name and lora_name not in ['None', '', 'ComfyUI']:
                                    # Check if already added
                                    if not any(l['name'] == lora_name for l in parsed['loras']):
                                        parsed['loras'].append({
                                            'name': lora_name,
                                            'strength_model': widgets[1] if len(widgets) > 1 else inputs.get('strength_model', 1.0),
                                            'strength_clip': widgets[2] if len(widgets) > 2 else inputs.get('strength_clip', 1.0)
                                        })
                        
                        except Exception as e:
                            print(f"Error parsing LoRAs from node {node_type}: {e}")
                    
                    # Extract dimensions
                    elif 'EmptyLatentImage' in node_type:
                        if not parsed['width']:
                            parsed['width'] = widgets[0] if widgets else inputs.get('width')
                        if not parsed['height']:
                            parsed['height'] = widgets[1] if len(widgets) > 1 else inputs.get('height')

        # Try to parse raw prompt JSON (if prompt is a dict)
        if isinstance(metadata.get('prompt'), dict) and not parsed['prompt']:
            for node_id, node in metadata['prompt'].items():
                inputs = node.get('inputs', {})
                text = inputs.get('text')
                if isinstance(text, str) and text.strip():
                    parsed['prompt'] = text.strip()
                    break
    
    except Exception as e:
        print(f"Error parsing metadata: {e}")
        import traceback
        traceback.print_exc()
    
    return parsed

def flatten_metadata(metadata):
    flat = {}
    for key, value in metadata.items():
        if key == 'workflow':
            continue
        if isinstance(value, dict):
            for k2, v2 in value.items():
                flat[f"{key}.{k2}"] = _safe_str(v2)
        else:
            flat[key] = _safe_str(value)
    return flat

# --- API Endpoints ---
@server.PromptServer.instance.routes.get("/gemini-image-browser/list")
async def list_images(request):
    print("\n--- Gemini Image Browser: Received list request ---")
    try:
        page = int(request.query.get('page', 0))
        per_page = int(request.query.get('per_page', 50))
        sort = request.query.get('sort', 'date_desc')
        search = request.query.get('search', '').lower()
        folder_filter = request.query.get('folder', 'default')
        show_favorites = request.query.get('favorites', 'false') == 'true'
        print(f"Params: page={page}, sort='{sort}', search='{search}', folder='{folder_filter}', favorites={show_favorites}")

        output_dir = Path(folder_paths.get_output_directory())
        favorites = load_json(FAVORITES_FILE)
        additional_folders = load_json(ADDITIONAL_FOLDERS_FILE)

        scan_targets = []
        folder_map = {'default': output_dir}
        for f in additional_folders:
            folder_map[f['id']] = Path(f['path'])
        
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
            
            relative_path = str(path_obj.relative_to(base_dir))
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
            'folders': [{'id': 'default', 'name': 'ComfyUI Output', 'path': str(output_dir)}] + additional_folders
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
        
        # Determine which folder to use
        base_dir = Path(folder_paths.get_output_directory())
        if folder_id != 'default':
            folders = load_json(ADDITIONAL_FOLDERS_FILE)
            folder_data = next((f for f in folders if f['id'] == folder_id), None)
            if folder_data:
                base_dir = Path(folder_data['path'])
        
        file_path = base_dir / filename
        
        if not file_path.exists():
            print(f"Metadata: File not found at {file_path}")
            return web.json_response({'error': 'File not found'}, status=404)
        
        # Check if it's a video file
        is_video = file_path.suffix.lower() in ['.mp4', '.webm', '.mov']
        # Check if it's an audio file
        is_audio = file_path.suffix.lower() in ['.mp3', '.wav', '.ogg', '.flac', '.m4a']
        
        if is_video or is_audio:
            return web.json_response({
                'parsed': {
                    'prompt': None, 'negative_prompt': None, 'seed': None,
                    'steps': None, 'cfg': None, 'sampler': None,
                    'scheduler': None, 'model': None, 'loras': [],
                    'width': None, 'height': None
                },
                'raw': {},
                'dimensions': {'width': 'N/A', 'height': 'N/A'},
                'file_info': {'size': file_path.stat().st_size, 'modified': file_path.stat().st_mtime},
                'is_favorite': f"{folder_id}:{filename}" in load_json(FAVORITES_FILE),
                'is_video': is_video,
                'is_audio': is_audio
            })
        
        # Extract raw metadata
        raw_metadata = extract_metadata(file_path)
        
        # Parse metadata
        parsed_metadata = parse_comfy_metadata(raw_metadata)
        
        # Get image dimensions
        dimensions = {}
        if Image:
            try:
                with Image.open(file_path) as img:
                    dimensions = {'width': img.width, 'height': img.height}
                    # Fill in parsed dimensions if missing
                    if not parsed_metadata.get('width'):
                        parsed_metadata['width'] = img.width
                    if not parsed_metadata.get('height'):
                        parsed_metadata['height'] = img.height
            except:
                dimensions = {'width': 'N/A', 'height': 'N/A'}
        else:
            dimensions = {'width': 'N/A', 'height': 'N/A'}
        
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
            'raw': {k: (v[:500] + "…") if len(v) > 500 else v
                    for k, v in flatten_metadata(raw_metadata).items()},
            'extra': parsed_metadata.get('extras', {}),
            'dimensions': dimensions,
            'file_info': {
                'size': file_path.stat().st_size, 
                'modified': file_path.stat().st_mtime
            },
            'is_favorite': f"{folder_id}:{filename}" in load_json(FAVORITES_FILE),
            'is_video': False
        })
        
    except Exception as e:
        print(f"Error getting metadata: {e}")
        import traceback
        traceback.print_exc()
        return web.json_response({'error': str(e)}, status=500)

@server.PromptServer.instance.routes.post("/gemini-image-browser/favorite")
async def toggle_favorite_endpoint(request):
    data = await request.json()
    folder_id = data.get('folder_id')
    filename = data.get('filename')
    if not filename or not folder_id: return web.json_response({'error': 'Missing filename or folder ID'}, status=400)
    
    fav_key = f"{folder_id}:{filename}"
    favorites = load_json(FAVORITES_FILE)
    is_favorite = fav_key not in favorites
    if is_favorite:
        favorites.append(fav_key)
    else:
        favorites.remove(fav_key)
    save_json(favorites, FAVORITES_FILE)
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
        
        # Get the base directory
        base_dir = Path(folder_paths.get_output_directory())
        if folder_id != 'default':
            folders = load_json(ADDITIONAL_FOLDERS_FILE)
            folder_data = next((f for f in folders if f['id'] == folder_id), None)
            if not folder_data:
                return web.json_response({'error': 'Folder not found'}, status=404)
            base_dir = Path(folder_data['path'])
        
        # Construct the full file path
        file_path = base_dir / filename
        
        # Security check: ensure the file is within the allowed directory
        if not file_path.resolve().is_relative_to(base_dir.resolve()):
            return web.json_response({'error': 'Invalid file path'}, status=400)
        
        # Check if file exists
        if not file_path.exists():
            return web.json_response({'error': 'File not found'}, status=404)
        
        # Delete the file
        file_path.unlink()
        
        # Also remove from favorites if it was favorited
        fav_key = f"{folder_id}:{filename}"
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
    if not Path(path).exists(): return web.json_response({'error': 'Folder does not exist'}, status=400)
    
    folders = load_json(ADDITIONAL_FOLDERS_FILE)
    folder_id = f"folder_{len(folders)}_{os.urandom(2).hex()}"
    new_folder = {'id': folder_id, 'name': name, 'path': path}
    folders.append(new_folder)
    save_json(folders, ADDITIONAL_FOLDERS_FILE)
    return web.json_response({'success': True, 'folder': new_folder})

@server.PromptServer.instance.routes.post("/gemini-image-browser/remove_folder")
async def remove_folder_endpoint(request):
    data = await request.json()
    folder_id = data.get('folder_id')
    if not folder_id: return web.json_response({'error': 'Missing folder_id'}, status=400)
    
    folders = load_json(ADDITIONAL_FOLDERS_FILE)
    folders = [f for f in folders if f.get('id') != folder_id]
    save_json(folders, ADDITIONAL_FOLDERS_FILE)
    return web.json_response({'success': True})

@server.PromptServer.instance.routes.get("/gemini-image-browser/thumbnail")
async def get_thumbnail(request):
    filename = request.query.get('filename')
    folder_id = request.query.get('folder', 'default')
    if not filename: return web.Response(status=400)

    base_dir = Path(folder_paths.get_output_directory())
    if folder_id != 'default':
        folders = load_json(ADDITIONAL_FOLDERS_FILE)
        folder_data = next((f for f in folders if f['id'] == folder_id), None)
        if not folder_data: return web.Response(status=404)
        base_dir = Path(folder_data['path'])

    return web.FileResponse(base_dir / filename)

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

print("--- Gemini Image Browser ---")
print("Loaded. UI available via button in menu.")
