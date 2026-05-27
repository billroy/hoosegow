const ToastContainer = {
  props: ['toasts'],
  emits: ['dismiss'],
  template: `
    <div class="toast-container">
      <div v-for="toast in visibleToasts" :key="toast.id"
           class="toast" :class="'toast-' + toast.type">
        <span class="toast-message">{{ toast.message }}</span>
        <button class="toast-close" @click="$emit('dismiss', toast.id)">&times;</button>
      </div>
    </div>
  `,
  computed: {
    visibleToasts() {
      return (this.toasts || []).slice(-5);
    }
  }
};
