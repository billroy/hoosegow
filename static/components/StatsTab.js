const StatsTab = {
  props: ['tasks', 'archivedTasks', 'columns', 'layout', 'workspaceId'],
  emits: ['select-task'],
  data() {
    return {
      selectedPeriodDays: 14,
    };
  },
  template: `
    <div class="stats-tab">
      <div class="stats-summary-grid">
        <section v-for="metric in summaryMetrics" :key="metric.key" class="stats-metric">
          <div class="stats-metric-value">{{ metric.value }}</div>
          <div class="stats-metric-label">{{ metric.label }}</div>
          <div class="stats-metric-hint">{{ metric.hint }}</div>
        </section>
      </div>

      <div class="stats-dashboard-grid">
        <section class="stats-pane stats-status-pane">
          <div class="stats-pane-header">
            <div>
              <h2>Ticket Status</h2>
              <p>Open work by category</p>
            </div>
            <i data-lucide="list-filter" aria-hidden="true"></i>
          </div>

          <div class="stats-section">
            <h3>Open by status</h3>
            <div v-if="statusRows.length === 0" class="stats-empty">No open tickets</div>
            <div v-for="row in statusRows" :key="row.key" class="stats-row">
              <div class="stats-row-top">
                <span class="stats-row-label">
                  <span class="stats-row-swatch" :style="{ backgroundColor: row.color }"></span>
                  {{ row.label }}
                </span>
                <span class="stats-row-count">{{ row.count }}</span>
              </div>
              <div class="stats-row-track">
                <span class="stats-row-bar" :style="{ width: row.percent + '%', backgroundColor: row.color }"></span>
              </div>
            </div>
          </div>

          <div class="stats-section stats-section-split">
            <div>
              <h3>Open by type</h3>
              <div v-for="row in typeRows" :key="row.key" class="stats-compact-row">
                <span>{{ row.label }}</span>
                <strong>{{ row.count }}</strong>
              </div>
            </div>
            <div>
              <h3>Open by priority</h3>
              <div v-for="row in priorityRows" :key="row.key" class="stats-compact-row">
                <span>{{ row.label }}</span>
                <strong>{{ row.count }}</strong>
              </div>
            </div>
          </div>

          <div class="stats-section stats-archive-note">
            <div>
              <span class="stats-note-label">Archive</span>
              <strong>{{ archivedCountLabel }}</strong>
            </div>
            <div>
              <span class="stats-note-label">Archived tokens</span>
              <strong>{{ formatTokens(archivedTokenTotal) }}</strong>
            </div>
          </div>
        </section>

        <section class="stats-pane stats-trends-pane">
          <div class="stats-pane-header">
            <div>
              <h2>Trends</h2>
              <p>{{ trendWindowLabel }}</p>
            </div>
            <div class="stats-pane-header-actions">
              <div class="stats-period-selector" role="group" aria-label="Trend period">
                <button
                  v-for="option in periodOptions"
                  :key="option.days"
                  type="button"
                  class="stats-period-button"
                  :class="{ 'is-active': option.days === selectedPeriodDays }"
                  @click="selectedPeriodDays = option.days"
                >
                  {{ option.label }}
                </button>
              </div>
              <i data-lucide="activity" aria-hidden="true"></i>
            </div>
          </div>

          <div class="stats-spark-grid">
            <article v-for="chart in sparklineCharts" :key="chart.key" class="stats-spark-card">
              <div class="stats-spark-header">
                <span>{{ chart.label }}</span>
                <strong>{{ formatSparkTotal(chart) }}</strong>
              </div>
              <svg class="stats-sparkline" viewBox="0 0 160 44" preserveAspectRatio="none" aria-hidden="true">
                <line
                  v-for="tick in chart.ticks"
                  :key="tick.key"
                  :x1="tick.x"
                  y1="3"
                  :x2="tick.x"
                  y2="39"
                  class="stats-sparkline-tick"
                ></line>
                <polyline
                  v-if="chart.points"
                  :points="chart.points"
                  fill="none"
                  :stroke="chart.color"
                  stroke-width="2.5"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                ></polyline>
                <line x1="0" y1="39" x2="160" y2="39" class="stats-sparkline-base"></line>
              </svg>
              <div class="stats-spark-axis" aria-hidden="true">
                <span
                  v-for="tick in chart.ticks"
                  :key="tick.key"
                  class="stats-spark-axis-label"
                  :style="{ left: tick.percent + '%' }"
                >
                  {{ tick.label }}
                </span>
              </div>
              <div class="stats-spark-caption">{{ chart.caption }}</div>
            </article>
          </div>
        </section>

        <section class="stats-pane stats-recent-pane">
          <div class="stats-pane-header">
            <div>
              <h2>Recent Archive</h2>
              <p>{{ archiveDateLabel }}</p>
            </div>
            <i data-lucide="archive" aria-hidden="true"></i>
          </div>

          <div v-if="recentArchived.length === 0" class="stats-empty stats-empty-large">No archived tickets yet</div>
          <button
            v-for="task in recentArchived"
            :key="task.id"
            type="button"
            class="stats-recent-row"
            @click="$emit('select-task', { id: task.id, readOnly: true })"
          >
            <span class="stats-recent-main">
              <span class="stats-recent-title">{{ task.title || task.id }}</span>
              <span class="stats-recent-meta">{{ task.type || 'task' }} - {{ formatDate(bestArchiveDate(task)) }}</span>
            </span>
            <span class="stats-recent-tokens">{{ task.tokens ? formatTokens(task.tokens) : '-' }}</span>
          </button>
        </section>

        <section class="stats-pane stats-extra-pane">
          <div class="stats-pane-header">
            <div>
              <h2>Current Load</h2>
              <p>Worker queues and ticket age</p>
            </div>
            <i data-lucide="gauge" aria-hidden="true"></i>
          </div>

          <div class="stats-load-grid">
            <div class="stats-load-item">
              <span>Queued tickets</span>
              <strong>{{ queuedTicketCount }}</strong>
            </div>
            <div class="stats-load-item">
              <span>Assigned workers</span>
              <strong>{{ workersWithQueues }}</strong>
            </div>
            <div class="stats-load-item">
              <span>Oldest open</span>
              <strong>{{ oldestOpenLabel }}</strong>
            </div>
            <div class="stats-load-item">
              <span>Token hotspot</span>
              <strong>{{ tokenHotspotLabel }}</strong>
            </div>
          </div>
        </section>
      </div>
    </div>
  `,
  mounted() {
    renderLucideIcons(this.$el);
  },
  computed: {
    liveTasks() {
      return Array.isArray(this.tasks) ? this.tasks : [];
    },
    archiveTasks() {
      return Array.isArray(this.archivedTasks) ? this.archivedTasks : [];
    },
    openTicketCount() {
      return this.liveTasks.length;
    },
    archivedTicketCount() {
      return this.archiveTasks.length;
    },
    doneWaitingCount() {
      return this.liveTasks.filter(task => task?.status === 'done').length;
    },
    liveTokenTotal() {
      return this.sumTokens(this.liveTasks);
    },
    archivedTokenTotal() {
      return this.sumTokens(this.archiveTasks);
    },
    tokenTotal() {
      return this.liveTokenTotal + this.archivedTokenTotal;
    },
    summaryMetrics() {
      return [
        { key: 'open', label: 'Open tickets', value: this.formatNumber(this.openTicketCount), hint: 'live' },
        { key: 'archived', label: 'Archived tickets', value: this.formatNumber(this.archivedTicketCount), hint: 'archive' },
        { key: 'done', label: 'Done waiting', value: this.formatNumber(this.doneWaitingCount), hint: 'ready to archive' },
        { key: 'tokens', label: 'Token total', value: this.formatTokens(this.tokenTotal), hint: 'live + archive' },
      ];
    },
    statusRows() {
      const counts = this.countBy(this.liveTasks, task => task?.status || 'inbox');
      const configured = (this.columns || []).map(col => ({
        key: col.key,
        label: col.label || col.key,
        color: col.color || 'var(--gray)',
      }));
      const seen = new Set(configured.map(row => row.key));
      Object.keys(counts).forEach(key => {
        if (!seen.has(key)) configured.push({ key, label: key, color: 'var(--gray)' });
      });
      const max = Math.max(1, ...Object.values(counts));
      return configured.map(row => ({
        ...row,
        count: counts[row.key] || 0,
        percent: Math.round(((counts[row.key] || 0) / max) * 100),
      }));
    },
    typeRows() {
      return this.categoryRows(['task', 'bug', 'feature', 'chore'], task => task?.type || 'task');
    },
    priorityRows() {
      return this.categoryRows(['urgent', 'high', 'normal', 'low'], task => task?.priority || 'normal');
    },
    archivedCountLabel() {
      return `${this.formatNumber(this.archivedTicketCount)} tickets`;
    },
    archiveDateLabel() {
      return this.archiveHasExplicitDates ? 'Archive date' : 'Recorded date';
    },
    archiveHasExplicitDates() {
      return this.archiveTasks.some(task => task?.archived_at);
    },
    periodOptions() {
      return [
        { days: 1, label: '1d' },
        { days: 7, label: '7d' },
        { days: 14, label: '14d' },
        { days: 30, label: '30d' },
        { days: 90, label: '90d' },
      ];
    },
    trendWindowLabel() {
      return `Last ${this.selectedPeriodDays} day${this.selectedPeriodDays === 1 ? '' : 's'}`;
    },
    recentArchived() {
      return this.archiveTasks
        .slice()
        .sort((a, b) => this.dateMillis(this.bestArchiveDate(b)) - this.dateMillis(this.bestArchiveDate(a)))
        .slice(0, 8);
    },
    dayKeys() {
      const keys = [];
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      for (let i = this.selectedPeriodDays - 1; i >= 0; i--) {
        const d = new Date(today);
        d.setDate(today.getDate() - i);
        keys.push(this.dayKey(d));
      }
      return keys;
    },
    sparklineCharts() {
      const archivedCounts = this.seriesFor(this.archiveTasks, task => this.bestArchiveDate(task), () => 1);
      const openCounts = this.seriesFor(this.liveTasks, task => task?.created_at, () => 1);
      const archivedTime = this.seriesFor(this.archiveTasks, task => this.bestArchiveDate(task), task => this.taskTimeValue(task));
      const archivedTokens = this.seriesFor(this.archiveTasks, task => this.bestArchiveDate(task), task => this.tokenValue(task));
      return [
        {
          key: 'archived',
          label: 'Daily archived tickets',
          color: 'var(--green)',
          values: archivedCounts,
          total: archivedCounts.reduce((sum, n) => sum + n, 0),
          points: this.sparkPoints(archivedCounts),
          ticks: this.axisTicks(),
          caption: this.archiveDateLabel.toLowerCase(),
        },
        {
          key: 'open',
          label: 'Daily open tickets',
          color: 'var(--blue)',
          values: openCounts,
          total: openCounts.reduce((sum, n) => sum + n, 0),
          points: this.sparkPoints(openCounts),
          ticks: this.axisTicks(),
          caption: 'created date',
        },
        {
          key: 'archived-time',
          label: 'Daily archived total ticket time',
          color: 'var(--accent)',
          values: archivedTime,
          total: archivedTime.reduce((sum, n) => sum + n, 0),
          points: this.sparkPoints(archivedTime),
          ticks: this.axisTicks(),
          caption: this.archiveDateLabel.toLowerCase(),
          totalType: 'duration',
        },
        {
          key: 'tokens',
          label: 'Archived ticket tokens',
          color: 'var(--orange)',
          values: archivedTokens,
          total: archivedTokens.reduce((sum, n) => sum + n, 0),
          points: this.sparkPoints(archivedTokens),
          ticks: this.axisTicks(),
          caption: this.archiveDateLabel.toLowerCase(),
        },
      ];
    },
    queuedTicketCount() {
      return (this.layout?.slots || []).reduce((sum, slot) => {
        return sum + (Array.isArray(slot?.task_queue) ? slot.task_queue.length : 0);
      }, 0);
    },
    workersWithQueues() {
      return (this.layout?.slots || []).filter(slot => Array.isArray(slot?.task_queue) && slot.task_queue.length).length;
    },
    oldestOpenLabel() {
      const dated = this.liveTasks
        .map(task => ({ task, time: this.dateMillis(task?.created_at) }))
        .filter(item => item.time > 0)
        .sort((a, b) => a.time - b.time);
      if (!dated.length) return '-';
      return this.daysAgoLabel(dated[0].time);
    },
    tokenHotspotLabel() {
      const top = this.liveTasks
        .concat(this.archiveTasks)
        .slice()
        .sort((a, b) => this.tokenValue(b) - this.tokenValue(a))[0];
      const tokens = this.tokenValue(top);
      return tokens ? this.formatTokens(tokens) : '-';
    },
  },
  methods: {
    tokenValue(task) {
      const value = Number(task?.tokens);
      return Number.isFinite(value) && value > 0 ? value : 0;
    },
    taskTimeValue(task) {
      const value = typeof getReportedTaskTimeMs === 'function' ? getReportedTaskTimeMs(task) : Number(task?.reported_task_time_ms || task?.task_time_ms);
      return Number.isFinite(value) && value > 0 ? value : 0;
    },
    sumTokens(tasks) {
      return (tasks || []).reduce((sum, task) => sum + this.tokenValue(task), 0);
    },
    countBy(tasks, getter) {
      const counts = {};
      (tasks || []).forEach(task => {
        const key = String(getter(task) || '').trim() || 'unknown';
        counts[key] = (counts[key] || 0) + 1;
      });
      return counts;
    },
    categoryRows(baseKeys, getter) {
      const counts = this.countBy(this.liveTasks, getter);
      const keys = baseKeys.slice();
      Object.keys(counts).sort().forEach(key => {
        if (!keys.includes(key)) keys.push(key);
      });
      return keys.map(key => ({
        key,
        label: key.charAt(0).toUpperCase() + key.slice(1).replace(/[_-]/g, ' '),
        count: counts[key] || 0,
      }));
    },
    bestArchiveDate(task) {
      return task?.archived_at || task?.updated_at || task?.created_at || '';
    },
    dateMillis(value) {
      if (!value) return 0;
      const millis = new Date(value).getTime();
      return Number.isFinite(millis) ? millis : 0;
    },
    dayKey(value) {
      const d = value instanceof Date ? value : new Date(value);
      if (isNaN(d)) return '';
      const year = d.getFullYear();
      const month = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      return `${year}-${month}-${day}`;
    },
    seriesFor(tasks, dateGetter, valueGetter) {
      const buckets = Object.fromEntries(this.dayKeys.map(key => [key, 0]));
      (tasks || []).forEach(task => {
        const key = this.dayKey(dateGetter(task));
        if (key && Object.prototype.hasOwnProperty.call(buckets, key)) {
          buckets[key] += Number(valueGetter(task)) || 0;
        }
      });
      return this.dayKeys.map(key => buckets[key] || 0);
    },
    sparkPoints(values) {
      if (!Array.isArray(values) || values.length === 0) return '';
      const max = Math.max(1, ...values);
      const step = values.length === 1 ? 0 : 160 / Math.max(1, values.length - 1);
      return values.map((value, index) => {
        const x = values.length === 1 ? 80 : Math.round(index * step * 100) / 100;
        const y = Math.round((38 - (value / max) * 30) * 100) / 100;
        return `${x},${y}`;
      }).join(' ');
    },
    axisTicks() {
      if (!this.dayKeys.length) return [];
      const firstIndex = 0;
      const lastIndex = this.dayKeys.length - 1;
      const weeklyIndexes = [];
      for (let index = firstIndex; index <= lastIndex; index += 7) {
        weeklyIndexes.push(index);
      }
      if (!weeklyIndexes.includes(lastIndex)) weeklyIndexes.push(lastIndex);
      return weeklyIndexes.map(index => this.axisTick(this.dayKeys[index], index));
    },
    axisTick(dayKey, index) {
      const maxIndex = Math.max(1, this.dayKeys.length - 1);
      const percent = this.dayKeys.length === 1 ? 50 : (index / maxIndex) * 100;
      return {
        key: `${dayKey}-${index}`,
        x: this.dayKeys.length === 1 ? 80 : Math.round((160 * index / maxIndex) * 100) / 100,
        percent,
        label: this.formatDayKey(dayKey),
      };
    },
    formatDayKey(dayKey) {
      if (!dayKey) return '';
      return this.formatDate(`${dayKey}T00:00:00`);
    },
    formatNumber(value) {
      const n = Number(value) || 0;
      return n.toLocaleString();
    },
    formatTokens(value) {
      const n = Number(value) || 0;
      if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
      if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
      return String(n);
    },
    formatSparkTotal(chart) {
      if (chart?.totalType === 'duration') {
        return typeof formatTaskDuration === 'function' ? formatTaskDuration(chart.total) : this.formatNumber(chart.total);
      }
      return this.formatNumber(chart?.total);
    },
    formatDate(value) {
      const millis = this.dateMillis(value);
      if (!millis) return '-';
      return new Date(millis).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    },
    daysAgoLabel(millis) {
      const start = new Date();
      start.setHours(0, 0, 0, 0);
      const then = new Date(millis);
      then.setHours(0, 0, 0, 0);
      const days = Math.max(0, Math.round((start.getTime() - then.getTime()) / 86400000));
      if (days === 0) return 'Today';
      if (days === 1) return '1 day';
      return `${days} days`;
    },
  },
};
