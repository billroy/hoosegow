window.BULLPEN_TASK_DND_MIME = 'application/x-bullpen-task-id';
window.BULLPEN_TASK_DRAG_ACTIVE = false;

const MODEL_OPTIONS = {
  claude: [
    'claude-opus-4-7',
    'claude-opus-4-6', 'claude-opus-4-5-20250514',
    'claude-sonnet-4-6', 'claude-sonnet-4-5-20250514',
    'claude-haiku-4-5-20251001',
  ],
  codex: ['gpt-5.5', 'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.3-codex', 'gpt-5.2'],
  gemini: ['flash', 'flash-lite', 'gemini-3-flash-preview'],
};

const DEFAULT_AGENT_COLORS = { claude: '#da7756', codex: '#5b6fd6', gemini: '#3c7bf4', shell: '#64748b', service: '#0f766e', marker: '#c8b38c' };
window.DEFAULT_AGENT_COLORS = DEFAULT_AGENT_COLORS;
window.BULLPEN_AGENT_COLORS = (window.Vue && window.Vue.reactive)
  ? window.Vue.reactive({ overrides: {} })
  : { overrides: {} };
const HEX_COLOR_RE = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

function agentColor(agent) {
  const overrides = window.BULLPEN_AGENT_COLORS.overrides || {};
  return overrides[agent] || DEFAULT_AGENT_COLORS[agent] || '#6B7280';
}

function workerColorKey(worker) {
  if (worker?.type === 'shell') return 'shell';
  if (worker?.type === 'service') return 'service';
  if (worker?.type === 'marker') return 'marker';
  return worker?.agent;
}

function workerColor(worker) {
  const configured = typeof worker?.color === 'string' ? worker.color.trim() : '';
  if (configured && HEX_COLOR_RE.test(configured)) return configured.toLowerCase();
  return agentColor(configured || workerColorKey(worker));
}

function isHumanWorker(worker) {
  return worker?.is_human === true || worker?.type === 'human' || worker?.agent === 'human';
}

const BUILTIN_WORKER_TYPES = new Set(['ai', 'shell', 'service', 'marker', 'eval', 'human']);

function isShellWorker(worker) {
  return worker?.type === 'shell';
}

function isServiceWorker(worker) {
  return worker?.type === 'service';
}

function isMarkerWorker(worker) {
  return worker?.type === 'marker';
}

function getServiceSiteUrl(worker, locationLike = window.location) {
  if (!isServiceWorker(worker)) return '';
  const port = Number(worker?.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return '';
  const fallbackHost = String(locationLike?.hostname || '').trim() || '127.0.0.1';
  let url;
  try {
    url = new URL(String(locationLike?.href || ''));
  } catch (_err) {
    url = new URL(`http://${fallbackHost}`);
  }
  url.protocol = 'http:';
  url.port = String(port);
  url.pathname = '/';
  url.search = '';
  url.hash = '';
  return url.toString();
}

function isEvalWorker(worker) {
  return worker?.type === 'eval';
}

function isUnknownWorkerType(worker) {
  const type = worker?.type;
  if (!type) return false;
  return !BUILTIN_WORKER_TYPES.has(type);
}

function getWorkerTypeIcon(worker) {
  if (isHumanWorker(worker)) return 'user';
  if (isShellWorker(worker)) return 'terminal';
  if (isServiceWorker(worker)) return 'server-cog';
  if (isMarkerWorker(worker)) return 'square-dot';
  if (isEvalWorker(worker)) return 'flask-conical';
  if (isUnknownWorkerType(worker)) return 'circle-help';
  return 'bot';
}

function workerTypeLabel(worker) {
  if (isShellWorker(worker)) return 'Shell';
  if (isServiceWorker(worker)) return 'Service';
  if (isMarkerWorker(worker)) return 'Marker';
  if (isEvalWorker(worker)) return 'Eval';
  if (isHumanWorker(worker)) return 'Human';
  if (isUnknownWorkerType(worker)) return worker.type;
  return 'AI';
}

function getColumnIcon(col) {
  const workerColumns = new Set(['assigned', 'in_progress']);
  return col?.icon || (workerColumns.has(col?.key) ? 'bot' : 'user');
}

function renderLucideIcons(rootEl) {
  if (!window.lucide?.createIcons) return;
  const root = rootEl?.querySelectorAll ? rootEl : document;
  window.lucide.createIcons({ attrs: { 'stroke-width': 2 }, root });
}

window.getServiceSiteUrl = getServiceSiteUrl;
window.renderLucideIcons = renderLucideIcons;
window.getWorkerTypeIcon = getWorkerTypeIcon;
window.workerTypeLabel = workerTypeLabel;
window.workerColor = workerColor;
window.isHumanWorker = isHumanWorker;
window.getColumnIcon = getColumnIcon;

function formatTaskDuration(ms) {
  const totalMs = Number(ms);
  if (!Number.isFinite(totalMs) || totalMs <= 0) return '0s';
  const totalSeconds = Math.floor(totalMs / 1000);
  const seconds = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const minutes = totalMinutes % 60;
  const hours = Math.floor(totalMinutes / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

function getReportedTaskTimeMs(task) {
  const reported = Number(task?.reported_task_time_ms);
  if (Number.isFinite(reported) && reported > 0) return reported;
  const persisted = Number(task?.task_time_ms);
  if (Number.isFinite(persisted) && persisted > 0) return persisted;
  return 0;
}

window.formatTaskDuration = formatTaskDuration;
window.getReportedTaskTimeMs = getReportedTaskTimeMs;
