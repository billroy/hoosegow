"""Regression checks for multiple Live Agent tabs."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_live_agent_tabs_are_dynamic_in_app_shell():
    text = _read("static/app.js")
    assert "const chatTabs = reactive([]);" in text
    assert "const lastLiveAgentTabByWorkspace = reactive({});" in text
    assert "function addLiveAgentTab({ activate = true } = {})" in text
    assert "function closeLiveAgentTab(tabId)" in text
    assert "function setActiveTab(tabId)" in text
    assert "class=\"tab-btn tab-btn-add\"" in text
    assert "v-for=\"ct in chatTabs\"" in text
    assert ":session-id=\"ct.sessionId\"" in text


def test_live_agent_seed_does_not_override_default_tickets_tab():
    text = _read("static/app.js")
    assert "const activeTab = ref('tasks');" in text
    assert "addLiveAgentTab({ activate: false });" in text


def test_live_agent_component_uses_injected_session_id():
    text = _read("static/components/LiveAgentChatTab.js")
    assert "props:" in text
    assert "sessionId" in text
    assert "activeSessionId: this.sessionId || _generateChatSessionId()" in text
    assert "data.sessionId !== this.activeSessionId" in text
    assert "sessionId: this.activeSessionId" in text


def test_live_agent_provider_options_include_gemini():
    text = _read("static/components/LiveAgentChatTab.js")
    assert "['claude', 'codex', 'gemini']" in text


def test_live_agent_component_syncs_user_messages_between_windows():
    text = _read("static/components/LiveAgentChatTab.js")
    assert "s.on('chat:user', this._onUser);" in text
    assert "if (data.senderSid && s.id && data.senderSid === s.id) return;" in text


def test_live_agent_project_switch_preserves_live_agent_mode():
    text = _read("static/app.js")
    assert "const wasLiveAgent = !!currentChatTab;" in text
    assert "const ensuredChatTab = _ensureChatTabForWorkspace(wsId);" in text
    assert "const preferred = chatTabs.find(t => t.id === lastLiveAgentTabByWorkspace[wsId] && t.workspaceId === wsId);" in text
    assert "const fallback = preferred || ensuredChatTab || chatTabs.find(t => t.workspaceId === wsId);" in text
    assert "setActiveTab(fallback.id);" in text


def test_live_agent_tabs_remember_last_active_per_workspace():
    text = _read("static/app.js")
    assert "lastLiveAgentTabByWorkspace[tab.workspaceId] = tab.id;" in text
    assert "lastLiveAgentTabByWorkspace[currentChatTab.workspaceId] = currentChatTab.id;" in text
    assert "@click=\"setActiveTab(tab.id)\"" in text
    assert "connected, activeTab, setActiveTab," in text
    assert "if (lastLiveAgentTabByWorkspace[wsId] === tabId)" in text
