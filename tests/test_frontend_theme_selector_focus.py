"""Regression checks for theme selector focus handoff to worker grid."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_theme_selector_emits_focus_handoff_hint():
    text = _read("static/components/TopToolbar.js")
    assert "@change=\"onThemeSelect\"" in text
    assert "this.$emit('set-theme', value, { focusWorkerGrid: true });" in text


def test_app_restores_focus_to_worker_grid_after_theme_change_from_toolbar():
    text = _read("static/app.js")
    assert "const bullpenTabRef = ref(null);" in text
    assert "function focusWorkerGridSoon() {" in text
    assert "if (activeTab.value === 'workers') {" in text
    assert "Vue.nextTick(() => bullpenTabRef.value?.focusViewport?.());" in text
    assert "function setTheme(themeId, options = {}) {" in text
    assert "if (options?.focusWorkerGrid) focusWorkerGridSoon();" in text
    assert "ref=\"bullpenTabRef\"" in text
