"""Metadata extraction adapted from ComfyUI Image Browser v1.6.0.
Standalone: no ComfyUI imports or running server required.
"""

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from itertools import islice
from pathlib import Path

try:
    from PIL import Image, ExifTags
except ImportError:
    Image = None
    ExifTags = None

WORKFLOW_MAX_NODES = 5_000
WORKFLOW_MAX_LINKS = 20_000
WORKFLOW_MAX_SUBGRAPHS = 128
WORKFLOW_MAX_PORTS = 512
WORKFLOW_MAX_TOTAL_PORTS = 40_000
WORKFLOW_MAX_PARAMS = 64
WORKFLOW_MAX_TEXT_LENGTH = 2_048
WORKFLOW_MAX_PROMPT_LENGTH = 20_000
WORKFLOW_MAX_ID_LENGTH = 512
WORKFLOW_MAX_LORAS = 128
WORKFLOW_RAW_SCAN_BYTES = 16 * 1024 * 1024
WORKFLOW_MAX_EMBEDDED_JSON_BYTES = 4 * 1024 * 1024
WORKFLOW_MAX_RAW_DISPLAY_BYTES = 8 * 1024 * 1024
WORKFLOW_MAX_SCAN_CANDIDATES = 32
WORKFLOW_MAX_METADATA_ENTRIES = 256
WORKFLOW_MAX_RESOLVE_STEPS = 80_000
WORKFLOW_MAX_RESOLVER_CACHE = 512
_RESOLVER_MEMO_KEY = object()

def _safe_str(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="ignore")
        except Exception:
            return f"<{len(value)} bytes>"
    return str(value)


def _finite_json_value(value, depth=0):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if depth >= 8:
        return _workflow_text(value)
    if isinstance(value, dict):
        return {
            _workflow_text(key): _finite_json_value(item, depth + 1)
            for key, item in islice(value.items(), 256)
        }
    if isinstance(value, (list, tuple)):
        return [_finite_json_value(item, depth + 1) for item in islice(value, 256)]
    return _workflow_text(value)


def raw_metadata_for_display(metadata):
    """Return bounded, strict-JSON metadata while retaining workflow and prompt trees."""
    remaining_text = [WORKFLOW_MAX_RAW_DISPLAY_BYTES]

    def bounded_text(value):
        candidate = value[:WORKFLOW_MAX_PROMPT_LENGTH]
        encoded = candidate.encode("utf-8", errors="ignore")
        if len(encoded) <= remaining_text[0]:
            remaining_text[0] -= len(encoded)
            return candidate
        prefix = encoded[:remaining_text[0]].decode("utf-8", errors="ignore")
        remaining_text[0] -= len(prefix.encode("utf-8"))
        return prefix

    def clean(value, depth=0):
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, str):
            return bounded_text(value)
        if isinstance(value, bytes):
            return clean(value.decode("utf-8", errors="ignore"), depth)
        if isinstance(value, (int, bool)) or value is None:
            return value
        if depth >= 12:
            return clean(_workflow_text(value), depth)
        if isinstance(value, dict):
            result = {}
            priority = ("CreationTime", "prompt", "workflow", "parameters") if depth == 0 else ()
            if priority:
                for key in priority:
                    if key in value and len(result) < WORKFLOW_MAX_METADATA_ENTRIES:
                        result[key] = clean(value[key], depth + 1)
            for key, item in value.items():
                if key in priority or len(result) >= WORKFLOW_MAX_METADATA_ENTRIES:
                    continue
                if remaining_text[0] <= 0:
                    break
                result[_workflow_text(key)] = clean(item, depth + 1)
            return result
        if isinstance(value, (list, tuple)):
            result = []
            for item in islice(value, WORKFLOW_MAX_METADATA_ENTRIES):
                if remaining_text[0] <= 0:
                    break
                result.append(clean(item, depth + 1))
            return result
        return clean(_workflow_text(value), depth)

    return clean(metadata)


def _workflow_text(value):
    def preview(item, depth=0):
        if item is None:
            return ""
        if isinstance(item, str):
            return item[:WORKFLOW_MAX_TEXT_LENGTH]
        if isinstance(item, bytes):
            return item[:WORKFLOW_MAX_TEXT_LENGTH].decode("utf-8", errors="ignore")
        if isinstance(item, dict):
            if depth >= 2:
                return f"<{len(item)} values>"
            parts = [
                f"{preview(key, depth + 1)[:64]}: {preview(nested, depth + 1)}"
                for key, nested in islice(item.items(), 8)
            ]
            if len(item) > 8:
                parts.append(f"… {len(item) - 8} more")
            return "{" + ", ".join(parts) + "}"
        if isinstance(item, (list, tuple, set)):
            if depth >= 2:
                return f"<{len(item)} values>"
            parts = [preview(nested, depth + 1) for nested in islice(iter(item), 8)]
            if len(item) > 8:
                parts.append(f"… {len(item) - 8} more")
            return "[" + ", ".join(parts) + "]"
        return _safe_str(item)[:WORKFLOW_MAX_TEXT_LENGTH]

    return preview(value)[:WORKFLOW_MAX_TEXT_LENGTH]


def _workflow_id(value):
    text = value if isinstance(value, str) else _workflow_text(value)
    if len(text) <= WORKFLOW_MAX_ID_LENGTH:
        return text
    sample = f"{text[:4096]}|{text[-256:]}|{len(text)}"
    digest = hashlib.sha256(sample.encode("utf-8", errors="ignore")).hexdigest()
    keep = WORKFLOW_MAX_ID_LENGTH - len(digest) - 1
    return f"{text[:keep]}~{digest}"

def extract_metadata(image_path):
    """Extract metadata from image (PNG/JPEG/WEBP/etc)"""
    try:
        if not Image or not image_path.exists():
            return {}

        with Image.open(image_path) as img:
            metadata = {}

            # Extract PNG text chunks (ComfyUI stores workflow here)
            if hasattr(img, 'text'):
                for key, value in islice(img.text.items(), WORKFLOW_MAX_METADATA_ENTRIES):
                    metadata[key] = value

            # Also check PNG info dict
            if hasattr(img, 'info'):
                for key, value in islice(img.info.items(), WORKFLOW_MAX_METADATA_ENTRIES):
                    if key not in metadata:
                        metadata[key] = value

            # EXIF (JPEG/WEBP/etc)
            exif_data = {}
            try:
                exif = img._getexif() if hasattr(img, "_getexif") else None
                if exif:
                    for tag, val in islice(exif.items(), WORKFLOW_MAX_METADATA_ENTRIES):
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
                        if len(metadata[key]) <= WORKFLOW_MAX_EMBEDDED_JSON_BYTES:
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
        stream_str = bytes(content_bytes[:WORKFLOW_RAW_SCAN_BYTES]).decode('utf-8', errors='ignore')
    except Exception:
        return

    start_pos = 0
    candidates_examined = 0
    while candidates_examined < WORKFLOW_MAX_SCAN_CANDIDATES:
        first_brace = stream_str.find('{', start_pos)
        if first_brace == -1:
            break

        open_braces = 0
        start_index = first_brace
        in_string = False
        escaped = False

        for i in range(start_index, len(stream_str)):
            char = stream_str[i]
            if in_string:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == '{':
                open_braces += 1
            elif char == '}':
                open_braces -= 1

            if open_braces == 0:
                candidate = stream_str[start_index:i + 1]
                candidates_examined += 1
                try:
                    json.loads(candidate)
                    yield candidate
                    start_pos = i + 1
                except Exception:
                    start_pos = start_index + 1
                break
            if i - start_index + 1 > WORKFLOW_MAX_EMBEDDED_JSON_BYTES:
                candidates_examined += 1
                start_pos = start_index + 1
                break
        else:
            candidates_examined += 1
            start_pos = start_index + 1

def extract_workflow_payloads_from_file(file_path):
    """
    Extract workflow payload from image/video/audio files.
    Return every embedded ComfyUI payload found, keyed by ``ui`` and/or ``api``.
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
                with tempfile.TemporaryFile() as output:
                    subprocess.run(
                        cmd,
                        stdout=output,
                        stderr=subprocess.DEVNULL,
                        check=True,
                        timeout=15,
                        creationflags=creationflags,
                    )
                    output.seek(0)
                    ff_output = output.read(WORKFLOW_RAW_SCAN_BYTES)
                ff_data = json.loads(ff_output.decode('utf-8', errors='ignore'))
                tags = ff_data.get('format', {}).get('tags', {})
                if isinstance(tags, dict):
                    for value in islice(tags.values(), WORKFLOW_MAX_METADATA_ENTRIES):
                        if not isinstance(value, str):
                            continue
                        value = value[:WORKFLOW_MAX_EMBEDDED_JSON_BYTES]
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
                window = WORKFLOW_RAW_SCAN_BYTES // 2
                size = os.fstat(f.fileno()).st_size
                head = f.read(window)
                tail = b''
                if size > window:
                    f.seek(max(window, size - window))
                    tail = f.read(window)
            for content in (head, tail):
                if not content:
                    continue
                for json_str in _scan_bytes_for_workflow(content):
                    analyze_json(json_str)
                    if 'ui' in found and 'api' in found:
                        break
                if 'ui' in found and 'api' in found:
                    break
        except Exception:
            pass

    return found


def extract_workflow_from_file(file_path):
    """Return the preferred workflow payload while preserving the legacy API."""
    found = extract_workflow_payloads_from_file(file_path)
    if 'ui' in found:
        return found['ui'], 'ui'
    if 'api' in found:
        return found['api'], 'api'
    return None, None


def extract_media_dimensions(file_path):
    """Read the encoded dimensions of the first video stream with ffprobe."""
    ffprobe_bin = os.environ.get('FFPROBE_PATH') or shutil.which('ffprobe')
    if not ffprobe_bin:
        return {'width': None, 'height': None}
    try:
        cmd = [
            ffprobe_bin, '-v', 'quiet', '-print_format', 'json',
            '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
            str(file_path),
        ]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with tempfile.TemporaryFile() as output:
            subprocess.run(
                cmd,
                stdout=output,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=15,
                creationflags=creationflags,
            )
            output.seek(0)
            probe = json.loads(output.read(64 * 1024).decode('utf-8', errors='ignore'))
        streams = probe.get('streams', [])
        stream = streams[0] if isinstance(streams, list) and streams else {}
        width = stream.get('width') if isinstance(stream, dict) else None
        height = stream.get('height') if isinstance(stream, dict) else None
        return {
            'width': width if isinstance(width, int) and width > 0 else None,
            'height': height if isinstance(height, int) and height > 0 else None,
        }
    except Exception:
        return {'width': None, 'height': None}

def _normalized_lora_identity(name):
    value = _workflow_text(name).strip().replace('\\', '/')
    for suffix in ('.safetensors', '.ckpt', '.pt', '.bin'):
        if value.lower().endswith(suffix):
            value = value[:-len(suffix)]
            break
    return value.casefold()


def _normalized_lora_basename(name):
    return _normalized_lora_identity(name).rsplit('/', 1)[-1]


def _lora_names_equivalent(first, second):
    return _normalized_lora_identity(first) == _normalized_lora_identity(second)


def _parameter_lora_hash_names(text):
    """Return LoRA names explicitly recorded as applied by a parameters exporter."""
    names = set()
    lora_hashes = re.search(r'\bLora hashes:\s*"([^"\n]+)', text, re.IGNORECASE)
    if lora_hashes:
        for pair in islice(lora_hashes.group(1).split(','), WORKFLOW_MAX_LORAS):
            if ':' in pair:
                names.add(_normalized_lora_identity(pair.rsplit(':', 1)[0]))

    hashes_marker = re.search(r'\bHashes:\s*', text, re.IGNORECASE)
    if hashes_marker:
        try:
            payload, _ = json.JSONDecoder().raw_decode(text[hashes_marker.end():])
            if isinstance(payload, dict):
                for key in islice(payload, WORKFLOW_MAX_LORAS * 4):
                    key_text = _workflow_text(key).strip()
                    if key_text.lower().startswith('lora:'):
                        names.add(_normalized_lora_identity(key_text.split(':', 1)[1]))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return {name for name in names if name}


def _parse_parameters_text(params_text, parsed):
    if not params_text or not isinstance(params_text, str):
        return
    text = params_text[:WORKFLOW_MAX_PROMPT_LENGTH].strip()
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
        "VAE": r'VAE:\s*([^,\n]+)',
        "VAE hash": r'VAE hash:\s*([^,\n]+)',
        "Denoising strength": r'Denoising strength:\s*([\d.]+)',
        "Hires steps": r'Hires steps:\s*([^,\n]+)',
        "Hires upscale": r'Hires upscale:\s*([^,\n]+)',
        "Hires resize": r'Hires resize:\s*([^,\n]+)',
        "Refiner": r'Refiner:\s*([^,\n]+)',
        "Refiner switch": r'Refiner switch:\s*([^,\n]+)',
        "RNG": r'RNG:\s*([^,\n]+)'
    }
    for label, pattern in extra_fields.items():
        m = re.search(pattern, text)
        if m:
            parsed['extras'][label] = m.group(1).strip()

    # A <lora:...> token in ComfyUI can remain after the LoRA is disabled. The
    # exporter's applied-hash payload is the authoritative distinction for
    # parameters-only files.
    is_comfyui_parameters = bool(re.search(r'\bVersion:\s*ComfyUI\b', text, re.IGNORECASE))
    applied_hash_names = _parameter_lora_hash_names(text)
    lora_tags = re.findall(r'<lora:([^:>]+):([\d.]+)>', text)
    for lora_name, strength in lora_tags:
        lora_name = lora_name.strip()
        if applied_hash_names:
            identity = _normalized_lora_identity(lora_name)
            if identity not in applied_hash_names:
                basename_matches = {
                    applied for applied in applied_hash_names
                    if '/' not in identity and _normalized_lora_basename(applied) == identity
                }
                if len(basename_matches) != 1:
                    continue
        elif is_comfyui_parameters:
            continue
        _add_lora(parsed, lora_name, float(strength), float(strength))

def _sanitize_prompt_text(prompt_text):
    """Remove inline LoRA tags from display prompt while preserving line structure."""
    if not isinstance(prompt_text, str):
        return prompt_text

    prompt_text = prompt_text[:WORKFLOW_MAX_PROMPT_LENGTH]
    cleaned = re.sub(r'[ \t]*<lora:[^>]+>[ \t]*', ' ', prompt_text, flags=re.IGNORECASE)
    out_lines = []
    for line in cleaned.splitlines():
        line = re.sub(r'[ \t]+', ' ', line).strip()
        line = re.sub(r'\s+,', ',', line)
        line = re.sub(r',\s*,', ',', line)
        if line:
            out_lines.append(line)
    return '\n'.join(out_lines).strip()


def _decode_json_maybe(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _is_link_ref(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return False
    node_id, slot = value
    return (
        isinstance(node_id, (str, int))
        and not isinstance(node_id, bool)
        and isinstance(slot, int)
        and not isinstance(slot, bool)
        and slot >= 0
    )


def _node_title(node):
    if not isinstance(node, dict):
        return ''
    meta = node.get('_meta') or {}
    if not isinstance(meta, dict):
        meta = {}
    return str(node.get('title') or meta.get('title') or '')


def _node_type(node):
    if not isinstance(node, dict):
        return ''
    return str(node.get('class_type') or node.get('type') or '')


def _iter_ui_workflow_nodes(workflow_data):
    """Yield (node, id_prefix, subgraph_name) for top-level and definitions.subgraphs UI nodes."""
    if not isinstance(workflow_data, dict):
        return

    yielded = 0
    for node in islice(workflow_data.get('nodes', []) or [], WORKFLOW_MAX_NODES):
        if isinstance(node, dict):
            yield node, '', None
            yielded += 1

    definitions = workflow_data.get('definitions')
    if not isinstance(definitions, dict):
        return

    raw_subgraphs = definitions.get('subgraphs', [])
    if not isinstance(raw_subgraphs, list):
        return
    used_subgraph_ids = set()
    for subgraph_index, subgraph in enumerate(islice(raw_subgraphs, WORKFLOW_MAX_SUBGRAPHS)):
        if yielded >= WORKFLOW_MAX_NODES:
            break
        if not isinstance(subgraph, dict):
            continue
        base_id = _workflow_id(subgraph.get('id') or subgraph.get('name') or f'subgraph-{subgraph_index + 1}')
        subgraph_id = base_id
        suffix = 2
        while subgraph_id in used_subgraph_ids:
            subgraph_id = _workflow_id(f'{base_id}~{suffix}')
            suffix += 1
        used_subgraph_ids.add(subgraph_id)
        subgraph_name = _workflow_text(subgraph.get('name') or subgraph_id)
        remaining = WORKFLOW_MAX_NODES - yielded
        for node in islice(subgraph.get('nodes', []) or [], remaining):
            if isinstance(node, dict):
                yield node, f'{subgraph_id}:', subgraph_name
                yielded += 1


def _coerce_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r'-?\d+', text):
            return int(text)
        if re.fullmatch(r'-?\d+\.\d+', text):
            return float(text)
    return None


def _build_showtext_snapshots(prompt_graph):
    snapshots = {}
    for consumer in islice(prompt_graph.values(), WORKFLOW_MAX_NODES):
        if not isinstance(consumer, dict) or 'showtext' not in _node_type(consumer).lower():
            continue
        consumer_inputs = consumer.get('inputs', {})
        if not isinstance(consumer_inputs, dict):
            continue
        snapshot = next((
            consumer_inputs[name].strip()
            for name in islice(consumer_inputs, WORKFLOW_MAX_PORTS)
            if str(name).startswith('text_')
            and isinstance(consumer_inputs.get(name), str)
            and consumer_inputs[name].strip()
        ), None)
        if not snapshot:
            continue
        for input_value in islice(consumer_inputs.values(), WORKFLOW_MAX_PORTS):
            if _is_link_ref(input_value):
                snapshots.setdefault(str(input_value[0]), snapshot)
    return snapshots


def _bounded_join_text(parts, delimiter=''):
    delimiter = delimiter if isinstance(delimiter, str) else ''
    delimiter = delimiter[:WORKFLOW_MAX_TEXT_LENGTH]
    pieces = []
    remaining = WORKFLOW_MAX_PROMPT_LENGTH
    for part in parts:
        if not isinstance(part, str) or not part.strip() or remaining <= 0:
            continue
        part = part.strip()
        if pieces and delimiter:
            separator = delimiter[:remaining]
            pieces.append(separator)
            remaining -= len(separator)
        if remaining <= 0:
            break
        piece = part[:remaining]
        pieces.append(piece)
        remaining -= len(piece)
    return ''.join(pieces).strip() or None


def _resolve_api_scalar(prompt_graph, value, visited=None):
    """Resolve a primitive value through generic ComfyUI helper nodes."""
    seen = set(visited or ())
    current = value
    for _ in range(WORKFLOW_MAX_NODES):
        if not _is_link_ref(current):
            return current
        node_key = str(current[0])
        if node_key in seen:
            return None
        seen.add(node_key)
        node = prompt_graph.get(node_key)
        inputs = node.get('inputs', {}) if isinstance(node, dict) else {}
        if not isinstance(inputs, dict):
            return None
        current = next((inputs[name] for name in ('value', 'boolean', 'switch', 'index', 'number') if name in inputs), None)
    return None


def _resolve_api_text_iterative(
    prompt_graph, value, preferred_names=None, visited=None, showtext_snapshots=None,
):
    preferred_names = list(preferred_names or [])
    snapshots = showtext_snapshots
    if snapshots is None:
        snapshots = _build_showtext_snapshots(prompt_graph)
    memo = snapshots.setdefault(_RESOLVER_MEMO_KEY, {})
    active = set(visited or ())
    results = []
    stack = [('eval', value)]
    steps = 0

    def cache_result(node_key, result):
        cache_key = (node_key, tuple(preferred_names))
        if result is None:
            return
        if cache_key not in memo and len(memo) >= WORKFLOW_MAX_RESOLVER_CACHE:
            memo.pop(next(iter(memo)))
        memo[cache_key] = result

    while stack and steps < WORKFLOW_MAX_RESOLVE_STEPS:
        steps += 1
        frame = stack.pop()
        operation = frame[0]

        if operation == 'finish':
            _, node_key, count, delimiter = frame
            parts = results[-count:] if count else []
            if count:
                del results[-count:]
            result = _bounded_join_text(parts, delimiter)
            active.discard(node_key)
            cache_result(node_key, result)
            results.append(result)
            continue

        if operation == 'forward':
            node_key = frame[1]
            result = results[-1] if results else None
            active.discard(node_key)
            cache_result(node_key, result)
            continue

        current = frame[1]
        if not _is_link_ref(current):
            result = current.strip()[:WORKFLOW_MAX_PROMPT_LENGTH] if isinstance(current, str) else None
            results.append(result or None)
            continue

        node_key = str(current[0])
        cache_key = (node_key, tuple(preferred_names))
        if cache_key in memo:
            results.append(memo[cache_key])
            continue
        if node_key in active:
            results.append(None)
            continue
        if node_key in snapshots:
            result = snapshots[node_key][:WORKFLOW_MAX_PROMPT_LENGTH]
            cache_result(node_key, result)
            results.append(result)
            continue

        ref_node = prompt_graph.get(node_key)
        if not isinstance(ref_node, dict):
            results.append(None)
            continue
        inputs = ref_node.get('inputs', {})
        if not isinstance(inputs, dict):
            results.append(None)
            continue
        bounded_inputs = dict(islice(inputs.items(), WORKFLOW_MAX_PORTS))
        active.add(node_key)

        node_type_l = _node_type(ref_node).lower()
        if 'switch' in node_type_l:
            switch_value = _resolve_api_scalar(prompt_graph, bounded_inputs.get('switch'), active)
            if isinstance(switch_value, str):
                switch_value = switch_value.strip().lower() in {'true', '1', 'yes', 'on'}
            branch_names = (
                ('on_true', 'true', 'if_true') if bool(switch_value)
                else ('on_false', 'false', 'if_false')
            )
            selected = next((bounded_inputs[name] for name in branch_names if name in bounded_inputs), None)
            if selected is not None:
                stack.append(('forward', node_key))
                stack.append(('eval', selected))
                continue

        if 'stringconcatenate' in node_type_l:
            operands = [
                bounded_inputs[name]
                for name in sorted(bounded_inputs, key=str)
                if str(name).startswith('string_')
            ]
            stack.append(('finish', node_key, len(operands), bounded_inputs.get('delimiter', '')))
            for operand in reversed(operands):
                stack.append(('eval', operand))
            continue

        candidate_names = preferred_names + [
            'text', 'prompt', 'positive', 'negative', 'string', 'string_a', 'value', 'name'
        ]
        selected = next((bounded_inputs[name] for name in candidate_names if name in bounded_inputs), None)
        if selected is None:
            # Do not walk arbitrary model/clip/vae inputs when a conditioning node has
            # no literal prompt. That used to display checkpoint filenames as prompts
            # for upscalers such as SeedVR2. Only generic pass-through helpers may use
            # their first input as a text source.
            is_text_passthrough = any(token in node_type_l for token in (
                'reroute', 'primitive', 'string', 'text', 'prompt', 'showtext', 'switch'
            ))
            if not is_text_passthrough:
                active.discard(node_key)
                cache_result(node_key, None)
                results.append(None)
                continue
            selected = next(iter(bounded_inputs.values()), None)
        stack.append(('forward', node_key))
        stack.append(('eval', selected))

    if stack or not results:
        return None
    return results[-1]


def _resolve_api_value(
    prompt_graph, value, preferred_names=None, want='text', visited=None,
    showtext_snapshots=None,
):
    """Resolve a ComfyUI API graph input, following links through text/prompt/string/size helper nodes."""
    preferred_names = preferred_names or []
    if want == 'text':
        return _resolve_api_text_iterative(
            prompt_graph, value, preferred_names, visited, showtext_snapshots,
        )
    seen = set(visited or ())
    current = value

    if not _is_link_ref(current):
        if want == 'number':
            return _coerce_number(current)
        if isinstance(current, str):
            return current.strip()
        return current if current is not None and want != 'text' else None

    if showtext_snapshots is None:
        showtext_snapshots = _build_showtext_snapshots(prompt_graph) if want == 'text' else {}

    for _ in range(WORKFLOW_MAX_NODES):
        if not _is_link_ref(current):
            if want == 'number':
                return _coerce_number(current)
            if isinstance(current, str):
                return current.strip()
            return current if current is not None and want != 'text' else None

        node_key = str(current[0])
        if node_key in seen:
            return None
        seen.add(node_key)
        if node_key in showtext_snapshots:
            return showtext_snapshots[node_key]

        ref_node = prompt_graph.get(node_key)
        if not isinstance(ref_node, dict):
            return None
        inputs = ref_node.get('inputs', {})
        if not isinstance(inputs, dict):
            return None
        bounded_inputs = dict(islice(inputs.items(), WORKFLOW_MAX_PORTS))
        node_type_l = _node_type(ref_node).lower()

        candidate_names = list(preferred_names)
        candidate_names.extend(
            ['width', 'height', 'steps', 'cfg', 'seed', 'value', 'number', 'int', 'float']
            if want == 'number'
            else ['text', 'prompt', 'positive', 'negative', 'string', 'string_a', 'value', 'name']
        )
        selected = False
        for name in candidate_names:
            if name in bounded_inputs:
                current = bounded_inputs[name]
                selected = True
                break
        if not selected:
            current = next(iter(bounded_inputs.values()), None)
        if current in (None, ''):
            return None
    return None


def _extract_api_node_value(
    prompt_graph, node, preferred_names=None, want='text', visited=None,
    showtext_snapshots=None,
):
    preferred_names = preferred_names or []
    visited = visited or set()
    inputs = node.get('inputs', {}) if isinstance(node, dict) else {}
    if not isinstance(inputs, dict):
        return None

    node_type_l = _node_type(node).lower()
    if want == 'text' and 'stringconcatenate' in node_type_l:
        delimiter = inputs.get('delimiter', '')
        if not isinstance(delimiter, str):
            delimiter = ''
        parts = []
        for key in sorted([k for k in inputs if str(k).startswith('string_')]):
            part = _resolve_api_value(
                prompt_graph, inputs.get(key), want='text', visited=set(visited),
                showtext_snapshots=showtext_snapshots,
            )
            if isinstance(part, str) and part.strip():
                parts.append(part.strip())
        if parts:
            return _bounded_join_text(parts, delimiter)

    for name in preferred_names:
        if name in inputs:
            resolved = _resolve_api_value(
                prompt_graph, inputs.get(name), preferred_names, want, set(visited),
                showtext_snapshots,
            )
            if resolved not in (None, ''):
                return resolved
    if want == 'text' and any(name in inputs for name in preferred_names):
        # A preferred text/prompt field exists but is empty. Do not fall through into
        # unrelated inputs such as clip/model/vae links and mistake filenames for prompts.
        return None

    if want == 'number':
        for name in ['width', 'height', 'steps', 'cfg', 'seed', 'value', 'number', 'int', 'float']:
            if name in inputs:
                resolved = _resolve_api_value(
                    prompt_graph, inputs.get(name), preferred_names, want, set(visited),
                    showtext_snapshots,
                )
                if resolved is not None:
                    return resolved
        for value in inputs.values():
            resolved = _resolve_api_value(
                prompt_graph, value, preferred_names, want, set(visited), showtext_snapshots,
            )
            if resolved is not None:
                return resolved
        return None

    for name in ['text', 'prompt', 'positive', 'negative', 'string', 'string_a', 'value', 'name']:
        if name in inputs:
            resolved = _resolve_api_value(
                prompt_graph, inputs.get(name), preferred_names, want, set(visited),
                showtext_snapshots,
            )
            if isinstance(resolved, str) and resolved.strip():
                return resolved.strip()

    for value in inputs.values():
        resolved = _resolve_api_value(
            prompt_graph, value, preferred_names, want, set(visited), showtext_snapshots,
        )
        if isinstance(resolved, str) and resolved.strip():
            return resolved.strip()
    return None


def _add_lora(parsed, name, strength_model=1.0, strength_clip=None):
    if len(parsed['loras']) >= WORKFLOW_MAX_LORAS:
        return
    if not name:
        return
    lora_name = _workflow_text(name).strip()
    if lora_name in ['None', '', 'ComfyUI']:
        return
    new_identity = _normalized_lora_identity(lora_name)
    new_has_path = '/' in new_identity
    if strength_clip is None:
        strength_clip = strength_model
    entry = {
        'name': lora_name,
        'strength_model': strength_model,
        'strength_clip': strength_clip,
    }
    for index, existing in enumerate(parsed['loras']):
        existing_identity = _normalized_lora_identity(existing['name'])
        if existing_identity == new_identity:
            return
        if _normalized_lora_basename(existing_identity) != _normalized_lora_basename(new_identity):
            continue
        existing_has_path = '/' in existing_identity
        if not existing_has_path and new_has_path:
            parsed['loras'][index] = entry
            return
        if existing_has_path and not new_has_path:
            return
        if not existing_has_path and not new_has_path:
            return
    parsed['loras'].append(entry)


def _structured_lora_entries(values):
    for value in values:
        if isinstance(value, dict) and isinstance(value.get('__value__'), list):
            value = value['__value__']
        if isinstance(value, list) and value and all(isinstance(item, dict) and 'name' in item for item in value):
            return value[:WORKFLOW_MAX_PARAMS]
    return None


def _active_api_lora_node_ids(prompt_graph):
    """Return LoRA nodes on the model path actually selected by samplers/guiders."""
    model_roots = []
    for node in prompt_graph.values():
        if not isinstance(node, dict):
            continue
        node_type_l = _node_type(node).lower()
        inputs = node.get('inputs', {})
        if not isinstance(inputs, dict):
            continue
        is_sampler = 'sampler' in node_type_l and 'select' not in node_type_l
        if is_sampler:
            for input_name in ('model', 'guider'):
                if _is_link_ref(inputs.get(input_name)):
                    model_roots.append(inputs[input_name])

    if not model_roots:
        return None

    active_loras = set()
    visited = set()
    stack = list(model_roots)
    while stack and len(visited) < WORKFLOW_MAX_NODES:
        value = stack.pop()
        if not _is_link_ref(value):
            continue
        node_id = str(value[0])
        if node_id in visited:
            continue
        visited.add(node_id)
        node = prompt_graph.get(node_id)
        if not isinstance(node, dict):
            continue
        node_type_l = _node_type(node).lower()
        inputs = node.get('inputs', {})
        if not isinstance(inputs, dict):
            continue
        if 'lora' in node_type_l:
            active_loras.add(node_id)
        if 'switch' in node_type_l:
            selector = next((inputs.get(name) for name in ('switch', 'select', 'index', 'choice') if name in inputs), None)
            switch_value = _resolve_api_scalar(prompt_graph, selector)
            normalized_switch = switch_value
            if isinstance(normalized_switch, str):
                text_value = normalized_switch.strip().lower()
                if text_value in {'true', 'yes', 'on'}:
                    normalized_switch = True
                elif text_value in {'false', 'no', 'off'}:
                    normalized_switch = False
                else:
                    try:
                        normalized_switch = int(text_value)
                    except ValueError:
                        pass
            branch_names = (
                ('on_true', 'true', 'if_true') if bool(normalized_switch)
                else ('on_false', 'false', 'if_false')
            )
            selected = next((inputs[name] for name in branch_names if name in inputs), None)
            if selected is None:
                if isinstance(normalized_switch, bool):
                    branch_index = 1 if normalized_switch else 2
                elif isinstance(normalized_switch, (int, float)):
                    branch_index = int(normalized_switch)
                else:
                    branch_index = None
                if branch_index is not None:
                    numbered_names = (
                        f'model{branch_index}', f'model_{branch_index}',
                        f'unet{branch_index}', f'unet_{branch_index}',
                        f'input{branch_index}', f'input_{branch_index}',
                    )
                    selected = next((inputs[name] for name in numbered_names if name in inputs), None)
            if _is_link_ref(selected):
                stack.append(selected)
            continue
        for name, linked_value in inputs.items():
            name_l = str(name).lower()
            follows_model = 'model' in name_l or 'unet' in name_l
            follows_applied_stack = 'lora' in node_type_l and 'lora' in name_l
            if _is_link_ref(linked_value) and (follows_model or follows_applied_stack):
                stack.append(linked_value)
    return active_loras


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
            for k, v in islice(exif.items(), 256):
                if isinstance(v, str) and any(x in v for x in ['Steps:', 'Sampler:', 'Negative prompt:', 'CFG']):
                    _parse_parameters_text(v, parsed)

        # First check if there's a 'parameters' field (Automatic1111/Forge/WebUI format)
        if 'parameters' in metadata:
            params_text = metadata['parameters']
            if isinstance(params_text, str):
                params_text = params_text[:WORKFLOW_MAX_PROMPT_LENGTH]
                lines = params_text.split('\n')
                full_text = '\n'.join(lines[1:]) if len(lines) > 1 else ''

                # Also extract from Lora hashes field
                lora_match = re.search(r'Lora hashes:\s*"([^"\n]+)', full_text)
                if lora_match:
                    lora_text = lora_match.group(1)
                    for pair in islice(lora_text.split(','), WORKFLOW_MAX_LORAS):
                        if ':' in pair:
                            lora_name = pair.split(':')[0].strip()
                            strength_match = re.search(rf'<lora:{re.escape(lora_name)}:([\d.]+)>', parsed['prompt'] or '')
                            strength = float(strength_match.group(1)) if strength_match else 1.0
                            _add_lora(parsed, lora_name, strength, strength)

        # ComfyUI UI workflow format. Newer ComfyUI can keep important nodes inside
        # workflow.definitions.subgraphs, so iterate both top-level and subgraph nodes.
        workflow = _decode_json_maybe(metadata.get('workflow', {}))
        if isinstance(workflow, dict):
            for node, _id_prefix, _subgraph_name in _iter_ui_workflow_nodes(workflow):
                node_type = _node_type(node)
                node_type_l = node_type.lower()
                raw_widgets = node.get('widgets_values', [])
                widgets = raw_widgets[:WORKFLOW_MAX_PARAMS] if isinstance(raw_widgets, list) else []
                inputs = node.get('inputs', {})
                title = _node_title(node).lower()

                # Extract prompts from core and custom text-encode/prompt nodes.
                if any(kw in node_type_l for kw in ['cliptextencode', 'textencode', 'prompttext']):
                    text = widgets[0] if widgets and isinstance(widgets[0], str) else None
                    if not text and isinstance(inputs, dict):
                        text = inputs.get('text') or inputs.get('prompt')
                    if text:
                        is_negative = any(word in title for word in ['negative', 'neg'])
                        if is_negative and not parsed['negative_prompt']:
                            parsed['negative_prompt'] = text
                        elif not is_negative and not parsed['prompt']:
                            parsed['prompt'] = text

                # KSamplerSelect is only a sampler-name picker; treating its first
                # widget as a seed corrupts the details panel (for example seed="lcm").
                if 'ksampler' in node_type_l and 'select' not in node_type_l:
                    if not parsed['seed']:
                        parsed['seed'] = widgets[0] if widgets else (inputs.get('seed') if isinstance(inputs, dict) else None)
                    if not parsed['steps']:
                        parsed['steps'] = widgets[1] if len(widgets) > 1 else (inputs.get('steps') if isinstance(inputs, dict) else None)
                    if not parsed['cfg']:
                        parsed['cfg'] = widgets[2] if len(widgets) > 2 else (inputs.get('cfg') if isinstance(inputs, dict) else None)
                    if not parsed['sampler']:
                        parsed['sampler'] = widgets[3] if len(widgets) > 3 else (inputs.get('sampler_name') if isinstance(inputs, dict) else None)
                    if not parsed['scheduler']:
                        parsed['scheduler'] = widgets[4] if len(widgets) > 4 else (inputs.get('scheduler') if isinstance(inputs, dict) else None)

                # Extract model/checkpoint names.
                if any(kw in node_type_l for kw in ['checkpointloader', 'unetloader', 'modelloader']):
                    if not parsed['model']:
                        parsed['model'] = widgets[0] if widgets else None
                        if not parsed['model'] and isinstance(inputs, dict):
                            for key in ['ckpt_name', 'unet_name', 'model_name', 'name']:
                                if inputs.get(key):
                                    parsed['model'] = inputs.get(key)
                                    break

                # Extract LoRAs from LoRA Manager, standard loaders, stackers, and custom loaders.
                if any(kw in node_type_l for kw in ['lora', 'loraloader', 'lora stacker', 'lora_stack']):
                    if node.get('mode', 0) != 0:
                        continue
                    try:
                        # LoraManager stores a human-readable string containing every
                        # inventory entry in widget 0 and the authoritative structured
                        # list (including each entry's active flag) in a later widget.
                        # Never treat that display string as one giant LoRA name.
                        structured_loras = next(
                            (
                                value[:WORKFLOW_MAX_PARAMS] for value in widgets
                                if isinstance(value, list)
                                and value
                                and all(
                                    isinstance(item, dict) and 'name' in item
                                    for item in islice(value, WORKFLOW_MAX_PARAMS)
                                )
                            ),
                            None,
                        )
                        if structured_loras is None:
                            if widgets and isinstance(widgets[0], list) and not widgets[0]:
                                # Preserve the original empty-list convention used by
                                # stackers that put their structured data in widget 0.
                                structured_loras = widgets[0]
                            elif 'loramanager' in node_type_l:
                                # An empty later list is authoritative only for
                                # LoraManager. Other loaders can have unrelated empty
                                # list widgets after their normal name/strength fields.
                                structured_loras = next(
                                    (value for value in widgets if isinstance(value, list) and not value),
                                    None,
                                )
                        if structured_loras is not None:
                            for lora_obj in structured_loras:
                                if lora_obj.get('active', True):
                                    _add_lora(
                                        parsed,
                                        lora_obj.get('name'),
                                        lora_obj.get('strength', 1.0),
                                        lora_obj.get('clipStrength', lora_obj.get('strength', 1.0)),
                                    )
                        else:
                            lora_name = widgets[0] if widgets else None
                            strength_model = widgets[1] if len(widgets) > 1 else 1.0
                            strength_clip = widgets[2] if len(widgets) > 2 else strength_model
                            if isinstance(inputs, dict):
                                lora_name = lora_name or inputs.get('lora_name') or inputs.get('name')
                                strength_model = inputs.get('strength_model', inputs.get('lora_strength', inputs.get('strength', strength_model)))
                                strength_clip = inputs.get('strength_clip', inputs.get('clip_strength', inputs.get('clipStrength', strength_model)))
                            _add_lora(parsed, lora_name, strength_model, strength_clip)
                    except Exception as e:
                        print(f"Error parsing LoRAs from node {node_type}: {e}")

                # Extract dimensions.
                if 'emptylatentimage' in node_type_l or ('latent' in node_type_l and isinstance(inputs, dict) and 'width' in inputs and 'height' in inputs):
                    if not parsed['width']:
                        parsed['width'] = widgets[0] if widgets else inputs.get('width')
                    if not parsed['height']:
                        parsed['height'] = widgets[1] if len(widgets) > 1 else inputs.get('height')

        # Parse API prompt graph JSON (ComfyUI default Save Image metadata). This is the
        # most reliable source for images saved by core SaveImage and most custom save nodes.
        prompt_graph = _decode_json_maybe(metadata.get('prompt'))
        if isinstance(prompt_graph, dict):
            bounded_prompt_graph = {}
            for node_id, node in islice(prompt_graph.items(), WORKFLOW_MAX_NODES):
                if not isinstance(node, dict):
                    bounded_prompt_graph[node_id] = node
                    continue
                bounded_node = {
                    key: node[key]
                    for key in ('class_type', 'type', 'title')
                    if key in node
                }
                node_meta = node.get('_meta')
                if isinstance(node_meta, dict) and 'title' in node_meta:
                    bounded_node['_meta'] = {'title': _workflow_text(node_meta.get('title'))}
                node_inputs = node.get('inputs')
                if isinstance(node_inputs, dict):
                    bounded_node['inputs'] = dict(islice(node_inputs.items(), WORKFLOW_MAX_PORTS))
                bounded_prompt_graph[node_id] = bounded_node
            prompt_graph = bounded_prompt_graph
            # The API prompt is the execution graph. Replace speculative LoRAs
            # collected from the UI workflow snapshot with its selected model path.
            parsed['loras'] = []
            active_api_loras = _active_api_lora_node_ids(prompt_graph)
            showtext_snapshots = _build_showtext_snapshots(prompt_graph)
            text_candidates = []
            api_negative_input_seen = False
            for node_id, node in prompt_graph.items():
                if not isinstance(node, dict):
                    continue
                node_type = _node_type(node)
                node_type_l = node_type.lower()
                title_l = _node_title(node).lower()
                node_inputs = node.get('inputs', {})
                if not isinstance(node_inputs, dict):
                    continue

                is_sampling_node = (
                    ('ksampler' in node_type_l and 'select' not in node_type_l)
                    or 'samplercustom' in node_type_l
                )
                if is_sampling_node:
                    # Prefer the API prompt graph over the UI workflow for sampler settings;
                    # UI widgets include control widgets (for example randomize/fixed) that
                    # shifted position in recent ComfyUI versions.
                    seed_input = node_inputs.get('seed', node_inputs.get('noise_seed'))
                    if seed_input is None:
                        seed_input = node_inputs.get('noise')
                    seed_value = _resolve_api_value(
                        prompt_graph, seed_input,
                        ['noise_seed', 'seed', 'value'], 'number',
                        showtext_snapshots=showtext_snapshots,
                    )
                    if seed_value is not None:
                        parsed['seed'] = seed_value
                    if node_inputs.get('steps') is not None:
                        parsed['steps'] = node_inputs.get('steps')
                    if node_inputs.get('cfg') is not None:
                        parsed['cfg'] = node_inputs.get('cfg')
                    if node_inputs.get('sampler_name') is not None:
                        parsed['sampler'] = node_inputs.get('sampler_name')
                    if node_inputs.get('scheduler') is not None:
                        parsed['scheduler'] = node_inputs.get('scheduler')

                    # SamplerCustomAdvanced stores settings in linked helper nodes.
                    if 'samplercustomadvanced' in node_type_l:
                        cfg_value = _resolve_api_value(
                            prompt_graph, node_inputs.get('guider'), ['cfg'], 'number',
                            showtext_snapshots=showtext_snapshots,
                        )
                        if cfg_value is not None:
                            parsed['cfg'] = cfg_value
                        sampler_value = _resolve_api_value(
                            prompt_graph, node_inputs.get('sampler'), ['sampler_name'], 'text',
                            showtext_snapshots=showtext_snapshots,
                        )
                        if sampler_value:
                            parsed['sampler'] = sampler_value
                        sigma_text = _resolve_api_value(
                            prompt_graph, node_inputs.get('sigmas'), ['sigmas'], 'text',
                            showtext_snapshots=showtext_snapshots,
                        )
                        if isinstance(sigma_text, str):
                            sigma_values = [value.strip() for value in sigma_text.split(',') if value.strip()]
                            if len(sigma_values) >= 2:
                                parsed['steps'] = len(sigma_values) - 1
                                if not parsed['scheduler']:
                                    parsed['scheduler'] = 'manual sigmas'

                # Guider/conditioning nodes encode the semantic role of otherwise
                # identically titled CLIP text nodes. Follow those links before using
                # title/order heuristics.
                has_prompt_roles = is_sampling_node or (
                    'positive' in node_inputs and 'negative' in node_inputs
                )
                if has_prompt_roles:
                    positive_text = _resolve_api_value(
                        prompt_graph, node_inputs.get('positive'),
                        ['text', 'prompt', 'positive', 'string_a', 'string'], 'text',
                        showtext_snapshots=showtext_snapshots,
                    )
                    if positive_text:
                        parsed['prompt'] = positive_text
                    if 'negative' in node_inputs:
                        api_negative_input_seen = True
                    negative_text = _resolve_api_value(
                        prompt_graph, node_inputs.get('negative'),
                        ['text', 'prompt', 'negative', 'string_a', 'string'], 'text',
                        showtext_snapshots=showtext_snapshots,
                    )
                    if negative_text:
                        parsed['negative_prompt'] = negative_text
                    elif 'negative' in node_inputs:
                        # The API graph explicitly says the negative input is empty. Do not keep
                        # stale/wrong text guessed from UI widgets (recent ComfyUI widgets moved).
                        parsed['negative_prompt'] = None

                # Newer/custom graphs can split sampling into SamplerCustom,
                # KSamplerSelect, and BasicScheduler nodes.
                if 'ksamplerselect' in node_type_l and node_inputs.get('sampler_name') is not None:
                    parsed['sampler'] = node_inputs.get('sampler_name')
                if 'scheduler' in node_type_l:
                    if node_inputs.get('steps') is not None:
                        parsed['steps'] = node_inputs.get('steps')
                    if node_inputs.get('scheduler') is not None:
                        parsed['scheduler'] = node_inputs.get('scheduler')

                if any(key in node_inputs for key in ['ckpt_name', 'unet_name', 'model_name']) or any(kw in node_type_l for kw in ['checkpointloader', 'unetloader', 'modelloader', 'ditloader']):
                    # Checkpoint/UNet names are the generation model. A generic
                    # model_name on a captioner or VLM helper must not overwrite it.
                    primary_model = node_inputs.get('ckpt_name') or node_inputs.get('unet_name')
                    if isinstance(primary_model, str) and primary_model.strip():
                        parsed['model'] = primary_model.strip()
                    elif not parsed['model']:
                        for key in ['model_name', 'name']:
                            if isinstance(node_inputs.get(key), str) and node_inputs.get(key).strip():
                                parsed['model'] = node_inputs.get(key).strip()
                                break

                if any(kw in node_type_l for kw in ['lora', 'loraloader', 'lora_stack', 'lora stacker']):
                    if active_api_loras is not None and str(node_id) not in active_api_loras:
                        continue
                    structured_loras = _structured_lora_entries(node_inputs.values())
                    if structured_loras is not None:
                        for lora_obj in structured_loras:
                            if lora_obj.get('active', True):
                                _add_lora(
                                    parsed,
                                    lora_obj.get('name'),
                                    lora_obj.get('strength', 1.0),
                                    lora_obj.get('clipStrength', lora_obj.get('strength', 1.0)),
                                )
                    else:
                        lora_name = _resolve_api_value(
                            prompt_graph, node_inputs.get('lora_name') or node_inputs.get('name'),
                            ['lora_name', 'name'], 'text', showtext_snapshots=showtext_snapshots,
                        )
                        strength_model = node_inputs.get('strength_model', node_inputs.get('lora_strength', node_inputs.get('strength', 1.0)))
                        strength_clip = node_inputs.get('strength_clip', node_inputs.get('clip_strength', node_inputs.get('clipStrength', strength_model)))
                        _add_lora(parsed, lora_name, strength_model, strength_clip)

                if 'emptylatentimage' in node_type_l or ('latent' in node_type_l and 'width' in node_inputs and 'height' in node_inputs):
                    width_value = _resolve_api_value(
                        prompt_graph, node_inputs.get('width'), ['width'], 'number',
                        showtext_snapshots=showtext_snapshots,
                    )
                    height_value = _resolve_api_value(
                        prompt_graph, node_inputs.get('height'), ['height'], 'number',
                        showtext_snapshots=showtext_snapshots,
                    )
                    if width_value is not None:
                        parsed['width'] = width_value
                    if height_value is not None:
                        parsed['height'] = height_value

                if (
                    any(kw in node_type_l for kw in ['cliptextencode', 'textencode', 'conditioning', 'prompt'])
                    or any(key in node_inputs for key in ['text', 'prompt', 'positive', 'negative'])
                ):
                    text = _extract_api_node_value(
                        prompt_graph, node, ['text', 'prompt', 'positive', 'string_a', 'string'], 'text',
                        showtext_snapshots=showtext_snapshots,
                    )
                    if isinstance(text, str) and text.strip():
                        text_candidates.append((text.strip(), title_l))

            if not parsed['negative_prompt'] and not api_negative_input_seen:
                for text, title in text_candidates:
                    if any(word in title for word in ['negative', 'neg']):
                        parsed['negative_prompt'] = text
                        break

            if not parsed['prompt']:
                for text, title in text_candidates:
                    if not any(word in title for word in ['negative', 'neg']):
                        parsed['prompt'] = text
                        break

            # Do not invent a negative prompt from arbitrary extra text nodes. If a
            # workflow has a real negative prompt, it is either linked from the sampler
            # negative input or explicitly titled negative/neg above.
            if text_candidates and not parsed['prompt']:
                for text, _ in text_candidates:
                    parsed['prompt'] = text
                    break

    except Exception as e:
        print(f"Error parsing metadata: {e}")
        import traceback
        traceback.print_exc()

    # Final normalization: prompt display should not duplicate LoRA entries.
    parsed['prompt'] = _sanitize_prompt_text(parsed.get('prompt'))
    parsed['negative_prompt'] = _sanitize_prompt_text(parsed.get('negative_prompt'))

    if parsed.get('prompt') and parsed.get('negative_prompt') == parsed.get('prompt'):
        # Some workflows/models have no real negative prompt but the default ComfyUI
        # SaveImage API graph still routes the same conditioning/text to positive and
        # negative. Showing the same block twice is misleading; treat exact duplicates
        # as an absent negative prompt.
        parsed['negative_prompt'] = None

    return _finite_json_value(parsed)

def flatten_metadata(metadata):
    flat = {}
    for key, value in islice(metadata.items(), 256):
        if key in {'workflow', 'prompt'}:
            continue
        if isinstance(value, dict):
            for k2, v2 in islice(value.items(), 256 - len(flat)):
                flat[f"{_workflow_text(key)}.{_workflow_text(k2)}"] = _workflow_text(v2)
        else:
            flat[_workflow_text(key)] = _workflow_text(value)
        if len(flat) >= 256:
            break
    return flat

def _normalize_node_params_from_ui_node(node, limit=WORKFLOW_MAX_PARAMS, value_formatter=None):
    params = []

    def add_param(name, value):
        if len(params) >= limit:
            return False
        formatted_value = value_formatter(name, value) if value_formatter else _workflow_text(value)
        params.append({'name': _workflow_text(name), 'value': formatted_value})
        return True

    widgets = node.get('widgets_values', [])
    widgets = widgets if isinstance(widgets, list) else []
    widget_cursor = 0

    inputs = node.get('inputs', {})
    if isinstance(inputs, list):
        for idx, item in enumerate(islice(inputs, limit)):
            if isinstance(item, dict):
                name = item.get('name') or f"input_{idx + 1}"
                has_widget = item.get('widget') is not None
                if item.get('link') is not None:
                    value = item.get('link')
                elif 'value' in item:
                    value = item.get('value')
                elif has_widget and widget_cursor < len(widgets):
                    value = widgets[widget_cursor]
                elif has_widget:
                    value = item.get('widget')
                else:
                    continue
                if not add_param(name, value):
                    break
                if has_widget:
                    widget_cursor += 1
    elif isinstance(inputs, dict):
        for name, value in islice(inputs.items(), limit):
            if not add_param(name, value):
                break

    # Preserve extra custom-node widgets without duplicating the named values above.
    remaining = max(0, limit - len(params))
    for idx, value in enumerate(islice(widgets[widget_cursor:], remaining), start=widget_cursor):
        if not add_param(f"widget_{idx + 1}", value):
            break

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

    # UI format: {"nodes":[...], "definitions":{"subgraphs":[...]}}
    if isinstance(source, dict) and isinstance(source.get('nodes'), list):
        for node, id_prefix, subgraph_name in _iter_ui_workflow_nodes(source):
            if len(nodes_out) >= WORKFLOW_MAX_NODES:
                break
            if not isinstance(node, dict):
                continue
            node_id = node.get('id', 'N/A')
            node_type = node.get('type') or node.get('class_type') or 'Unknown'
            node_meta = node.get('_meta') if isinstance(node.get('_meta'), dict) else {}
            node_title = node.get('title') or node_meta.get('title')
            params = _normalize_node_params_from_ui_node(node)
            if subgraph_name:
                params.insert(0, {'name': 'subgraph', 'value': subgraph_name})
                params = params[:WORKFLOW_MAX_PARAMS]
            nodes_out.append({
                'id': _workflow_id(f"{id_prefix}{node_id}"),
                'type': _workflow_text(node_type),
                'title': _workflow_text(node_title or node_type),
                'mode': node.get('mode', 0),
                'params': params
            })
    # API format: {"3":{"class_type":"KSampler","inputs":{...}}, ...}
    elif isinstance(source, dict):
        for node_id, node in islice(source.items(), WORKFLOW_MAX_NODES):
            if not isinstance(node, dict) or 'class_type' not in node:
                continue
            params = []
            inputs = node.get('inputs', {})
            if isinstance(inputs, dict):
                for name, value in islice(inputs.items(), WORKFLOW_MAX_PARAMS):
                    params.append({'name': _workflow_text(name), 'value': _workflow_text(value)})
            nodes_out.append({
                'id': _workflow_id(node_id),
                'type': _workflow_text(node.get('class_type', 'Unknown')),
                'title': _workflow_text(_node_title(node) or node.get('class_type', 'Unknown')),
                'mode': 0,
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


def build_workflow_graph(workflow_data, workflow_type):
    """Normalize embedded workflow data for the read-only visual graph viewer."""
    if not isinstance(workflow_data, dict):
        return {"kind": workflow_type or None, "nodes": [], "links": [], "groups": []}

    max_slot = WORKFLOW_MAX_PORTS - 1
    max_nodes = WORKFLOW_MAX_NODES
    max_links = WORKFLOW_MAX_LINKS
    max_subgraphs = WORKFLOW_MAX_SUBGRAPHS
    max_params = WORKFLOW_MAX_PARAMS
    graph_text = _workflow_text
    graph_id = _workflow_id
    remaining_param_text = [WORKFLOW_MAX_EMBEDDED_JSON_BYTES]
    remaining_ports = [WORKFLOW_MAX_TOTAL_PORTS]

    def graph_param_text(name, value, node_type=""):
        if not isinstance(value, str):
            return graph_text(value)
        available = max(0, min(WORKFLOW_MAX_PROMPT_LENGTH, remaining_param_text[0]))
        text = value[:available]
        remaining_param_text[0] -= len(text)
        return text

    def graph_param(name, value, node_type=""):
        text = graph_param_text(name, value, node_type)
        hint = str(name).lower()
        multiline = isinstance(text, str) and (
            len(text) > 80 or "\n" in text or any(word in hint for word in ("text", "prompt", "caption"))
        )
        return {"name": graph_text(name), "value": text, "multiline": multiline}

    def finite_number(value, fallback, minimum=-1_000_000, maximum=1_000_000):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return float(fallback)
        if not math.isfinite(number) or number < minimum or number > maximum:
            return float(fallback)
        return number

    def pair(value, fallback, minimum=-1_000_000, maximum=1_000_000):
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return [
                finite_number(value[0], fallback[0], minimum, maximum),
                finite_number(value[1], fallback[1], minimum, maximum),
            ]
        return [float(fallback[0]), float(fallback[1])]

    def bounded_slot(value):
        if not isinstance(value, int) or isinstance(value, bool):
            return None
        return value if 0 <= value <= max_slot else None

    def slots(value):
        if not isinstance(value, list):
            return []
        result = []
        available = min(max_slot + 1, remaining_ports[0])
        for index, item in enumerate(value[:available]):
            if not isinstance(item, dict):
                result.append({"name": f"slot_{index + 1}", "type": ""})
                continue
            result.append({
                "name": graph_text(item.get("label") or item.get("localized_name") or item.get("name") or f"slot_{index + 1}"),
                "type": graph_text(item.get("type") or ""),
            })
        remaining_ports[0] -= len(result)
        return result

    def generated_slots(records):
        available = min(len(records), remaining_ports[0])
        result = records[:available]
        remaining_ports[0] -= len(result)
        return result

    def reserve_identifier(candidate, used, next_suffixes):
        candidate = graph_id(candidate)
        if candidate not in used:
            used.add(candidate)
            next_suffixes.setdefault(candidate, 2)
            return candidate
        suffix = next_suffixes.get(candidate, 2)
        while True:
            marker = f"~{suffix}"
            alternate = f"{candidate[:max(0, WORKFLOW_MAX_ID_LENGTH - len(marker))]}{marker}"
            suffix += 1
            if alternate not in used:
                used.add(alternate)
                next_suffixes[candidate] = suffix
                return alternate

    if workflow_type == "api":
        api_nodes = {}
        api_aliases = {}
        used_api_ids = set()
        api_id_suffixes = {}

        def api_key(value):
            return (type(value).__name__, graph_id(value))

        for raw_node_id, node in islice(workflow_data.items(), max_nodes):
            if isinstance(node, dict) and node.get("class_type"):
                candidate = graph_id(raw_node_id)
                node_id = reserve_identifier(candidate, used_api_ids, api_id_suffixes)
                api_aliases[api_key(raw_node_id)] = node_id
                api_aliases.setdefault(("*", candidate), node_id)
                api_nodes[node_id] = node
        def node_sort_key(node_id):
            try:
                return (0, int(node_id))
            except (TypeError, ValueError):
                return (1, node_id)

        ordered_ids = sorted(api_nodes, key=node_sort_key)
        incoming = {node_id: set() for node_id in ordered_ids}
        outgoing = {node_id: set() for node_id in ordered_ids}
        linked_inputs = {node_id: set() for node_id in ordered_ids}
        api_links = []
        max_output_slot = {}
        for target_id in ordered_ids:
            inputs = api_nodes[target_id].get("inputs", {})
            if not isinstance(inputs, dict):
                continue
            for input_index, (input_name, value) in enumerate(islice(inputs.items(), max_slot + 1)):
                if not _is_link_ref(value):
                    continue
                source_id = api_aliases.get(api_key(value[0])) or api_aliases.get(("*", graph_id(value[0])))
                if source_id not in api_nodes:
                    continue
                source_slot = bounded_slot(value[1] if len(value) > 1 else 0)
                if source_slot is None:
                    continue
                incoming[target_id].add(source_id)
                outgoing[source_id].add(target_id)
                linked_inputs[target_id].add(input_name)
                max_output_slot[source_id] = max(max_output_slot.get(source_id, -1), source_slot)
                api_links.append({
                    "id": f"api-{len(api_links) + 1}",
                    "from_node": source_id,
                    "from_slot": source_slot,
                    "to_node": target_id,
                    "to_slot": input_index,
                    "type": "",
                })
                if len(api_links) >= max_links:
                    break
            if len(api_links) >= max_links:
                break

        indegree = {node_id: len(incoming[node_id]) for node_id in ordered_ids}
        depths = {node_id: 0 for node_id in ordered_ids}
        queue = sorted((node_id for node_id in ordered_ids if indegree[node_id] == 0), key=node_sort_key)
        processed = set()
        cursor = 0
        while cursor < len(queue):
            node_id = queue[cursor]
            cursor += 1
            processed.add(node_id)
            for target_id in sorted(outgoing[node_id], key=node_sort_key):
                depths[target_id] = max(depths[target_id], depths[node_id] + 1)
                indegree[target_id] -= 1
                if indegree[target_id] == 0:
                    queue.append(target_id)
        if len(processed) != len(ordered_ids):
            cycle_depth = max((depths[node_id] for node_id in processed), default=-1) + 1
            for node_id in ordered_ids:
                if node_id not in processed:
                    depths[node_id] = cycle_depth

        next_y_by_depth = {}
        api_graph_nodes = []
        for node_id in ordered_ids:
            node = api_nodes[node_id]
            depth = depths[node_id]
            inputs = node.get("inputs", {}) if isinstance(node.get("inputs"), dict) else {}
            input_items = list(islice(inputs.items(), max_slot + 1))
            node_type = graph_text(node.get("class_type") or "Unknown")
            params = [
                graph_param(name, value, node_type)
                for name, value in input_items[:max_params]
                if name not in linked_inputs[node_id]
            ]
            input_slots = generated_slots([
                {"name": graph_text(name), "type": ""}
                for name, _value in input_items
            ])
            output_slots = generated_slots([
                {"name": f"output_{slot}", "type": ""}
                for slot in range(max_output_slot.get(node_id, -1) + 1)
            ])
            parameter_height = 14 + sum(104 if param.get("multiline") else 22 for param in params) if params else 0
            node_height = float(max(120, 48 + max(len(input_slots), len(output_slots)) * 22 + parameter_height))
            node_y = next_y_by_depth.get(depth, 0.0)
            next_y_by_depth[depth] = node_y + node_height + 90.0
            meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
            api_graph_nodes.append({
                "id": node_id,
                "type": node_type,
                "title": graph_text(meta.get("title") or node_type),
                "position": [float(depth * 340), node_y],
                "size": [260.0, node_height],
                "inputs": input_slots,
                "outputs": output_slots,
                "params": params,
                "mode": 0,
                "color": "",
                "bgcolor": "",
            })

        api_nodes_by_id = {node["id"]: node for node in api_graph_nodes}
        api_links = [
            link for link in api_links
            if link["from_node"] in api_nodes_by_id
            and link["to_node"] in api_nodes_by_id
            and link["from_slot"] < len(api_nodes_by_id[link["from_node"]]["outputs"])
            and link["to_slot"] < len(api_nodes_by_id[link["to_node"]]["inputs"])
        ]
        return {"kind": "api", "nodes": api_graph_nodes, "links": api_links, "groups": []}

    if workflow_type != "ui" or not isinstance(workflow_data.get("nodes"), list):
        return {"kind": workflow_type or None, "nodes": [], "links": [], "groups": []}

    used_node_ids = set()
    used_link_ids = set()
    node_id_suffixes = {}
    link_id_suffixes = {}

    definitions = workflow_data.get("definitions")
    raw_subgraphs = definitions.get("subgraphs") if isinstance(definitions, dict) else []
    if not isinstance(raw_subgraphs, list):
        raw_subgraphs = []
    subgraph_definitions = {
        graph_id(item.get("id")): item
        for item in raw_subgraphs[:max_subgraphs]
        if isinstance(item, dict) and item.get("id") is not None
    }

    def normalize_ui_node(node, index, aliases, id_prefix="", subgraph_name=None):
        node_type = graph_text(node.get("type") or node.get("class_type") or "Unknown")
        definition = subgraph_definitions.get(graph_id(node.get("type"))) if not id_prefix else None
        params = []
        for param in _normalize_node_params_from_ui_node(
            node,
            max_params,
            lambda name, value: graph_param_text(name, value, node_type),
        ):
            if isinstance(param, dict):
                value = param.get("value", "")
                hint = str(param.get('name', '')).lower()
                params.append({
                    "name": graph_text(param.get("name")),
                    "value": value,
                    "multiline": isinstance(value, str) and (
                        len(value) > 80 or "\n" in value or any(word in hint for word in ("text", "prompt", "caption"))
                    ),
                })
        if definition:
            widget_values = node.get("widgets_values")
            widget_values = widget_values if isinstance(widget_values, list) else []
            semantic_params = []
            definition_inputs = definition.get("inputs") if isinstance(definition.get("inputs"), list) else []
            # ComfyUI serializes subgraph widget values in the same order as the
            # definition's trailing scalar inputs. Boundary media ports do not have
            # widget values, so align from the end instead of guessing from value_* names.
            widget_ports = definition_inputs[-len(widget_values):] if widget_values else []
            for widget_index, (port, widget_value) in enumerate(zip(widget_ports, widget_values)):
                if not isinstance(port, dict):
                    continue
                name = port.get("label") or port.get("name") or f"widget_{widget_index + 1}"
                semantic_params.append(graph_param(name, widget_value, node_type))
            if semantic_params:
                params = semantic_params[:max_params]
        scoped_id = f"{id_prefix}{graph_id(node.get('id', index))}"
        node_id = reserve_identifier(scoped_id, used_node_ids, node_id_suffixes)
        aliases.setdefault(scoped_id, node_id)
        normalized = {
            "id": node_id,
            "type": node_type,
            "title": graph_text(node.get("title") or (definition or {}).get("name") or node.get("type") or node.get("class_type") or "Unknown"),
            "position": pair(node.get("pos"), (index % 4 * 320, index // 4 * 250)),
            "size": pair(node.get("size"), (240, 150), 1, 10_000),
            "inputs": slots((definition or {}).get("inputs") or node.get("inputs")),
            "outputs": slots((definition or {}).get("outputs") or node.get("outputs")),
            "params": params,
            "mode": finite_number(node.get("mode", 0), 0),
            "color": graph_text(node.get("color")),
            "bgcolor": graph_text(node.get("bgcolor")),
        }
        if subgraph_name:
            normalized["subgraph"] = subgraph_name
        return normalized

    def append_ui_links(destination, raw_links, scope_nodes, aliases, id_prefix=""):
        if not isinstance(raw_links, (list, tuple)):
            return
        nodes_by_id = {node["id"]: node for node in scope_nodes}
        remaining_links = max(0, max_links - len(destination))
        for index, link in enumerate(islice(raw_links, remaining_links)):
            if isinstance(link, dict):
                raw_id = link.get("id", index)
                raw_from_node = link.get("origin_id", link.get("from_node"))
                raw_from_slot = link.get("origin_slot", link.get("from_slot"))
                raw_to_node = link.get("target_id", link.get("to_node"))
                raw_to_slot = link.get("target_slot", link.get("to_slot"))
                link_type = link.get("type", "")
            elif isinstance(link, (list, tuple)) and len(link) >= 5:
                raw_id = link[0] if link[0] is not None else index
                raw_from_node = link[1]
                raw_from_slot = link[2]
                raw_to_node = link[3]
                raw_to_slot = link[4]
                link_type = link[5] if len(link) > 5 else ""
            else:
                continue
            from_slot = bounded_slot(raw_from_slot)
            to_slot = bounded_slot(raw_to_slot)
            if from_slot is None or to_slot is None:
                continue
            from_node = aliases.get(f"{id_prefix}{graph_id(raw_from_node)}")
            to_node = aliases.get(f"{id_prefix}{graph_id(raw_to_node)}")
            source = nodes_by_id.get(from_node)
            target = nodes_by_id.get(to_node)
            if not source or not target:
                continue
            if from_slot >= len(source["outputs"]) or to_slot >= len(target["inputs"]):
                continue
            destination.append({
                "id": reserve_identifier(f"{id_prefix}{graph_id(raw_id)}", used_link_ids, link_id_suffixes),
                "from_node": from_node,
                "from_slot": from_slot,
                "to_node": to_node,
                "to_slot": to_slot,
                "type": graph_text(link_type),
            })

    top_aliases = {}
    top_nodes = [
        normalize_ui_node(node, index, top_aliases)
        for index, node in enumerate((workflow_data.get("nodes") or [])[:max_nodes])
        if isinstance(node, dict)
    ]
    nodes = list(top_nodes)
    links = []
    groups = []
    append_ui_links(links, workflow_data.get("links"), top_nodes, top_aliases)

    top_right = max((node["position"][0] + node["size"][0] for node in top_nodes), default=-260.0)
    top_y = min((node["position"][1] for node in top_nodes), default=0.0)
    subgraph_x = top_right + 260.0
    subgraph_y = top_y
    subgraphs = raw_subgraphs

    used_subgraph_ids = set()
    subgraph_id_suffixes = {}
    for subgraph_index, subgraph in enumerate(subgraphs[:max_subgraphs]):
        if len(nodes) >= max_nodes:
            break
        if not isinstance(subgraph, dict) or not isinstance(subgraph.get("nodes"), list):
            continue
        base_subgraph_id = graph_id(subgraph.get("id") or subgraph.get("name") or f"subgraph-{subgraph_index + 1}")
        subgraph_id = reserve_identifier(base_subgraph_id, used_subgraph_ids, subgraph_id_suffixes)
        subgraph_name = graph_text(subgraph.get("name") or subgraph_id)
        id_prefix = f"{subgraph_id}:"
        subgraph_aliases = {}
        remaining_nodes = max_nodes - len(nodes)
        subgraph_nodes = [
            normalize_ui_node(node, index, subgraph_aliases, id_prefix, subgraph_name)
            for index, node in enumerate((subgraph.get("nodes") or [])[:remaining_nodes])
            if isinstance(node, dict)
        ]
        for interface_key, title, ports_key, ports_side in (
            ("inputNode", "Subgraph Inputs", "inputs", "outputs"),
            ("outputNode", "Subgraph Outputs", "outputs", "inputs"),
        ):
            if len(nodes) + len(subgraph_nodes) >= max_nodes:
                break
            interface = subgraph.get(interface_key)
            if not isinstance(interface, dict) or interface.get("id") is None:
                continue
            bounding = interface.get("bounding")
            if not isinstance(bounding, (list, tuple)):
                bounding = []
            scoped_interface_id = f"{id_prefix}{graph_id(interface.get('id'))}"
            interface_node_id = reserve_identifier(scoped_interface_id, used_node_ids, node_id_suffixes)
            subgraph_aliases.setdefault(scoped_interface_id, interface_node_id)
            interface_node = {
                "id": interface_node_id,
                "type": title.replace(" ", ""),
                "title": title,
                "position": pair(bounding[:2], (0, 0)),
                "size": pair(bounding[2:4], (140, 120), 1, 10_000),
                "inputs": slots(subgraph.get(ports_key)) if ports_side == "inputs" else [],
                "outputs": slots(subgraph.get(ports_key)) if ports_side == "outputs" else [],
                "params": [],
                "mode": 0,
                "color": "",
                "bgcolor": "",
                "subgraph": subgraph_name,
            }
            subgraph_nodes.append(interface_node)
        if not subgraph_nodes:
            continue

        def rendered_node_size(node):
            width = max(210.0, min(420.0, node["size"][0]))
            content_height = 48.0 + max(len(node["inputs"]), len(node["outputs"])) * 22.0
            if node["params"]:
                content_height += 14.0 + sum(
                    104.0 if param.get("multiline") else 22.0
                    for param in node["params"][:7]
                )
            height = max(92.0, min(12_000.0, max(node["size"][1], content_height)))
            return width, height

        local_min_x = min(node["position"][0] for node in subgraph_nodes)
        local_min_y = min(node["position"][1] for node in subgraph_nodes)
        local_max_x = max(
            node["position"][0] + rendered_node_size(node)[0]
            for node in subgraph_nodes
        )
        local_max_y = max(
            node["position"][1] + rendered_node_size(node)[1]
            for node in subgraph_nodes
        )
        padding_x = 74.0
        padding_top = 88.0
        padding_bottom = 64.0
        group_width = local_max_x - local_min_x + padding_x * 2
        group_height = local_max_y - local_min_y + padding_top + padding_bottom
        for node in subgraph_nodes:
            node["position"] = [
                subgraph_x + padding_x + node["position"][0] - local_min_x,
                subgraph_y + padding_top + node["position"][1] - local_min_y,
            ]
        groups.append({
            "id": graph_id(f"subgraph:{subgraph_id}"),
            "title": subgraph_name,
            "position": [subgraph_x, subgraph_y],
            "size": [group_width, group_height],
            "subgraph": True,
        })
        nodes.extend(subgraph_nodes)
        append_ui_links(links, subgraph.get("links"), subgraph_nodes, subgraph_aliases, id_prefix)
        subgraph_y += group_height + 140.0

    return {"kind": "ui", "nodes": nodes, "links": links, "groups": groups}
