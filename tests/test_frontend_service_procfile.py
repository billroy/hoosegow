"""Regression checks for Procfile-backed Service worker UI."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


def test_worker_config_modal_exposes_service_procfile_controls():
    text = _read("static/components/WorkerConfigModal.js")
    assert "Command Source" in text
    assert 'option value="manual">Inline command' in text
    assert 'option value="procfile">Procfile' in text
    assert "Procfile process" in text
    assert "Resolved command preview" in text
    assert "fetch('/api/service/preview'" in text
    assert "Suggested open port:" in text
    assert "data.suggested_port" in text
    assert "servicePortAutoFilled" in text
    assert "activation: w.activation || (w.type === 'service' ? 'manual' : 'on_drop')" in text


def test_worker_config_modal_uses_assignment_policy_labels():
    text = _read("static/components/WorkerConfigModal.js")
    assert 'option value="on_drop">Auto on Assignment' in text
    assert 'option value="manual">Hold for Run' in text


def test_service_start_action_runs_queued_orders_through_worker_start():
    app = _read("static/app.js")
    card = _read("static/components/WorkerCard.js")

    assert "const hasQueuedOrder = worker?.type === 'service' && Array.isArray(worker.task_queue) && worker.task_queue.length > 0;" in app
    assert "worker?.type === 'service' && !hasQueuedOrder ? 'service:start' : 'worker:start'" in app
    assert "runMenuLabel()" in card
    assert "this.taskQueueCount > 0 ? 'Run queued order' : 'Start'" in card


def test_worker_card_hides_procfile_badge_and_conditionally_appends_port_to_title():
    text = _read("static/components/WorkerCard.js")
    assert "Procfile:${this.worker.procfile_process || 'web'}" not in text
    assert "titlePortCandidate()" in text
    assert "workerNameWithPort()" in text
    assert "recalculateTitlePortVisibility()" in text
    assert "serviceSiteUrl()" in text
    assert "menuOpenSite()" in text


def test_service_site_opening_uses_shared_url_helper_and_root_action():
    utils = _read("static/utils.js")
    app = _read("static/app.js")

    assert "function getServiceSiteUrl(worker, locationLike = window.location) {" in utils
    assert "url.protocol = 'http:';" in utils
    assert "url.port = String(port);" in utils
    assert "url.pathname = '/';" in utils
    assert "window.getServiceSiteUrl = getServiceSiteUrl;" in utils

    assert "function openServiceSite(slot) {" in app
    assert "window.getServiceSiteUrl(worker, window.location)" in app
    assert "const link = document.createElement('a');" in app
    assert "link.target = '_blank';" in app
    assert "link.rel = 'noopener noreferrer';" in app
    assert "link.click();" in app
    assert "window.open(url, '_blank', 'noopener,noreferrer')" not in app
    assert "Browser blocked opening the service site" not in app
    assert "Service site is unavailable until this worker has a valid port" in app


def test_add_service_worker_defaults_to_manual_activation():
    text = _read("static/components/BullpenTab.js")
    assert "name: 'Service worker'" in text
    assert "activation: 'manual'" in text
