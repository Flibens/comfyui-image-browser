"""
ComfyUI Gemini Image Browser - Fixed Metadata Version
"""

import os
import json
import re
import shutil
import subprocess
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
        if resolved.exists() and resolved.is_dir() and _is_under_allowed_roots(resolved):
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

def _validate_and_get_workflow(json_string):
    try:
        data = json.loads(json_string)
        workflow_data = data.get('workflow', data.get('prompt', data))

        if isinstance(workflow_data, dict):
            if 'nodes' in workflow_data:
                return workflow_data, 'ui'

            is_api = False
            for _, value in workflow_data.items():
                if isinstance(value, dict) and 'class_type' in value:
                    is_api = True
                    break
            if is_api:
                return workflow_data, 'api'
    except Exception:
        pass
    return None, None

def _scan_bytes_for_workflow(content_bytes):
    """
    Yield valid JSON object strings found in a binary stream by brace matching.
    """
    try:
        stream_str = content_bytes.decode('utf-8', errors='ignore')
    except Exception:
        return

    start_pos = 0
    while True:
        first_brace = stream_str.find('{', start_pos)
        if first_brace == -1:
            break

        open_braces = 0
        start_index = first_brace

        for i in range(start_index, len(stream_str)):
            char = stream_str[i]
            if char == '{':
                open_braces += 1
            elif char == '}':
                open_braces -= 1

            if open_braces == 0:
                candidate = stream_str[start_index:i + 1]
                try:
                    json.loads(candidate)
                    yield candidate
                except Exception:
                    pass
                start_pos = i + 1
                break
        else:
            break

def extract_workflow_from_file(file_path):
    """
    Extract workflow payload from image/video/audio files.
    Returns (workflow_dict_or_none, 'ui'|'api'|None).
    """
    found = {}

    def analyze_json(json_str):
        wf, wf_type = _validate_and_get_workflow(json_str)
        if wf and wf_type and wf_type not in found:
            found[wf_type] = wf

    ext_lower = str(file_path).lower()
    is_media = ext_lower.endswith(('.mp4', '.mkv', '.webm', '.mov', '.avi', '.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac'))

    # Video/audio metadata tags via ffprobe (primary path for many encoded files)
    if is_media:
        ffprobe_bin = os.environ.get('FFPROBE_PATH') or shutil.which('ffprobe')
        if ffprobe_bin:
            try:
                cmd = [ffprobe_bin, '-v', 'quiet', '-print_format', 'json', '-show_format', str(file_path)]
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='ignore',
                    check=True,
                    creationflags=creationflags
                )
                ff_data = json.loads(result.stdout)
                tags = ff_data.get('format', {}).get('tags', {})
                if isinstance(tags, dict):
                    for value in tags.values():
                        if not isinstance(value, str):
                            continue
                        if value.strip().startswith('{'):
                            analyze_json(value)
                        elif '{' in value:
                            for json_str in _scan_bytes_for_workflow(value.encode('utf-8', errors='ignore')):
                                analyze_json(json_str)
            except Exception:
                pass

    # Fast image metadata path (PNG/JPG/WEBP text chunks, EXIF)
    if Image:
        try:
            with Image.open(file_path) as img:
                for key in ['workflow', 'prompt']:
                    value = img.info.get(key)
                    if isinstance(value, str) and value.strip().startswith('{'):
                        analyze_json(value)

                exif_data = img.info.get('exif')
                if isinstance(exif_data, bytes):
                    for json_str in _scan_bytes_for_workflow(exif_data):
                        analyze_json(json_str)
        except Exception:
            pass

    # Ultimate fallback: scan raw bytes (works for video/audio metadata blobs too)
    if not found:
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            for json_str in _scan_bytes_for_workflow(content):
                analyze_json(json_str)
                if 'ui' in found and 'api' in found:
                    break
        except Exception:
            pass

    if 'ui' in found:
        return found['ui'], 'ui'
    if 'api' in found:
        return found['api'], 'api'
    return None, None

def _parse_parameters_text(params_text, parsed):
    if not params_text or not isinstance(params_text, str):
        return
    text = params_text.strip()
    if not text:
        return

    # Positive prompt (explicit label, if present)
    pos_match = re.search(
        r'(?:^|\n)Positive prompt:\s*(.+?)(?=\n(?:Negative prompt:|Steps:|Seed:|Sampler:|CFG(?: scale)?:|CfgScale:|Size:|Model:|Scheduler:|$))',
        text,
        re.DOTALL,
    )
    if pos_match and not parsed['prompt']:
        parsed['prompt'] = pos_match.group(1).strip()

    # Fallback: extract unlabeled positive prompt block (common in A1111/Forge parameters)
    if not parsed['prompt']:
        marker_match = re.search(
            r'(?m)^\s*(Negative prompt:|Steps:|Seed:|Sampler:|CFG(?: scale)?:|CfgScale:|Size:|Model:|Scheduler:)',
            text,
        )
        prompt_block = text[:marker_match.start()] if marker_match else text
        prompt_block = prompt_block.strip()
        first_line = prompt_block.split('\n', 1)[0].strip() if prompt_block else ''
        if prompt_block and not re.search(
            r'^(Steps:|Seed:|Sampler:|CFG(?: scale)?:|CfgScale:|Size:|Model:|Scheduler:)',
            first_line,
        ):
            parsed['prompt'] = prompt_block

    # Negative prompt
    neg_match = re.search(
        r'(?:^|\n)Negative prompt:\s*(.+?)(?=\n(?:Steps:|Seed:|Sampler:|CFG(?: scale)?:|CfgScale:|Size:|Model:|Scheduler:|$))',
        text,
        re.DOTALL,
    )
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
    if text:
        lora_tags = re.findall(r'<lora:([^:>]+):([\d.]+)>', text)
        for lora_name, strength in lora_tags:
            lora_name = lora_name.strip()
            if lora_name and lora_name not in ['None', '', 'ComfyUI']:
                if not any(l['name'] == lora_name for l in parsed['loras']):
                    parsed['loras'].append({
                        'name': lora_name,
                        'strength_model': float(strength),
                        'strength_clip': float(strength)
                    })

def _sanitize_prompt_text(prompt_text):
    """Remove inline LoRA tags from display prompt while preserving line structure."""
    if not isinstance(prompt_text, str):
        return prompt_text

    cleaned = re.sub(r'[ \t]*<lora:[^>]+>[ \t]*', ' ', prompt_text, flags=re.IGNORECASE)
    out_lines = []
    for line in cleaned.splitlines():
        line = re.sub(r'[ \t]+', ' ', line).strip()
        line = re.sub(r'\s+,', ',', line)
        line = re.sub(r',\s*,', ',', line)
        if line:
            out_lines.append(line)
    return '\n'.join(out_lines).strip()

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
        negative_prompt_keys = {'Negative prompt', 'negative_prompt', 'negativePrompt'}
        for key in [
            'parameters',
            'Comment',
            'Description',
            'UserComment',
            'ImageDescription',
            'Software',
            'Prompt',
            'prompt',
            'Negative prompt',
            'negative_prompt',
            'negativePrompt',
            'notes',
        ]:
            if key in metadata and isinstance(metadata[key], str):
                if key in negative_prompt_keys and not parsed['negative_prompt']:
                    parsed['negative_prompt'] = metadata[key].strip()
                    continue
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

        # Parse API prompt graph JSON (ComfyUI default Save Image metadata)
        if isinstance(metadata.get('prompt'), dict) and (not parsed['prompt'] or not parsed['negative_prompt']):
            prompt_graph = metadata['prompt']

            def _linked_text(link_ref):
                if not isinstance(link_ref, (list, tuple)) or len(link_ref) == 0:
                    return None
                node_key = str(link_ref[0])
                ref_node = prompt_graph.get(node_key)
                if not isinstance(ref_node, dict):
                    return None
                ref_inputs = ref_node.get('inputs', {})
                if not isinstance(ref_inputs, dict):
                    return None
                ref_text = ref_inputs.get('text')
                if isinstance(ref_text, str) and ref_text.strip():
                    return ref_text.strip()
                return None

            # Preferred path: resolve positive/negative links from KSampler-like nodes
            for _, node in prompt_graph.items():
                if not isinstance(node, dict):
                    continue
                node_type = str(node.get('class_type') or node.get('type') or '')
                if 'KSampler' not in node_type:
                    continue
                node_inputs = node.get('inputs', {})
                if not isinstance(node_inputs, dict):
                    continue

                if not parsed['prompt']:
                    positive_text = _linked_text(node_inputs.get('positive'))
                    if positive_text:
                        parsed['prompt'] = positive_text

                if not parsed['negative_prompt']:
                    negative_text = _linked_text(node_inputs.get('negative'))
                    if negative_text:
                        parsed['negative_prompt'] = negative_text

                if parsed['prompt'] and parsed['negative_prompt']:
                    break

            # Fallback: scan CLIP text nodes if links were unavailable
            if not parsed['prompt'] or not parsed['negative_prompt']:
                clip_nodes = []
                for _, node in prompt_graph.items():
                    if not isinstance(node, dict):
                        continue
                    node_type = str(node.get('class_type') or node.get('type') or '')
                    if 'CLIPTextEncode' not in node_type:
                        continue
                    node_inputs = node.get('inputs', {})
                    if not isinstance(node_inputs, dict):
                        continue
                    text = node_inputs.get('text')
                    if not (isinstance(text, str) and text.strip()):
                        continue
                    title = str((node.get('_meta') or {}).get('title', '')).lower()
                    clip_nodes.append((text.strip(), title))

                if not parsed['negative_prompt']:
                    for text, title in clip_nodes:
                        if any(word in title for word in ['negative', 'neg']):
                            parsed['negative_prompt'] = text
                            break

                if not parsed['prompt'] and clip_nodes:
                    for text, title in clip_nodes:
                        if not any(word in title for word in ['negative', 'neg']):
                            parsed['prompt'] = text
                            break

                # Last resort: if one side is still missing, use the remaining distinct CLIP text
                if clip_nodes and (not parsed['prompt'] or not parsed['negative_prompt']):
                    for text, _ in clip_nodes:
                        if not parsed['prompt']:
                            parsed['prompt'] = text
                            continue
                        if not parsed['negative_prompt'] and text != parsed['prompt']:
                            parsed['negative_prompt'] = text
                            break
    
    except Exception as e:
        print(f"Error parsing metadata: {e}")
        import traceback
        traceback.print_exc()

    # Final normalization: prompt display should not duplicate LoRA entries.
    parsed['prompt'] = _sanitize_prompt_text(parsed.get('prompt'))
    parsed['negative_prompt'] = _sanitize_prompt_text(parsed.get('negative_prompt'))

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

def _normalize_node_params_from_ui_node(node):
    params = []

    inputs = node.get('inputs', {})
    if isinstance(inputs, list):
        for idx, item in enumerate(inputs):
            if isinstance(item, dict):
                name = item.get('name') or f"input_{idx + 1}"
                value = item.get('widget', item.get('value', item.get('link')))
                params.append({'name': str(name), 'value': _safe_str(value)})
    elif isinstance(inputs, dict):
        for name, value in inputs.items():
            params.append({'name': str(name), 'value': _safe_str(value)})

    widgets = node.get('widgets_values', [])
    if isinstance(widgets, list):
        for idx, value in enumerate(widgets):
            params.append({'name': f"widget_{idx + 1}", 'value': _safe_str(value)})

    return params

def extract_workflow_nodes(metadata):
    """Extract workflow nodes for sidebar node inspector (UI and API workflow formats)."""
    workflow_data = metadata.get('workflow')
    prompt_data = metadata.get('prompt')
    source = workflow_data if workflow_data is not None else prompt_data

    if isinstance(source, str):
        try:
            source = json.loads(source)
        except Exception:
            return []

    nodes_out = []

    # UI format: {"nodes":[...]}
    if isinstance(source, dict) and isinstance(source.get('nodes'), list):
        for node in source.get('nodes', []):
            if not isinstance(node, dict):
                continue
            if node.get('mode', 0) != 0:
                continue
            node_id = node.get('id', 'N/A')
            node_type = node.get('type') or node.get('class_type') or 'Unknown'
            nodes_out.append({
                'id': _safe_str(node_id),
                'type': _safe_str(node_type),
                'params': _normalize_node_params_from_ui_node(node)
            })
    # API format: {"3":{"class_type":"KSampler","inputs":{...}}, ...}
    elif isinstance(source, dict):
        for node_id, node in source.items():
            if not isinstance(node, dict) or 'class_type' not in node:
                continue
            params = []
            inputs = node.get('inputs', {})
            if isinstance(inputs, dict):
                for name, value in inputs.items():
                    params.append({'name': str(name), 'value': _safe_str(value)})
            nodes_out.append({
                'id': _safe_str(node_id),
                'type': _safe_str(node.get('class_type', 'Unknown')),
                'params': params
            })

    def _sort_key(item):
        node_id = item.get('id', '')
        try:
            return (0, int(node_id))
        except Exception:
            return (1, str(node_id))

    nodes_out.sort(key=_sort_key)
    return nodes_out

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
        
        # Extract metadata for images, and workflow payload for video/audio
        raw_metadata = {}
        if not is_video and not is_audio:
            raw_metadata = extract_metadata(file_path)

        workflow_obj, workflow_type = extract_workflow_from_file(file_path)
        if workflow_obj:
            if workflow_type == 'ui':
                raw_metadata['workflow'] = workflow_obj
            else:
                raw_metadata['prompt'] = workflow_obj
        
        # Parse metadata
        parsed_metadata = parse_comfy_metadata(raw_metadata)
        workflow_nodes = extract_workflow_nodes(raw_metadata)
        
        # Get media dimensions
        dimensions = {}
        if not is_video and not is_audio and Image:
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
    if not _is_under_allowed_roots(normalized_path):
        return web.json_response({'error': 'Folder path is not allowed'}, status=400)
    
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

print("--- Gemini Image Browser ---")
print("Loaded. UI available via button in menu.")
