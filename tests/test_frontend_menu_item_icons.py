"""Regression checks for menu command icon rendering."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_worker_card_menu_commands_render_icons_before_labels():
    text = _read("static/components/WorkerCard.js")
    assert "class=\"worker-menu-item\" @click=\"menuEdit\"><i class=\"menu-item-icon\" data-lucide=\"pencil\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuRun\"><i class=\"menu-item-icon\" data-lucide=\"play\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuWatch\"><i class=\"menu-item-icon\" data-lucide=\"eye\"" in text
    assert "class=\"worker-menu-item\" :disabled=\"!serviceSiteUrl\" @click=\"menuOpenSite\"><i class=\"menu-item-icon\" data-lucide=\"external-link\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuStop\"><i class=\"menu-item-icon\" data-lucide=\"square\"" in text
    assert "class=\"worker-menu-item\" :disabled=\"multipleSelectionActive\" @click=\"menuPause\"><i class=\"menu-item-icon\" data-lucide=\"pause\"" in text
    assert "class=\"worker-menu-item\" :disabled=\"multipleSelectionActive\" @click=\"menuUnpause\"><i class=\"menu-item-icon\" data-lucide=\"play\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuDuplicate\"><i class=\"menu-item-icon\" data-lucide=\"copy\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuCopyWorker\"><i class=\"menu-item-icon\" data-lucide=\"clipboard\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuExportWorker\"><i class=\"menu-item-icon\" data-lucide=\"download\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuCopyTo\"><i class=\"menu-item-icon\" data-lucide=\"copy\"" in text
    assert "class=\"worker-menu-item\" @click=\"menuMoveTo\"><i class=\"menu-item-icon\" data-lucide=\"arrow-right\"" in text
    assert "class=\"worker-menu-item worker-menu-danger\" :disabled=\"multipleSelectionActive\" @click=\"menuDelete\"><i class=\"menu-item-icon\" data-lucide=\"trash-2\"" in text
    assert "<span class=\"menu-item-label\">Open site in browser</span></button>" in text
    assert "<span class=\"menu-item-label\">Move to workspace&hellip;</span></button>" in text


def test_worker_card_menu_is_teleported_and_uses_root_menu_ref():
    text = _read("static/components/WorkerCard.js")
    assert "<Teleport to=\"body\">" in text
    assert "ref=\"menu\" class=\"worker-menu\"" in text
    assert "menuItems()" in text
    assert "const [first] = this.menuItems();" in text
    assert "const items = this.menuItems();" in text
    assert "const clickedInsideMenu = menu && typeof menu.contains === 'function' && menu.contains(e.target);" in text


def test_empty_slot_menu_commands_render_icons_before_labels():
    text = _read("static/components/BullpenTab.js")
    assert "class=\"worker-menu-item\" @click=\"openLibraryForCoord(ghostCell)\"><i class=\"menu-item-icon\" data-lucide=\"user-plus\"" in text
    assert "class=\"worker-menu-item\" :disabled=\"!canPasteAt(ghostCell)\" @click=\"pasteWorkerFromMenu(ghostCell)\"><i class=\"menu-item-icon\" data-lucide=\"clipboard\"" in text
    assert "<span class=\"menu-item-label\">Add Worker</span></button>" in text
    assert "<span class=\"menu-item-label\">Paste Worker</span></button>" in text


def test_bullpen_tab_hydrates_lucide_icons_for_dynamic_menu_content():
    text = _read("static/components/BullpenTab.js")
    assert "mounted() {" in text
    assert "updated() {" in text
    assert "renderLucideIcons(this.$el);" in text


def test_worker_menu_item_styles_support_icon_and_label_layout():
    text = _read("static/style.css")
    assert ".worker-menu-item {" in text
    assert "display: flex;" in text
    assert "align-items: center;" in text
    assert "gap: 6px;" in text


def test_worker_menu_uses_high_overlay_z_index():
    text = _read("static/style.css")
    assert ".worker-menu {" in text
    assert "z-index: 1000;" in text
