"""Regression checks for the project Stats tab."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_stats_tab_component_is_loaded_before_app():
    text = _read("static/index.html")
    assert '<script src="/components/StatsTab.js"></script>' in text
    assert text.index('/components/StatsTab.js') < text.index('/app.js')


def test_stats_tab_is_after_commits():
    text = _read("static/app.js")
    files = "{ id: 'files', label: 'Files', icon: 'folder' }"
    stats = "{ id: 'stats', label: 'Stats', icon: 'chart-no-axes-column' }"
    commits = "{ id: 'commits', label: 'Commits', icon: 'git-commit' }"
    assert files in text
    assert stats in text
    assert commits in text
    assert text.index(files) < text.index(commits) < text.index(stats)


def test_stats_tab_uses_archived_ticket_cache_and_refreshes_archive_scope():
    app = _read("static/app.js")
    component = _read("static/components/StatsTab.js")
    assert "StatsTab," in app
    assert 'v-if="activeWorkspaceId && activeTab === \'stats\'"' in app
    assert ':archived-tasks="workspaces[activeWorkspaceId]?.archivedTasks || []"' in app
    assert "if (tabId === 'stats' && activeWorkspaceId.value)" in app
    assert "socket.emit('task:list', _wsData({ scope: 'archived' }));" in app
    assert "props: ['tasks', 'archivedTasks', 'columns', 'layout', 'workspaceId']" in component
    assert "emits: ['select-task']" in component


def test_stats_tab_renders_required_dashboard_panes():
    text = _read("static/components/StatsTab.js")
    assert "Open tickets" in text
    assert "Archived tickets" in text
    assert "Daily archived tickets" in text
    assert "Daily open tickets" in text
    assert "Daily archived total ticket time" in text
    assert "Archived ticket tokens" in text
    assert "Recent Archive" in text
    assert "Current Load" in text


def test_stats_tab_trends_supports_period_selector_options():
    text = _read("static/components/StatsTab.js")
    assert "selectedPeriodDays: 14" in text
    assert "{ days: 1, label: '1d' }" in text
    assert "{ days: 7, label: '7d' }" in text
    assert "{ days: 14, label: '14d' }" in text
    assert "{ days: 30, label: '30d' }" in text
    assert "{ days: 90, label: '90d' }" in text
    assert 'aria-label="Trend period"' in text


def test_stats_tab_trends_include_date_axis_ticks():
    text = _read("static/components/StatsTab.js")
    assert "class=\"stats-sparkline-tick\"" in text
    assert "class=\"stats-spark-axis\"" in text
    assert "axisTicks()" in text
    assert "formatDayKey(dayKey)" in text


def test_stats_tab_archived_time_trend_uses_reported_task_time_formatting():
    text = _read("static/components/StatsTab.js")
    assert "taskTimeValue(task)" in text
    assert "getReportedTaskTimeMs" in text
    assert "totalType: 'duration'" in text
    assert "formatSparkTotal(chart)" in text
    assert "formatTaskDuration(chart.total)" in text
