# ComfyUI Image Browser

## Description :mag:
A custom ComfyUI image browser with search, favorites, metadata inspection, and media support. Built to make browsing outputs fast and visual, with a full-screen viewer and compare mode.

## Screenshots :camera:
![Image Browser Gallery](screenshots/image-browser-gallery.png?v=1.7.0-studio)
![Image Viewer + Metadata](screenshots/image-browser-viewer.png?v=1.7.0-studio)

## Features :sparkles:
- Studio-style media workspace with fixed library navigation and tools
- Graphite and Nier Automata themes
- Gallery view for images, videos, and audio files
- Search, sorting, favorites, multi-select, and A/B compare mode
- Fullscreen viewer with keyboard navigation and a boundary-aware filmstrip
- Lightweight Details and searchable, expandable Nodes metadata inspector
- Folder manager with external folders and confirmation-backed right-click removal
- Video/audio indicators with inline playback

## Changelog :memo:
- See `CHANGELOG.md` for release history.

## Download :arrow_down:
Option 1 (ZIP):
1. Click **Code ? Download ZIP** on GitHub.
2. Extract the ZIP.

Option 2 (git):
1. `git clone https://github.com/Flibens/comfyui-image-browser`

## Install :wrench:
1. Copy this folder into your ComfyUI `custom_nodes` directory.
2. Restart ComfyUI.

## Use :arrow_forward:
In ComfyUI, click the **Image Browser** button in the top menu bar.

## Notes:
Metadata parsing supports ComfyUI core SaveImage, LoRA Manager Save Image, and other save nodes that preserve ComfyUI `prompt`/`workflow` metadata, including workflow nodes stored inside subgraphs.
Favorites and added folders are stored in ComfyUI user data (`user/comfyui-image-browser`) so they survive node updates.


[![Buy Me A Coffee](https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=%E2%98%95&slug=flibens&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff)](https://www.buymeacoffee.com/flibens)


