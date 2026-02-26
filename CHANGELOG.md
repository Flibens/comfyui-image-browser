# Changelog

All notable changes to this project will be documented in this file.

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
