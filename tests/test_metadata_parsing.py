import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path


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

    sys.modules["server"] = server_stub
    sys.modules["folder_paths"] = folder_paths_stub

    spec = importlib.util.spec_from_file_location("image_browser_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ib = load_image_browser_module()


class MetadataParsingTests(unittest.TestCase):
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
                                    "inputs": [{"name": "clip", "link": 10}],
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
        self.assertNotIn("2", node_ids)
        subgraph_node = next(node for node in nodes if node["id"] == "subgraph-abc:42")
        self.assertEqual(subgraph_node["type"], "CLIPTextEncode")
        self.assertTrue(any(param["name"] == "subgraph" and param["value"] == "Qwen Text-to-image (Subgraph)" for param in subgraph_node["params"]))


if __name__ == "__main__":
    unittest.main()
