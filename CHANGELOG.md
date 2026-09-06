# Changelog

All notable changes to this project will be documented in this file.

## [1.7.1] - 2026-09-06
- Report only LoRAs corroborated as active by execution graphs, LoRA Manager/Stacker data, or applied LoRA hashes, excluding disabled, bypassed, disconnected, and unselected branches.
- Preserve distinct folder-qualified LoRAs that share a filename while deduplicating equivalent path/extension spellings.
- Hide noisy exporter-only `Model hash`, `Clip skip`, and `Version` fields from generation details.
- Share favorites and custom folders safely with LumaVault using atomic, cross-process updates that preserve intentional clears and recover from malformed shared state without erasing the untouched collection.
- Added regression coverage for active model paths, numbered switches, disconnected guiders, collection concurrency, write failures, and malformed shared state.

## [1.7.0] - 2026-08-31
- Rebuilt the browser as a Studio-style media workspace with fixed navigation, fixed library tools, a dense gallery, a dedicated viewer, and a persistent Details/Nodes inspector.
- Added Graphite and Nier Automata visual themes, including high-contrast Nier selection, A/B compare, active-filmstrip, and Open Compare states.
- Expanded metadata extraction for saved ComfyUI image, video, and audio workflows; the lightweight inspector now exposes searchable, expandable workflow nodes alongside Details.
- Improved viewer filmstrip boundaries so the first and final media items retain a full thumbnail window.
- Improved folder browsing, sidebar collapse/expand behavior, and custom-folder removal through a confirmation-backed right-click menu.
- Preserved existing browser features including favorites, search, sorting, compare mode, multi-select, external folders, and Quick Look mode.

## [1.6.0] - 2026-07-09
- Added broad metadata parsing support for ComfyUI core SaveImage and other save nodes that preserve `prompt`/`workflow` metadata.
- Resolved linked API prompt graph values for prompts, sampler settings, dimensions, models, and LoRAs.
- Added workflow node inspector support for nodes stored inside ComfyUI subgraph definitions.
- Fixed duplicate/phantom negative prompt display when workflows have no real negative prompt.
- Added regression tests for default SaveImage metadata and subgraph workflow nodes.

## [1.5.0] - 2026-03-17
- Added a mini version of the browser inside ComfyUI.
- Added a new theme.

## [1.4.5] - 2026-03-03
- Fixed some cosmetic issues.

## [1.4.4] - 2026-03-03
- Fixed minor issues.
- Updated runtime log labels from `Gemini Image Browser` to `ComfyUI Image Browser`.

## [1.4.3] - 2026-03-03
- Fixed minor issues.
- New installs now use `user/comfyui-image-browser` for favorites and folders.

## [1.4.1] - 2026-03-03
- Persist favorites and additional folders in ComfyUI user data (`user/gemini-image-browser`) so updates do not overwrite user state.
- Added automatic one-time migration from legacy in-node `favorites.json` and `folders.json`.

## [1.4.0] - 2026-03-03
- Added compare zoom controls and shortcuts (`+`, `-`, `0`), including mouse-wheel zoom.
- Added compare drag/pan while zoomed for easier inspection.
- Added `Multi-Select Mode` button (no Ctrl required) and kept Ctrl/Cmd multi-select support.
- Ctrl/Cmd multi-select now auto-enables Multi-Select Mode.
- Toggling Multi-Select Mode off now clears selections; `Clear` also exits multi-select mode.

## [1.3.1] - 2026-02-26
- Security fix: sanitize endpoint file paths to prevent path traversal / arbitrary file access.
- Hardened: metadata, thumbnail, and delete endpoints now enforce bounded path resolution.
- Hardened: add_folder now validates canonical paths against allowed ComfyUI roots.

## [1.3.0] - 2026-02-16
- Fixed prompt truncation on sdxl

## [1.2.0] - 2026-02-16
- Fixed delete bug

## [1.1.0] - 2026-02-15
- Minor refinements
- Added zoom in-out in images
- Improved metadata extraction

## [1.0.0] - 2026-02-12
- Initial release
