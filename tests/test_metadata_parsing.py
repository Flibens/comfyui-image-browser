import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "__init__.py"


def load_image_browser_module():
    """Load the ComfyUI extension module with tiny ComfyUI/server stubs."""
    routes = types.SimpleNamespace(
        get=lambda _path: (lambda func: func),
        post=lambda _path: (lambda func: func),
    )
    server_stub = types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=routes)))

    temp_root = Path(tempfile.mkdtemp(prefix="image-browser-test-"))
    folder_paths_stub = types.SimpleNamespace(
        get_user_directory=lambda: str(temp_root / "user"),
        user_directory=str(temp_root / "user"),
        base_path=str(temp_root),
        get_output_directory=lambda: str(temp_root / "output"),
        get_input_directory=lambda: str(temp_root / "input"),
        get_temp_directory=lambda: str(temp_root / "temp"),
    )
    for name in ["user", "output", "input", "temp"]:
        (temp_root / name).mkdir(parents=True, exist_ok=True)

    original_server = sys.modules.get("server")
    original_folder_paths = sys.modules.get("folder_paths")
    original_shared = os.environ.get("LUMAVAULT_SHARED_BROWSER_STATE_FILE")
    os.environ["LUMAVAULT_SHARED_BROWSER_STATE_FILE"] = str(temp_root / "shared.json")
    sys.modules["server"] = server_stub
    sys.modules["folder_paths"] = folder_paths_stub

    try:
        spec = importlib.util.spec_from_file_location("image_browser_under_test", MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if original_shared is None:
            os.environ.pop("LUMAVAULT_SHARED_BROWSER_STATE_FILE", None)
        else:
            os.environ["LUMAVAULT_SHARED_BROWSER_STATE_FILE"] = original_shared
        if original_server is None:
            sys.modules.pop("server", None)
        else:
            sys.modules["server"] = original_server
        if original_folder_paths is None:
            sys.modules.pop("folder_paths", None)
        else:
            sys.modules["folder_paths"] = original_folder_paths


ib = load_image_browser_module()


class MetadataParsingTests(unittest.TestCase):
    def test_shared_collections_are_used_for_favorites_and_folders(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original_shared = getattr(ib, "SHARED_BROWSER_STATE_FILE", None)
            original_folders = ib.ADDITIONAL_FOLDERS_FILE
            original_favorites = ib.FAVORITES_FILE
            try:
                ib.SHARED_BROWSER_STATE_FILE = root / "shared.json"
                ib.ADDITIONAL_FOLDERS_FILE = root / "folders.json"
                ib.FAVORITES_FILE = root / "favorites.json"
                folders = [{"id": "vault", "name": "Vault", "path": str(root / "media")}]
                favorites = ["vault:favorite.png"]
                ib.save_json(folders, ib.ADDITIONAL_FOLDERS_FILE)
                ib.save_json(favorites, ib.FAVORITES_FILE)

                self.assertEqual(ib.load_json(ib.ADDITIONAL_FOLDERS_FILE), folders)
                self.assertEqual(ib.load_json(ib.FAVORITES_FILE), favorites)

                ib.SHARED_BROWSER_STATE_FILE.write_text(
                    '{"version": 1, "folders": [{"id": "other", "name": "Other", "path": "C:/media"}], "favorites": ["other:latest.png"]}',
                    encoding="utf-8",
                )
                self.assertEqual(ib.load_json(ib.ADDITIONAL_FOLDERS_FILE)[0]["id"], "other")
                self.assertEqual(ib.load_json(ib.FAVORITES_FILE), ["other:latest.png"])
            finally:
                ib.ADDITIONAL_FOLDERS_FILE = original_folders
                ib.FAVORITES_FILE = original_favorites
                if original_shared is not None:
                    ib.SHARED_BROWSER_STATE_FILE = original_shared

    def test_concurrent_shared_updates_preserve_both_collections(self):
        root = Path(tempfile.mkdtemp(prefix="image-browser-concurrency-test-"))
        shared = root / "shared.json"
        shared.write_text(json.dumps({"version": 1, "folders": [], "favorites": []}), encoding="utf-8")
        original_paths = (ib.SHARED_BROWSER_STATE_FILE, ib.ADDITIONAL_FOLDERS_FILE, ib.FAVORITES_FILE)
        original_read = ib._read_shared_collections
        barrier = threading.Barrier(2)
        try:
            ib.SHARED_BROWSER_STATE_FILE = shared
            ib.ADDITIONAL_FOLDERS_FILE = root / "folders.json"
            ib.FAVORITES_FILE = root / "favorites.json"

            def delayed_read():
                value = original_read()
                time.sleep(0.05)
                return value

            ib._read_shared_collections = delayed_read
            folders = [{"id": "album", "name": "Album", "path": str(root / "album")}]
            favorites = ["album:favorite.png"]

            def save(value, path):
                barrier.wait()
                ib.save_json(value, path)

            threads = [
                threading.Thread(target=save, args=(folders, ib.ADDITIONAL_FOLDERS_FILE)),
                threading.Thread(target=save, args=(favorites, ib.FAVORITES_FILE)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            self.assertFalse(any(thread.is_alive() for thread in threads))
        finally:
            ib._read_shared_collections = original_read
            ib.SHARED_BROWSER_STATE_FILE, ib.ADDITIONAL_FOLDERS_FILE, ib.FAVORITES_FILE = original_paths

        state = json.loads(shared.read_text(encoding="utf-8"))
        self.assertEqual(state["folders"], folders)
        self.assertEqual(state["favorites"], favorites)

    def test_shared_write_failure_does_not_update_local_mirror(self):
        root = Path(tempfile.mkdtemp(prefix="image-browser-write-failure-"))
        shared_file = root / "shared.json"
        favorites_file = root / "favorites.json"
        folders_file = root / "folders.json"
        shared_file.write_text(json.dumps({"version": 1, "folders": [], "favorites": []}), encoding="utf-8")
        favorites_file.write_text("[]", encoding="utf-8")
        folders_file.write_text("[]", encoding="utf-8")

        old_shared = ib.SHARED_BROWSER_STATE_FILE
        old_favorites = ib.FAVORITES_FILE
        old_folders = ib.ADDITIONAL_FOLDERS_FILE
        try:
            ib.SHARED_BROWSER_STATE_FILE = shared_file
            ib.FAVORITES_FILE = favorites_file
            ib.ADDITIONAL_FOLDERS_FILE = folders_file
            with mock.patch.object(ib.os, "replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    ib.save_json(["default:a.png"], favorites_file)
            self.assertEqual(json.loads(favorites_file.read_text(encoding="utf-8")), [])
            self.assertEqual(json.loads(shared_file.read_text(encoding="utf-8"))["favorites"], [])
        finally:
            ib.SHARED_BROWSER_STATE_FILE = old_shared
            ib.FAVORITES_FILE = old_favorites
            ib.ADDITIONAL_FOLDERS_FILE = old_folders

    def test_concurrent_favorite_toggles_preserve_both_changes(self):
        root = Path(tempfile.mkdtemp(prefix="image-browser-favorite-race-"))
        shared = root / "shared.json"
        shared.write_text(json.dumps({"version": 1, "folders": [], "favorites": []}), encoding="utf-8")
        original_paths = (ib.SHARED_BROWSER_STATE_FILE, ib.FAVORITES_FILE)
        original_read = ib._read_shared_collections
        barrier = threading.Barrier(2)
        try:
            ib.SHARED_BROWSER_STATE_FILE = shared
            ib.FAVORITES_FILE = root / "favorites.json"

            def delayed_read():
                value = original_read()
                time.sleep(0.05)
                return value

            ib._read_shared_collections = delayed_read

            def toggle(key):
                barrier.wait()
                ib._toggle_favorite(key)

            threads = [
                threading.Thread(target=toggle, args=("default:a.png",)),
                threading.Thread(target=toggle, args=("default:b.png",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
            self.assertFalse(any(thread.is_alive() for thread in threads))
        finally:
            ib._read_shared_collections = original_read
            ib.SHARED_BROWSER_STATE_FILE, ib.FAVORITES_FILE = original_paths

        state = json.loads(shared.read_text(encoding="utf-8"))
        self.assertEqual(set(state["favorites"]), {"default:a.png", "default:b.png"})

    def test_malformed_shared_collections_are_ignored(self):
        self.assertIsNone(ib._normalize_shared_collections({"folders": None, "favorites": None}))

    def test_initialization_respects_intentionally_empty_shared_collections(self):
        root = Path(tempfile.mkdtemp(prefix="image-browser-state-test-"))
        local = root / "comfyui-image-browser"
        local.mkdir(parents=True)
        folders = [{"id": "album", "name": "Album", "path": str(root / "album")}]
        favorites = ["album:favorite.png"]
        (local / "folders.json").write_text(json.dumps(folders), encoding="utf-8")
        (local / "favorites.json").write_text(json.dumps(favorites), encoding="utf-8")
        shared = root / "shared.json"
        shared.write_text(json.dumps({"version": 1, "folders": [], "favorites": []}), encoding="utf-8")

        original_paths = (
            ib.STATE_DIRECTORY,
            ib.ADDITIONAL_FOLDERS_FILE,
            ib.FAVORITES_FILE,
            ib.SHARED_BROWSER_STATE_FILE,
            ib.LEGACY_STATE_DIRECTORY,
            ib.LEGACY_ADDITIONAL_FOLDERS_FILE,
            ib.LEGACY_FAVORITES_FILE,
        )
        try:
            ib.STATE_DIRECTORY = local
            ib.ADDITIONAL_FOLDERS_FILE = local / "folders.json"
            ib.FAVORITES_FILE = local / "favorites.json"
            ib.SHARED_BROWSER_STATE_FILE = shared
            ib.LEGACY_STATE_DIRECTORY = root / "missing-legacy-state"
            ib.LEGACY_ADDITIONAL_FOLDERS_FILE = root / "missing-folders.json"
            ib.LEGACY_FAVORITES_FILE = root / "missing-favorites.json"
            ib._initialize_persistent_state()
        finally:
            (
                ib.STATE_DIRECTORY,
                ib.ADDITIONAL_FOLDERS_FILE,
                ib.FAVORITES_FILE,
                ib.SHARED_BROWSER_STATE_FILE,
                ib.LEGACY_STATE_DIRECTORY,
                ib.LEGACY_ADDITIONAL_FOLDERS_FILE,
                ib.LEGACY_FAVORITES_FILE,
            ) = original_paths

        restored = json.loads(shared.read_text(encoding="utf-8"))
        self.assertEqual(restored["folders"], [])
        self.assertEqual(restored["favorites"], [])
        self.assertEqual(json.loads((local / "folders.json").read_text(encoding="utf-8")), [])
        self.assertEqual(json.loads((local / "favorites.json").read_text(encoding="utf-8")), [])

    def test_default_save_image_api_prompt_follows_prompt_links_and_extracts_settings_loras_and_size(self):
        metadata = {
            "prompt": {
                "3": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": 851833015696796,
                        "steps": 4,
                        "cfg": 1.0,
                        "sampler_name": "euler",
                        "scheduler": "simple",
                        "positive": ["113", 0],
                        "negative": ["114", 0],
                        "latent_image": ["124", 0],
                        "model": ["116", 0],
                    },
                    "_meta": {"title": "KSampler"},
                },
                "113": {
                    "class_type": "TextEncodeQwenImageEditPlus",
                    "inputs": {"prompt": ["121", 0], "clip": ["38", 0], "vae": ["39", 0]},
                    "_meta": {"title": "Text Encode Positive"},
                },
                "114": {
                    "class_type": "TextEncodeQwenImageEditPlus",
                    "inputs": {"prompt": "low quality", "clip": ["38", 0], "vae": ["39", 0]},
                    "_meta": {"title": "Text Encode Negative"},
                },
                "121": {
                    "class_type": "StringConcatenate",
                    "inputs": {"string_a": "amateur photo", "string_b": "forest portrait", "delimiter": ", "},
                    "_meta": {"title": "Trigger Word + Positive Prompt"},
                },
                "124": {
                    "class_type": "EmptyLatentImage",
                    "inputs": {"width": ["125", 0], "height": ["125", 1], "batch_size": 1},
                },
                "125": {
                    "class_type": "ResolutionMaster",
                    "inputs": {"width": 1024, "height": 1024},
                },
                "116": {
                    "class_type": "NunchakuQwenImageLoraLoader",
                    "inputs": {"lora_name": "photo_style.safetensors", "lora_strength": 0.75, "model": ["110", 0]},
                },
            }
        }

        parsed = ib.parse_comfy_metadata(metadata)

        self.assertEqual(parsed["prompt"], "amateur photo, forest portrait")
        self.assertEqual(parsed["negative_prompt"], "low quality")
        self.assertEqual(parsed["seed"], 851833015696796)
        self.assertEqual(parsed["steps"], 4)
        self.assertEqual(parsed["cfg"], 1.0)
        self.assertEqual(parsed["sampler"], "euler")
        self.assertEqual(parsed["scheduler"], "simple")
        self.assertEqual(parsed["width"], 1024)
        self.assertEqual(parsed["height"], 1024)
        self.assertEqual(parsed["loras"], [{"name": "photo_style.safetensors", "strength_model": 0.75, "strength_clip": 0.75}])

    def test_default_save_image_without_negative_does_not_invent_negative_prompt_from_other_positive_text_nodes(self):
        metadata = {
            "prompt": {
                "3": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": 123,
                        "steps": 8,
                        "cfg": 1.0,
                        "sampler_name": "euler",
                        "scheduler": "simple",
                        "positive": ["6", 0],
                        "latent_image": ["5", 0],
                    },
                    "_meta": {"title": "KSampler"},
                },
                "6": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": "one positive prompt", "clip": ["4", 0]},
                    "_meta": {"title": "Positive Prompt"},
                },
                "7": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": "another positive helper text", "clip": ["4", 0]},
                    "_meta": {"title": "Helper Prompt"},
                },
                "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 1024}},
            }
        }

        parsed = ib.parse_comfy_metadata(metadata)

        self.assertEqual(parsed["prompt"], "one positive prompt")
        self.assertIsNone(parsed["negative_prompt"])

    def test_default_save_image_same_positive_and_negative_link_is_treated_as_no_negative_prompt(self):
        metadata = {
            "prompt": {
                "3": {
                    "class_type": "KSampler",
                    "inputs": {
                        "seed": 123,
                        "steps": 8,
                        "cfg": 1.0,
                        "sampler_name": "euler",
                        "scheduler": "simple",
                        "positive": ["6", 0],
                        "negative": ["6", 0],
                        "latent_image": ["5", 0],
                    },
                    "_meta": {"title": "KSampler"},
                },
                "6": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": "one shared prompt", "clip": ["4", 0]},
                    "_meta": {"title": "Prompt"},
                },
                "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 1024}},
            }
        }

        parsed = ib.parse_comfy_metadata(metadata)

        self.assertEqual(parsed["prompt"], "one shared prompt")
        self.assertIsNone(parsed["negative_prompt"])

    def test_workflow_nodes_include_nodes_inside_comfyui_subgraph_definitions(self):
        metadata = {
            "workflow": {
                "nodes": [
                    {"id": 1, "type": "SaveImage", "mode": 0, "inputs": [], "widgets_values": ["img"]},
                    {"id": 2, "type": "MutedTopLevel", "mode": 2, "inputs": [], "widgets_values": []},
                ],
                "definitions": {
                    "subgraphs": [
                        {
                            "id": "subgraph-abc",
                            "name": "Qwen Text-to-image (Subgraph)",
                            "nodes": [
                                {
                                    "id": 42,
                                    "type": "CLIPTextEncode",
                                    "mode": 0,
                                    "inputs": [
                                        {"name": "clip", "link": 10},
                                        {"name": "input_2", "widget": {"name": "text"}},
                                    ],
                                    "widgets_values": ["inside subgraph prompt"],
                                }
                            ],
                        }
                    ]
                },
            }
        }

        nodes = ib.extract_workflow_nodes(metadata)

        node_ids = {node["id"] for node in nodes}
        self.assertIn("1", node_ids)
        self.assertIn("subgraph-abc:42", node_ids)
        self.assertIn("2", node_ids)
        muted_node = next(node for node in nodes if node["id"] == "2")
        self.assertEqual(muted_node["mode"], 2)
        subgraph_node = next(node for node in nodes if node["id"] == "subgraph-abc:42")
        self.assertEqual(subgraph_node["type"], "CLIPTextEncode")
        self.assertTrue(any(param["name"] == "subgraph" and param["value"] == "Qwen Text-to-image (Subgraph)" for param in subgraph_node["params"]))
        self.assertTrue(any(param["name"] == "clip" and param["value"] == "10" for param in subgraph_node["params"]))
        self.assertTrue(any(param["name"] == "input_2" and param["value"] == "inside subgraph prompt" for param in subgraph_node["params"]))
        self.assertFalse(any(param["name"].startswith("widget_") for param in subgraph_node["params"]))

    def test_split_sampler_showtext_and_model_priority_match_lumavault(self):
        graph = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "pixel-dit.safetensors"}},
            "2": {"class_type": "SamplerCustom", "inputs": {"noise_seed": 3, "cfg": 1.0, "positive": ["6", 0], "negative": ["7", 0]}},
            "3": {"class_type": "BasicScheduler", "inputs": {"scheduler": "simple", "steps": 4}},
            "4": {"class_type": "AILab_QwenVL", "inputs": {"model_name": "Qwen3-VL-2B-Instruct", "seed": 99}},
            "5": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "lcm"}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ["4", 0]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "low quality"}, "_meta": {"title": "Negative Prompt"}},
            "8": {"class_type": "ShowText|pysssss", "inputs": {"text_0": "a luminous mushroom forest", "text": ["4", 0]}},
        }

        parsed = ib.parse_comfy_metadata({"prompt": graph})

        self.assertEqual(parsed["prompt"], "a luminous mushroom forest")
        self.assertEqual(parsed["negative_prompt"], "low quality")
        self.assertEqual(parsed["model"], "pixel-dit.safetensors")
        self.assertEqual(parsed["seed"], 3)
        self.assertEqual(parsed["steps"], 4)
        self.assertEqual(parsed["cfg"], 1.0)
        self.assertEqual(parsed["sampler"], "lcm")
        self.assertEqual(parsed["scheduler"], "simple")

    def test_lora_manager_only_reports_active_structured_entries(self):
        metadata = {"workflow": {"nodes": [{
            "id": 61,
            "type": "Lora Stacker (LoraManager)",
            "mode": 0,
            "widgets_values": [
                "<lora:active_style:0.80> <lora:unused_style:1.00>",
                [],
                [
                    {"name": "active_style", "strength": 0.8, "clipStrength": 0.6, "active": True},
                    {"name": "unused_style", "strength": 1.0, "clipStrength": 1.0, "active": False},
                ],
            ],
        }]}}

        parsed = ib.parse_comfy_metadata(metadata)

        self.assertEqual(parsed["loras"], [{
            "name": "active_style",
            "strength_model": 0.8,
            "strength_clip": 0.6,
        }])

    def test_comfyui_parameters_hide_noisy_exporter_fields(self):
        parsed = ib.parse_comfy_metadata({"parameters": (
            "portrait\nSteps: 30, Clip skip: 1, Model hash: deadbeef, "
            "Model: example, Version: ComfyUI"
        )})
        self.assertNotIn("Clip skip", parsed["extras"])
        self.assertNotIn("Model hash", parsed["extras"])
        self.assertNotIn("Version", parsed["extras"])

    def test_comfyui_parameters_reports_only_lora_corroborated_by_hashes_payload(self):
        metadata = {"parameters": (
            "portrait, <lora:active_style:0.75> <lora:disabled_style:1>\n"
            "Steps: 30, Hashes: {\"LORA:active_style\":\"abc123\",\"model\":\"deadbeef\"}, "
            "Version: ComfyUI"
        )}
        self.assertEqual(ib.parse_comfy_metadata(metadata)["loras"], [{
            "name": "active_style", "strength_model": 0.75, "strength_clip": 0.75,
        }])

    def test_comfyui_parameters_do_not_treat_prompt_only_lora_tag_as_executed(self):
        metadata = {"parameters": (
            "portrait, <lora:disabled_style:1>\nSteps: 30, "
            "Hashes: {\"model\":\"deadbeef\"}, Version: ComfyUI"
        )}
        self.assertEqual(ib.parse_comfy_metadata(metadata)["loras"], [])

    def test_api_lora_manager_stack_reports_only_active_entries_on_executed_model_path(self):
        metadata = {"prompt": {
            "base": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}},
            "stack": {"class_type": "Lora Stacker (LoraManager)", "inputs": {"loras": {"__value__": [
                {"name": "active_style", "strength": 0.8, "clipStrength": 0.6, "active": True},
                {"name": "disabled_style", "strength": 1.0, "active": False},
            ]}}},
            "loader": {"class_type": "Lora Loader (LoraManager)", "inputs": {
                "model": ["base", 0], "lora_stack": ["stack", 0],
            }},
            "sampler": {"class_type": "KSampler", "inputs": {"model": ["loader", 0]}},
            "unused": {"class_type": "Lora Stacker (LoraManager)", "inputs": {
                "loras": {"__value__": [{"name": "unselected_style", "strength": 1.0, "active": True}]},
            }},
        }}
        self.assertEqual(ib.parse_comfy_metadata(metadata)["loras"], [{
            "name": "active_style", "strength_model": 0.8, "strength_clip": 0.6,
        }])

    def test_api_sampler_custom_direct_model_ignores_disconnected_lora(self):
        graph = {
            "base": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}},
            "active": {"class_type": "LoraLoader", "inputs": {
                "model": ["base", 0], "lora_name": "active.safetensors", "strength_model": 1.0,
            }},
            "unused": {"class_type": "LoraLoader", "inputs": {
                "model": ["base", 0], "lora_name": "unused.safetensors", "strength_model": 1.0,
            }},
            "sampler": {"class_type": "SamplerCustom", "inputs": {"model": ["active", 0]}},
        }
        self.assertEqual(ib.parse_comfy_metadata({"prompt": graph})["loras"], [{
            "name": "active.safetensors", "strength_model": 1.0, "strength_clip": 1.0,
        }])

    def test_active_loras_with_same_basename_in_different_folders_remain_distinct(self):
        graph = {
            "base": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}},
            "first": {"class_type": "LoraLoader", "inputs": {
                "model": ["base", 0], "lora_name": "styles/shared.safetensors", "strength_model": 0.5,
            }},
            "second": {"class_type": "LoraLoader", "inputs": {
                "model": ["first", 0], "lora_name": "characters/shared.safetensors", "strength_model": 0.8,
            }},
            "sampler": {"class_type": "KSampler", "inputs": {"model": ["second", 0]}},
        }
        self.assertEqual(ib.parse_comfy_metadata({"prompt": graph})["loras"], [
            {"name": "styles/shared.safetensors", "strength_model": 0.5, "strength_clip": 0.5},
            {"name": "characters/shared.safetensors", "strength_model": 0.8, "strength_clip": 0.8},
        ])

    def test_api_model_switch_reports_only_lora_on_selected_branch(self):
        graph = {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "base.safetensors"}},
            "2": {"class_type": "LoraLoaderModelOnly", "inputs": {
                "model": ["1", 0], "lora_name": "optional.safetensors", "strength_model": 1.0,
            }},
            "3": {"class_type": "ComfySwitchNode", "inputs": {
                "switch": False, "on_false": ["1", 0], "on_true": ["2", 0],
            }},
            "4": {"class_type": "KSampler", "inputs": {"model": ["3", 0]}},
        }
        self.assertEqual(ib.parse_comfy_metadata({"prompt": graph})["loras"], [])
        graph["3"]["inputs"]["switch"] = True
        self.assertEqual(ib.parse_comfy_metadata({"prompt": graph})["loras"], [{
            "name": "optional.safetensors", "strength_model": 1.0, "strength_clip": 1.0,
        }])

    def test_parameter_hash_paths_disambiguate_same_basename(self):
        metadata = {"parameters": (
            "portrait, <lora:styles/shared:0.7> <lora:characters/shared:0.9>\n"
            "Steps: 20, Hashes: {\"LORA:styles/shared\":\"abc123\"}, Version: ComfyUI"
        )}
        self.assertEqual(ib.parse_comfy_metadata(metadata)["loras"], [{
            "name": "styles/shared", "strength_model": 0.7, "strength_clip": 0.7,
        }])

    def test_mixed_parameters_and_workflow_preserve_distinct_same_basename_loras(self):
        metadata = {
            "parameters": (
                "portrait, <lora:shared:0.5>\nSteps: 20, "
                "Hashes: {\"LORA:shared\":\"abc123\"}, Version: ComfyUI"
            ),
            "workflow": {"nodes": [
                {"id": 1, "type": "LoraLoader", "mode": 0,
                 "widgets_values": ["styles/shared.safetensors", 0.5, 0.5]},
                {"id": 2, "type": "LoraLoader", "mode": 0,
                 "widgets_values": ["characters/shared.safetensors", 0.8, 0.8]},
            ]},
        }
        self.assertEqual(ib.parse_comfy_metadata(metadata)["loras"], [
            {"name": "styles/shared.safetensors", "strength_model": 0.5, "strength_clip": 0.5},
            {"name": "characters/shared.safetensors", "strength_model": 0.8, "strength_clip": 0.8},
        ])

    def test_sampler_custom_ignores_disconnected_guider_lora(self):
        graph = {
            "base": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}},
            "active": {"class_type": "LoraLoader", "inputs": {
                "model": ["base", 0], "lora_name": "active.safetensors", "strength_model": 1.0,
            }},
            "unused": {"class_type": "LoraLoader", "inputs": {
                "model": ["base", 0], "lora_name": "unused.safetensors", "strength_model": 1.0,
            }},
            "active_guider": {"class_type": "CFGGuider", "inputs": {"model": ["active", 0]}},
            "unused_guider": {"class_type": "CFGGuider", "inputs": {"model": ["unused", 0]}},
            "sampler": {"class_type": "SamplerCustomAdvanced", "inputs": {"guider": ["active_guider", 0]}},
        }
        self.assertEqual(ib.parse_comfy_metadata({"prompt": graph})["loras"], [{
            "name": "active.safetensors", "strength_model": 1.0, "strength_clip": 1.0,
        }])

    def test_numbered_model_switch_follows_selected_branch(self):
        graph = {
            "base": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}},
            "optional": {"class_type": "LoraLoader", "inputs": {
                "model": ["base", 0], "lora_name": "optional.safetensors", "strength_model": 1.0,
            }},
            "switch": {"class_type": "ModelSwitch", "inputs": {
                "select": 1, "model1": ["optional", 0], "model2": ["base", 0],
            }},
            "sampler": {"class_type": "KSampler", "inputs": {"model": ["switch", 0]}},
        }
        self.assertEqual(ib.parse_comfy_metadata({"prompt": graph})["loras"], [{
            "name": "optional.safetensors", "strength_model": 1.0, "strength_clip": 1.0,
        }])
        graph["switch"]["inputs"]["select"] = 2
        self.assertEqual(ib.parse_comfy_metadata({"prompt": graph})["loras"], [])

    def test_bypassed_ui_lora_is_not_reported_without_api_graph(self):
        metadata = {"workflow": {"nodes": [{
            "id": 35, "type": "LoraLoaderModelOnly", "mode": 4,
            "widgets_values": ["bypassed.safetensors", 1.0],
        }]}}
        self.assertEqual(ib.parse_comfy_metadata(metadata)["loras"], [])

    def test_parameters_and_workflow_do_not_duplicate_same_lora_with_file_extension(self):
        metadata = {
            "parameters": (
                "portrait, <lora:active_style:0.75>\nSteps: 20, "
                "Hashes: {\"LORA:active_style\":\"abc123\"}, Version: ComfyUI"
            ),
            "workflow": {"nodes": [{
                "id": 8, "type": "LoraLoader", "mode": 0,
                "widgets_values": ["active_style.safetensors", 0.75, 0.75],
            }]},
        }
        self.assertEqual(ib.parse_comfy_metadata(metadata)["loras"], [{
            "name": "active_style", "strength_model": 0.75, "strength_clip": 0.75,
        }])

    def test_conditioning_without_text_does_not_turn_model_name_into_prompt(self):
        graph = {
            "model": {"class_type": "UNETLoader", "inputs": {"unet_name": "seedvr2_7b_int8_convrot.safetensors"}},
            "conditioning": {"class_type": "SeedVR2Conditioning", "inputs": {"model": ["model", 0], "vae_conditioning": ["latent", 0]}},
            "sampler": {"class_type": "KSampler", "inputs": {"positive": ["conditioning", 0], "negative": ["conditioning", 1], "model": ["model", 0]}},
        }

        parsed = ib.parse_comfy_metadata({"prompt": graph})

        self.assertIsNone(parsed["prompt"])
        self.assertIsNone(parsed["negative_prompt"])
        self.assertEqual(parsed["model"], "seedvr2_7b_int8_convrot.safetensors")

    def test_sampler_custom_advanced_resolves_linked_helpers(self):
        graph = {
            "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": 821767337667846}},
            "guider": {"class_type": "CFGGuider", "inputs": {"cfg": 1.0}},
            "sampler_select": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}},
            "sigmas": {"class_type": "ManualSigmas", "inputs": {"sigmas": "1., 0.9, 0.7, 0.4, 0.0"}},
            "sampler": {"class_type": "SamplerCustomAdvanced", "inputs": {
                "noise": ["noise", 0],
                "guider": ["guider", 0],
                "sampler": ["sampler_select", 0],
                "sigmas": ["sigmas", 0],
            }},
        }

        parsed = ib.parse_comfy_metadata({"prompt": graph})

        self.assertEqual(parsed["seed"], 821767337667846)
        self.assertEqual(parsed["cfg"], 1.0)
        self.assertEqual(parsed["sampler"], "euler")
        self.assertEqual(parsed["steps"], 4)
        self.assertEqual(parsed["scheduler"], "manual sigmas")


if __name__ == "__main__":
    unittest.main()
