const LiveAgentChatTab = {
  props: {
    sessionId: {
      type: String,
      default: null,
    },
    workspaceId: {
      type: String,
      default: null,
    },
  },
  data() {
    return {
      provider: 'claude',
      model: 'claude-sonnet-4-6',
      input: '',
      messages: [],       // {role: 'user'|'assistant', content: string, streaming?: bool}
      busy: false,
      activeSessionId: this.sessionId || _generateChatSessionId(),
      _streamingBuf: '',
    };
  },
  computed: {
    providerOptions() {
      return ['claude', 'codex', 'gemini'];
    },
    modelOptions() {
      return MODEL_OPTIONS[this.provider] || [];
    },
  },
  watch: {
    provider(newProvider) {
      const opts = this.modelOptions;
      if (!opts.includes(this.model)) this.model = opts[0] || '';
    },
  },
  mounted() {
    this._registerSocketHandlers();
    this.$nextTick(() => this.$refs.input && this.$refs.input.focus());
  },
  beforeUnmount() {
    this._removeSocketHandlers();
  },
  methods: {
    _registerSocketHandlers() {
      const s = window._bullpenSocket;
      if (!s) return;
      const _sameChatSession = (data) => {
        if (!data || data.sessionId !== this.activeSessionId) return false;
        if (data.workspaceId && this.workspaceId && data.workspaceId !== this.workspaceId) return false;
        return true;
      };
      this._onOutput = (data) => {
        if (!_sameChatSession(data)) return;
        const last = this.messages[this.messages.length - 1];
        if (!last || last.role !== 'assistant' || !last.streaming) {
          this.messages.push({ role: 'assistant', content: '', streaming: true });
        }
        const msg = this.messages[this.messages.length - 1];
        const lines = data.lines || [];
        for (const line of lines) {
          if (msg.content && !msg.content.endsWith('\n')) {
            msg.content += '\n';
          }
          msg.content += line;
        }
        this._scrollToBottom();
      };
      this._onUser = (data) => {
        if (!_sameChatSession(data)) return;
        if (data.senderSid && s.id && data.senderSid === s.id) return;
        const text = String(data.message || '').trim();
        if (!text) return;
        this.messages.push({ role: 'user', content: text });
        this.busy = true;
        this._scrollToBottom();
      };
      this._onDone = (data) => {
        if (!_sameChatSession(data)) return;
        const last = this.messages[this.messages.length - 1];
        if (last && last.streaming) last.streaming = false;
        this.busy = false;
        this._scrollToBottom();
      };
      this._onError = (data) => {
        if (!_sameChatSession(data)) return;
        this.messages.push({ role: 'system', content: 'Error: ' + (data.message || 'Unknown error') });
        this.busy = false;
        this._scrollToBottom();
      };
      this._onCleared = (data) => {
        if (!_sameChatSession(data)) return;
        this.messages = [];
        this.busy = false;
      };
      s.on('chat:user', this._onUser);
      s.on('chat:output', this._onOutput);
      s.on('chat:done', this._onDone);
      s.on('chat:error', this._onError);
      s.on('chat:cleared', this._onCleared);
    },
    _removeSocketHandlers() {
      const s = window._bullpenSocket;
      if (!s) return;
      if (this._onUser) s.off('chat:user', this._onUser);
      if (this._onOutput) s.off('chat:output', this._onOutput);
      if (this._onDone) s.off('chat:done', this._onDone);
      if (this._onError) s.off('chat:error', this._onError);
      if (this._onCleared) s.off('chat:cleared', this._onCleared);
    },
    sendMessage() {
      const text = this.input.trim();
      if (!text || this.busy) return;
      this.messages.push({ role: 'user', content: text });
      this.input = '';
      this.busy = true;
      this._scrollToBottom();
      const s = window._bullpenSocket;
      if (s) {
        s.emit('chat:send', {
          sessionId: this.activeSessionId,
          provider: this.provider,
          model: this.model,
          message: text,
          workspaceId: this.workspaceId,
        });
      }
    },
    stopChat() {
      const s = window._bullpenSocket;
      if (s) s.emit('chat:stop', { sessionId: this.activeSessionId, workspaceId: this.workspaceId });
    },
    clearChat() {
      this.messages = [];
      this.busy = false;
      const s = window._bullpenSocket;
      if (s) s.emit('chat:clear', { sessionId: this.activeSessionId, workspaceId: this.workspaceId });
      this.$nextTick(() => this.$refs.input && this.$refs.input.focus());
    },
    onKeydown(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.sendMessage();
      }
    },
    _scrollToBottom() {
      this.$nextTick(() => {
        const el = this.$refs.messages;
        if (el) el.scrollTop = el.scrollHeight;
      });
    },
  },
  template: `
    <div class="chat-tab">
      <div class="chat-toolbar">
        <label class="chat-label">Provider</label>
        <select class="form-select chat-select" v-model="provider" :disabled="busy">
          <option v-for="p in providerOptions" :key="p" :value="p">{{ p }}</option>
        </select>
        <label class="chat-label">Model</label>
        <select class="form-select chat-select" v-model="model" :disabled="busy">
          <option v-for="m in modelOptions" :key="m" :value="m">{{ m }}</option>
        </select>
        <button class="btn btn-sm" @click="clearChat" :disabled="busy">Clear</button>
      </div>
      <div class="chat-messages" ref="messages">
        <div v-if="messages.length === 0" class="chat-empty">
          Start a conversation with the AI agent.
        </div>
        <div v-for="(msg, i) in messages" :key="i"
             :class="['chat-message', 'chat-message--' + msg.role]">
          <div class="chat-bubble">
            <pre class="chat-text">{{ msg.content }}<span v-if="msg.streaming" class="chat-cursor">▍</span></pre>
          </div>
        </div>
      </div>
      <div class="chat-input-row">
        <textarea
          ref="input"
          class="chat-input"
          v-model="input"
          :disabled="busy"
          placeholder="Type a message... (Enter to send, Shift+Enter for newline)"
          rows="3"
          @keydown="onKeydown"
        ></textarea>
        <button v-if="busy" class="btn btn-danger chat-stop-btn" @click="stopChat">Stop</button>
        <button v-else class="btn chat-send-btn" :disabled="!input.trim()" @click="sendMessage">Send</button>
      </div>
    </div>
  `,
};

function _generateChatSessionId() {
  return 'chat-' + crypto.randomUUID();
}
