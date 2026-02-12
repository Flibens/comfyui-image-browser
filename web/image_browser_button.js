import { app } from "/scripts/app.js";
import { ComfyButtonGroup } from "/scripts/ui/components/buttonGroup.js";
import { ComfyButton } from "/scripts/ui/components/button.js";

const BUTTON_GROUP_CLASS = "gemini-image-browser-top-menu-group";
const BUTTON_TOOLTIP = "Open Gemini Image Browser (Ctrl+B)";
const IMAGE_BROWSER_PATH = "/gemini-image-browser/ui";

const openImageBrowser = () => {
    window.open(IMAGE_BROWSER_PATH, '_blank');
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
