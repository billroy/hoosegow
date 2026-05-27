const TaskCard = {
  props: ['task', 'layout'],
  emits: ['select-task', 'update-task'],
  data() {
    return {
      activePopup: null,
    };
  },
  computed: {
    assignedWorkerName() {
      if (!this.task.assigned_to && this.task.assigned_to !== 0) return null;
      const slot = parseInt(this.task.assigned_to, 10);
      if (isNaN(slot)) return null;
      const worker = this.layout?.slots?.[slot];
      return worker?.name || null;
    },
    priorityValue() {
      return this.task.priority || 'normal';
    },
    typeValue() {
      return this.task.type || 'task';
    },
    priorityOptions() {
      return [
        { value: 'low', label: 'Low' },
        { value: 'normal', label: 'Normal' },
        { value: 'high', label: 'High' },
        { value: 'urgent', label: 'Urgent' },
      ];
    },
    typeOptions() {
      return [
        { value: 'task', label: 'Task' },
        { value: 'bug', label: 'Bug' },
        { value: 'feature', label: 'Feature' },
        { value: 'chore', label: 'Chore' },
      ];
    }
  },
  template: `
    <div class="task-card"
         draggable="true"
         @dragstart="onDragStart"
         @dragend="onDragEnd"
         @click="selectTask">
      <div class="task-card-title">
        <i class="ticket-type-icon ticket-type-icon--card" data-lucide="tag" aria-hidden="true"></i>
        <span class="task-card-title-text">{{ task.title }}</span>
      </div>
      <div class="task-card-meta">
        <div class="task-card-bean-wrap">
          <button
            type="button"
            class="badge task-card-bean-btn"
            :class="['priority-' + priorityValue, { 'task-card-bean-open': activePopup === 'priority' }]"
            title="Change priority"
            @click.stop="togglePopup('priority')"
            @keydown.escape.stop.prevent="closePopup"
          >{{ priorityValue }}</button>
          <div
            v-if="activePopup === 'priority'"
            class="task-card-popup"
            role="menu"
            aria-label="Set priority"
            @click.stop
            @keydown.escape.stop.prevent="closePopup"
          >
            <button
              v-for="opt in priorityOptions"
              :key="opt.value"
              type="button"
              class="task-card-popup-item"
              :class="{ active: opt.value === priorityValue }"
              @click.stop="applyPopupChoice('priority', opt.value)"
            >{{ opt.label }}</button>
          </div>
        </div>
        <div class="task-card-bean-wrap">
          <button
            type="button"
            class="badge type-badge task-card-bean-btn"
            :class="['type-' + typeValue, { 'task-card-bean-open': activePopup === 'type' }]"
            title="Change type"
            @click.stop="togglePopup('type')"
            @keydown.escape.stop.prevent="closePopup"
          >{{ typeValue }}</button>
          <div
            v-if="activePopup === 'type'"
            class="task-card-popup"
            role="menu"
            aria-label="Set type"
            @click.stop
            @keydown.escape.stop.prevent="closePopup"
          >
            <button
              v-for="opt in typeOptions"
              :key="opt.value"
              type="button"
              class="task-card-popup-item"
              :class="{ active: opt.value === typeValue }"
              @click.stop="applyPopupChoice('type', opt.value)"
            >{{ opt.label }}</button>
          </div>
        </div>
      </div>
      <span v-if="assignedWorkerName" class="task-card-worker">{{ assignedWorkerName }}</span>
    </div>
  `,
  mounted() {
    document.addEventListener('pointerdown', this.handlePointerDownOutside);
  },
  beforeUnmount() {
    document.removeEventListener('pointerdown', this.handlePointerDownOutside);
  },
  methods: {
    selectTask() {
      this.closePopup();
      this.$emit('select-task', this.task.id);
    },
    togglePopup(field) {
      this.activePopup = this.activePopup === field ? null : field;
    },
    closePopup() {
      this.activePopup = null;
    },
    handlePointerDownOutside(event) {
      if (!this.activePopup) return;
      if (this.$el?.contains(event.target)) return;
      this.closePopup();
    },
    applyPopupChoice(field, value) {
      const currentValue = field === 'priority' ? this.priorityValue : this.typeValue;
      if (value !== currentValue) {
        this.$emit('update-task', { id: this.task.id, [field]: value });
      }
      this.closePopup();
    },
    onDragStart(e) {
      this.closePopup();
      window.dispatchEvent(new Event('bullpen:task-drag:start'));
      e.dataTransfer.setData(window.BULLPEN_TASK_DND_MIME, this.task.id);
      e.dataTransfer.setData('text/plain', this.task.id);
      e.dataTransfer.effectAllowed = 'move';
    },
    onDragEnd() {
      window.dispatchEvent(new Event('bullpen:task-drag:end'));
    }
  }
};
