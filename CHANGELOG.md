# Changelog

All notable changes to this project will be documented in this file.

## [1.5.0] - 2026-03-17
- Added a mini version of the browser inside ComfyUI.
- Added a new theme.

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
