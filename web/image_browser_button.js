import { app } from "/scripts/app.js";
import { ComfyButtonGroup } from "/scripts/ui/components/buttonGroup.js";
import { ComfyButton } from "/scripts/ui/components/button.js";

const BUTTON_GROUP_CLASS = "gemini-image-browser-top-menu-group";
const BUTTON_TOOLTIP = "Open Gemini Image Browser (Ctrl+B)";
const IMAGE_BROWSER_PATH = "/gemini-image-browser/ui";
const STORAGE_KEY_MODE = "geminiImageBrowserOpenMode";
const STORAGE_KEY_RECT = "geminiMiniWindowRect";

let miniWindowContainer = null;

function getOpenMode() {
    return localStorage.getItem(STORAGE_KEY_MODE) || "tab";
}

function getSavedRect() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY_RECT);
        if (raw) return JSON.parse(raw);
    } catch (_) { /* ignore */ }
    return null;
}

function saveRect(rect) {
    localStorage.setItem(STORAGE_KEY_RECT, JSON.stringify(rect));
}

function closeMiniWindow() {
    if (miniWindowContainer) {
        // Save position and size before removing
        const frame = miniWindowContainer.querySelector(".gemini-mini-frame");
        if (frame) {
            saveRect({
                left: frame.offsetLeft,
                top: frame.offsetTop,
                width: frame.offsetWidth,
                height: frame.offsetHeight,
            });
        }
        miniWindowContainer.remove();
        miniWindowContainer = null;
    }
}

function getCurrentRect() {
    if (!miniWindowContainer) return null;
    const frame = miniWindowContainer.querySelector(".gemini-mini-frame");
    if (!frame) return null;
    return {
        left: frame.offsetLeft,
        top: frame.offsetTop,
        width: frame.offsetWidth,
        height: frame.offsetHeight,
    };
}

// Save rect on page close/refresh so resizes are never lost
window.addEventListener("beforeunload", () => {
    const rect = getCurrentRect();
    if (rect) saveRect(rect);
});

function openMiniWindow() {
    if (miniWindowContainer) {
        // Already open — bring to front
        miniWindowContainer.style.zIndex = "9999";
        return;
    }

    const saved = getSavedRect();
    const defaultW = 520;
    const defaultH = 620;
    const left = saved?.left ?? Math.round((window.innerWidth - defaultW) / 2);
    const top = saved?.top ?? Math.round((window.innerHeight - defaultH) / 2);
    const width = saved?.width ?? defaultW;
    const height = saved?.height ?? defaultH;

    // Overlay container (captures nothing, just positions the window)
    miniWindowContainer = document.createElement("div");
    miniWindowContainer.className = "gemini-mini-overlay";
    miniWindowContainer.style.cssText = `
        position: fixed; inset: 0; z-index: 9999;
        pointer-events: none;
    `;

    // The floating frame
    const frame = document.createElement("div");
    frame.className = "gemini-mini-frame";
    frame.style.cssText = `
        position: absolute;
        left: ${left}px; top: ${top}px;
        width: ${width}px; height: ${height}px;
        min-width: 520px; min-height: 520px;
        display: flex; flex-direction: column;
        background: #1a1a1a;
        border: 1px solid #3a3a3a;
        border-radius: 12px;
        box-shadow: 0 20px 60px rgba(0,0,0,0.7), 0 0 0 1px rgba(255,255,255,0.05);
        overflow: hidden;
        pointer-events: auto;
        resize: both;
    `;

    // Title bar
    const titleBar = document.createElement("div");
    titleBar.className = "gemini-mini-titlebar";
    titleBar.style.cssText = `
        display: flex; align-items: center; justify-content: space-between;
        padding: 0 12px; height: 38px; min-height: 38px;
        background: linear-gradient(180deg, #2e2e2e 0%, #252525 100%);
        border-bottom: 1px solid #3a3a3a;
        cursor: grab; user-select: none;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
        font-size: 12px; color: #aaa; letter-spacing: 0.03em;
    `;

    const titleText = document.createElement("span");
    titleText.textContent = "Image Browser";
    titleText.style.cssText = "font-weight: 600; color: #ccc;";

    const closeBtn = document.createElement("button");
    closeBtn.textContent = "✕";
    closeBtn.style.cssText = `
        background: none; border: none; color: #888; cursor: pointer;
        font-size: 14px; width: 26px; height: 26px; border-radius: 6px;
        display: flex; align-items: center; justify-content: center;
        transition: background 0.15s, color 0.15s;
    `;
    closeBtn.addEventListener("mouseenter", () => { closeBtn.style.background = "#dc3545"; closeBtn.style.color = "#fff"; });
    closeBtn.addEventListener("mouseleave", () => { closeBtn.style.background = "none"; closeBtn.style.color = "#888"; });
    closeBtn.addEventListener("click", closeMiniWindow);

    titleBar.appendChild(titleText);
    titleBar.appendChild(closeBtn);

    // Iframe
    const iframe = document.createElement("iframe");
    iframe.src = IMAGE_BROWSER_PATH + "?mode=mini";
    iframe.style.cssText = `
        flex: 1; width: 100%; border: none;
        background: #1a1a1a;
    `;

    frame.appendChild(titleBar);
    frame.appendChild(iframe);
    miniWindowContainer.appendChild(frame);
    document.body.appendChild(miniWindowContainer);

    // --- Dragging ---
    let isDragging = false;
    let dragStartX = 0, dragStartY = 0;
    let frameStartX = 0, frameStartY = 0;

    titleBar.addEventListener("mousedown", (e) => {
        if (e.target === closeBtn) return;
        isDragging = true;
        dragStartX = e.clientX;
        dragStartY = e.clientY;
        frameStartX = frame.offsetLeft;
        frameStartY = frame.offsetTop;
        titleBar.style.cursor = "grabbing";
        // Prevent iframe from capturing mouse events during drag
        iframe.style.pointerEvents = "none";
        e.preventDefault();
    });

    document.addEventListener("mousemove", (e) => {
        if (!isDragging) return;
        const dx = e.clientX - dragStartX;
        const dy = e.clientY - dragStartY;
        frame.style.left = (frameStartX + dx) + "px";
        frame.style.top = (frameStartY + dy) + "px";
    });

    document.addEventListener("mouseup", () => {
        if (isDragging) {
            isDragging = false;
            titleBar.style.cursor = "grab";
            iframe.style.pointerEvents = "";
            // Save position
            saveRect({
                left: frame.offsetLeft,
                top: frame.offsetTop,
                width: frame.offsetWidth,
                height: frame.offsetHeight,
            });
        }
    });

    // Save size on resize (debounced for reliability)
    let resizeTimer = null;
    const resizeObserver = new ResizeObserver(() => {
        if (!isDragging) {
            clearTimeout(resizeTimer);
            resizeTimer = setTimeout(() => {
                saveRect({
                    left: frame.offsetLeft,
                    top: frame.offsetTop,
                    width: frame.offsetWidth,
                    height: frame.offsetHeight,
                });
            }, 200);
        }
    });
    resizeObserver.observe(frame);
}

// Listen for messages from the iframe
window.addEventListener("message", (e) => {
    if (e.data?.type === "gemini-browser-switch-to-tab") {
        closeMiniWindow();
        localStorage.setItem(STORAGE_KEY_MODE, "tab");
        window.open(IMAGE_BROWSER_PATH, "_blank");
    }
});

const openImageBrowser = () => {
    const mode = getOpenMode();
    if (mode === "mini") {
        if (miniWindowContainer) {
            closeMiniWindow();
        } else {
            openMiniWindow();
        }
    } else {
        window.open(IMAGE_BROWSER_PATH, "_blank");
    }
};

const createTopMenuButton = () => {
    const button = new ComfyButton({
        icon: "gemini-imagebrowser",
        tooltip: BUTTON_TOOLTIP,
        app,
        enabled: true,
        classList: "comfyui-button comfyui-menu-mobile-collapse primary",
    });

    button.element.setAttribute("aria-label", BUTTON_TOOLTIP);
    button.element.title = BUTTON_TOOLTIP;

    if (button.iconElement) {
        button.iconElement.textContent = "🖼️";
        button.iconElement.style.fontSize = "1.2rem";
    }

    button.element.addEventListener("click", openImageBrowser);
    return button;
};

const attachTopMenuButton = (attempt = 0) => {
    if (document.querySelector(`.${BUTTON_GROUP_CLASS}`)) return;

    const MAX_ATTACH_ATTEMPTS = 120;
    const settingsGroup = app.menu?.settingsGroup;
    if (!settingsGroup?.element?.parentElement) {
        if (attempt >= MAX_ATTACH_ATTEMPTS) {
            console.warn("Gemini Image Browser: settingsGroup not found");
            return;
        }
        requestAnimationFrame(() => attachTopMenuButton(attempt + 1));
        return;
    }

    const button = createTopMenuButton();
    const group = new ComfyButtonGroup(button);
    group.element.classList.add(BUTTON_GROUP_CLASS);

    settingsGroup.element.before(group.element);
    console.log("Gemini Image Browser: Button added to menu.");
};


app.registerExtension({
    name: "Gemini.ImageBrowser.Button",
    setup() {
        console.log("Gemini Image Browser: Setting up menu button.");
        attachTopMenuButton();

        document.addEventListener("keydown", (e) => {
            if (e.ctrlKey && e.key === "b") {
                e.preventDefault();
                openImageBrowser();
            }
        });
    },
});
