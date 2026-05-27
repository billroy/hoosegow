const TaskCreateModal = {
  props: ['visible'],
  emits: ['close', 'create'],
  data() {
    return {
      title: '',
      type: 'task',
      priority: 'normal',
      tags: '',
      description: '',
    };
  },
  template: `
    <div v-if="visible" class="modal-overlay" @click.self="$emit('close')" @keydown.escape="$emit('close')" @keydown.meta.enter="onPrimaryShortcut" tabindex="0" ref="overlay">
      <div class="modal">
        <div class="modal-header">
          <h2>New Ticket</h2>
          <button class="btn btn-icon" @click="$emit('close')">&times;</button>
        </div>
        <div class="modal-body">
          <label class="form-label">
            Title
            <input class="form-input" v-model="title" ref="titleInput"
                   @keydown.enter="submit" placeholder="Ticket title" autofocus>
          </label>
          <div class="form-row">
            <label class="form-label">
              Type
              <select class="form-select" v-model="type">
                <option value="task">Task</option>
                <option value="bug">Bug</option>
                <option value="feature">Feature</option>
                <option value="chore">Chore</option>
              </select>
            </label>
            <label class="form-label">
              Priority
              <select class="form-select" v-model="priority">
                <option value="low">Low</option>
                <option value="normal">Normal</option>
                <option value="high">High</option>
                <option value="urgent">Urgent</option>
              </select>
            </label>
          </div>
          <label class="form-label">
            Tags <span class="form-hint">(comma separated)</span>
            <input class="form-input" v-model="tags" placeholder="backend, auth">
          </label>
          <label class="form-label">
            Description
            <textarea class="form-textarea" v-model="description" rows="4"
                      placeholder="Describe the task..."></textarea>
          </label>
        </div>
        <div class="modal-footer">
          <button class="btn" @click="$emit('close')">Cancel</button>
          <button class="btn btn-primary" @click="submit" :disabled="!title.trim()">Create</button>
        </div>
      </div>
    </div>
  `,
  watch: {
    visible(v) {
      if (v) {
        this.title = '';
        this.type = 'task';
        this.priority = 'normal';
        this.tags = '';
        this.description = '';
        this.$nextTick(() => this.$refs.titleInput?.focus());
      }
    }
  },
  methods: {
    onPrimaryShortcut(e) {
      e.preventDefault();
      this.submit();
    },
    submit() {
      if (!this.title.trim()) return;
      const tags = this.tags
        .split(',')
        .map(t => t.trim())
        .filter(Boolean);
      this.$emit('create', {
        title: this.title.trim(),
        type: this.type,
        priority: this.priority,
        tags,
        description: this.description,
      });
      this.$emit('close');
    }
  }
};
