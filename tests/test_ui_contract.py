import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "ui.html").read_text(encoding="utf-8")
JS = (ROOT / "browser.js").read_text(encoding="utf-8")


class UiContractTests(unittest.TestCase):
    def test_lightweight_inspector_has_details_and_nodes_only(self):
        self.assertIn(">Details</button>", JS)
        self.assertIn(">Nodes</button>", JS)
        self.assertNotIn(">Raw</button>", JS)
        self.assertNotIn(">Workflow</button>", JS)

    def test_nodes_are_native_searchable_disclosures(self):
        self.assertIn('id="workflowNodeSearch"', JS)
        self.assertIn('id="workflowNodeList"', JS)
        self.assertIn('<details class="workflow-node-item${mutedClass}">', JS)
        self.assertIn('<summary class="workflow-node-header">', JS)
        self.assertIn('.workflow-node-item[open]', HTML)

    def test_studio_library_is_sidebar_and_workspace_not_header_over_grid(self):
        self.assertIn('class="studio-shell"', HTML)
        self.assertIn('class="studio-sidebar"', HTML)
        self.assertIn('class="library-main"', HTML)
        self.assertIn('class="library-toolbar"', HTML)
        self.assertIn('id="sidebarCollapseBtn"', HTML)
        self.assertIn('id="currentLocation"', HTML)
        self.assertIn('id="thumbnailSize"', HTML)

    def test_graphite_and_nier_themes_are_available(self):
        self.assertIn('data-theme="graphite"', HTML)
        self.assertIn('id="themeSelect"', HTML)
        self.assertIn('Nier Automata', HTML)
        self.assertIn('body[data-theme="nier"]', HTML)
        self.assertNotIn('Verdant Forest', HTML)
        self.assertNotIn('body[data-theme="forest"]', HTML)

    def test_full_viewer_has_dedicated_toolbar_stage_and_filmstrip(self):
        self.assertIn('class="viewer-toolbar"', HTML)
        self.assertIn('id="viewerTitle"', HTML)
        self.assertIn('class="viewer-stage"', HTML)
        self.assertIn('id="modalFilmstrip"', HTML)
        self.assertIn('id="filmstripCount"', HTML)
        self.assertIn('renderModalFilmstrip()', JS)

    def test_filmstrip_keeps_a_full_window_at_collection_boundaries(self):
        self.assertIn('const windowSize = 17;', JS)
        self.assertIn('currentImages.length - windowSize', JS)
        self.assertNotIn('currentImageIndex + 9', JS)

    def test_folder_browser_and_collapsed_sidebar_are_operable(self):
        self.assertIn('.folder-dropdown.active', HTML)
        self.assertIn("folderDropdown.classList.toggle('active')", JS)
        self.assertIn("sidebarCollapseBtn.textContent = collapsed ? '›' : '‹'", JS)
        self.assertIn('overflow-x: hidden', HTML)

    def test_folder_disclosure_persists_and_delete_is_right_click_only(self):
        self.assertIn('showFolderContextMenu', JS)
        self.assertIn("item.addEventListener('contextmenu'", JS)
        self.assertNotIn('folder-item-delete', HTML)
        self.assertNotIn('folder-item-delete', JS)
        self.assertNotIn("!folderDropdown.contains(e.target) && !folderBtn.contains(e.target)", JS)

    def test_nier_selection_and_filmstrip_states_are_high_contrast(self):
        self.assertIn('body[data-theme="nier"] .image-card.compare-first::after', HTML)
        self.assertIn('body[data-theme="nier"] .image-card.compare-second::after', HTML)
        self.assertIn('body[data-theme="nier"] .filmstrip-thumb.active::after', HTML)
        self.assertIn('box-shadow: inset 0 0 0 2px #e4e1c8', HTML)

    def test_nier_open_compare_is_high_contrast(self):
        self.assertIn('body[data-theme="nier"] #compareBtn', HTML)
        self.assertIn('background: #3d3e38; color: #f0edd5;', HTML)

    def test_library_navigation_and_toolbar_are_fixed(self):
        self.assertIn('.studio-sidebar {\n            position: fixed;', HTML)
        self.assertIn('.library-toolbar {\n            position: fixed;', HTML)
        self.assertIn('left: var(--sidebar-width);', HTML)
        self.assertIn('.library-main { grid-column: 2;', HTML)
        self.assertIn('padding-top: 52px;', HTML)

    def test_multi_selection_is_highlighted(self):
        self.assertIn('.image-card.selected', HTML)
        self.assertIn('content: "✓"', HTML)
        self.assertIn('class="multi-select-actions"', HTML)


if __name__ == "__main__":
    unittest.main()
