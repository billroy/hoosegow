/**
 * Web Audio API synthesizer for BizzBox.
 * All sounds are synthesized — no audio files required.
 */

class AudioEngine {
  constructor() {
    this._ctx = null;
    this._masterGain = null;
    this._muted = false;
    this._volume = 1.0;   // 0.0–1.0, scales within 0.4 ceiling
    this._ready = false;
    // Ambient state
    this._ambientNodes = null;
    this._ambientGain = null;
    this._ambientActive = false;
  }

  _init() {
    if (this._ready) return;
    try {
      this._ctx = new (window.AudioContext || window.webkitAudioContext)();
      this._masterGain = this._ctx.createGain();
      this._masterGain.gain.value = this._muted ? 0 : this._volume * 0.4;
      this._masterGain.connect(this._ctx.destination);
      this._ready = true;
    } catch (e) {
      console.warn('BizzBox: Web Audio not available', e);
    }
  }

  _resume() {
    if (this._ctx && this._ctx.state === 'suspended') {
      this._ctx.resume();
    }
  }

  /**
   * Play a tone with given parameters.
   * @param {number} freq      Start frequency
   * @param {number} freq2     End frequency (for sweep, or same as freq)
   * @param {number} duration  Duration in seconds
   * @param {string} type      OscillatorType: 'sine'|'square'|'sawtooth'|'triangle'
   * @param {number} volume    0–1
   */
  _tone(freq, freq2, duration, type = 'sine', volume = 0.3) {
    if (!this._ready || this._muted) return;
    this._resume();
    const vol = volume * 0.316;   // event sounds −10 dB
    const now = this._ctx.currentTime;
    const osc = this._ctx.createOscillator();
    const gain = this._ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, now);
    if (freq2 !== freq) {
      osc.frequency.linearRampToValueAtTime(freq2, now + duration);
    }
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(vol, now + 0.01);
    gain.gain.setValueAtTime(vol, now + duration - 0.05);
    gain.gain.linearRampToValueAtTime(0, now + duration);
    osc.connect(gain);
    gain.connect(this._masterGain);
    osc.start(now);
    osc.stop(now + duration);
  }

  _noise(duration, volume = 0.05) {
    if (!this._ready || this._muted) return;
    this._resume();
    const vol = volume * 0.316;   // event sounds −10 dB
    const now = this._ctx.currentTime;
    const bufLen = Math.floor(this._ctx.sampleRate * duration);
    const buf = this._ctx.createBuffer(1, bufLen, this._ctx.sampleRate);
    const data = buf.getChannelData(0);
    for (let i = 0; i < bufLen; i++) data[i] = Math.random() * 2 - 1;
    const src = this._ctx.createBufferSource();
    src.buffer = buf;
    const gain = this._ctx.createGain();
    gain.gain.setValueAtTime(vol, now);
    gain.gain.linearRampToValueAtTime(0, now + duration);
    src.connect(gain);
    gain.connect(this._masterGain);
    src.start(now);
  }

  /** Called on first user interaction to unlock AudioContext */
  unlock() {
    this._init();
    this._resume();
  }

  mute() {
    this._muted = true;
    if (this._masterGain) this._masterGain.gain.value = 0;
  }

  unmute() {
    this._muted = false;
    if (this._masterGain) this._masterGain.gain.value = this._volume * 0.4;
  }

  setMuted(val) {
    val ? this.mute() : this.unmute();
  }

  /** Set volume 0.0–1.0. Scales within the 0.4 master ceiling. */
  setVolume(v) {
    this._volume = Math.max(0, Math.min(1, v));
    if (!this._muted && this._masterGain) {
      this._masterGain.gain.value = this._volume * 0.4;
    }
  }

  getVolume() {
    return this._volume;
  }

  // ── Ambient soundscape presets ──────────────────────────────

  /**
   * Start an ambient preset by name, or stop if already playing the same preset.
   * @param {string} preset  One of AMBIENT_PRESETS keys
   * @param {number} intensity  Current activity intensity (1-20)
   */
  startAmbient(preset, intensity) {
    this._init();
    this._resume();
    // If already running, stop first (crossfade)
    if (this._ambientActive) this._teardownAmbient();

    const ctx = this._ctx;
    if (!ctx) return;

    const builder = AMBIENT_PRESETS[preset];
    if (!builder) return;

    const now = ctx.currentTime;
    const targetVol = this._ambientVolume(intensity);

    // Master ambient gain — fade in
    this._ambientGain = ctx.createGain();
    this._ambientGain.gain.value = 0;
    this._ambientGain.connect(this._masterGain);

    // Build the preset's node graph; collect stoppable/disconnectable nodes
    const nodes = builder(ctx, this._ambientGain, now);
    this._ambientStoppables = nodes;

    // Fade in over 2s
    this._ambientGain.gain.setValueAtTime(0.001, now);
    this._ambientGain.gain.linearRampToValueAtTime(targetVol, now + 2);

    this._ambientActive = true;
    this._ambientPreset = preset;
  }

  stopAmbient() {
    if (!this._ambientActive || !this._ctx) return;
    this._teardownAmbient();
  }

  _teardownAmbient() {
    if (!this._ambientGain || !this._ctx) {
      this._ambientActive = false;
      return;
    }
    const ctx = this._ctx;
    const now = ctx.currentTime;
    // Fade out
    this._ambientGain.gain.cancelScheduledValues(now);
    this._ambientGain.gain.setValueAtTime(this._ambientGain.gain.value, now);
    this._ambientGain.gain.linearRampToValueAtTime(0, now + 1.5);

    const stoppables = this._ambientStoppables || [];
    const gain = this._ambientGain;
    setTimeout(() => {
      for (const n of stoppables) {
        try { n.stop(); } catch (e) {}
        try { n.disconnect(); } catch (e) {}
      }
      try { gain.disconnect(); } catch (e) {}
    }, 1800);

    this._ambientStoppables = null;
    this._ambientGain = null;
    this._ambientActive = false;
    this._ambientPreset = null;
  }

  updateAmbientIntensity(intensity) {
    if (!this._ambientActive || !this._ambientGain || !this._ctx) return;
    const now = this._ctx.currentTime;
    const vol = this._ambientVolume(intensity);
    this._ambientGain.gain.cancelScheduledValues(now);
    this._ambientGain.gain.setValueAtTime(this._ambientGain.gain.value, now);
    this._ambientGain.gain.linearRampToValueAtTime(vol, now + 0.5);
  }

  _ambientVolume(intensity) {
    // intensity 1 → 0.113, intensity 20 → 0.708  (+7 dB over original)
    return 0.113 + (intensity - 1) * (0.595 / 19);
  }

  // ── Toast notification sound ─────────────────────────────────

  playToast() {
    this._init();
    // Quick two-note chime: C6 → E6
    this._tone(1047, 1047, 0.06, 'sine', 0.35);
    setTimeout(() => this._tone(1319, 1319, 0.08, 'sine', 0.25), 70);
  }

  // ── Spawn / despawn sounds ────────────────────────────────────

  playSpawn() {
    this._init();
    // Short ascending chirp
    this._tone(400, 800, 0.15, 'sine', 0.25);
    this._tone(600, 1200, 0.1, 'triangle', 0.1);
  }

  playDespawn() {
    this._init();
    // Short descending chirp
    this._tone(800, 300, 0.2, 'sine', 0.2);
  }

  // ── Event sounds (task/worker lifecycle) ─────────────────────

  /** Task started / worker picked up: two-note rising E5→B5 triangle */
  playStart() {
    this._init();
    this._tone(659, 659, 0.09, 'triangle', 0.25);
    setTimeout(() => this._tone(988, 988, 0.09, 'triangle', 0.25), 70);
  }

  /** Task done: three-note C-major arpeggio C5→E5→G5 */
  playDone() {
    this._init();
    this._tone(523, 523, 0.09, 'sine', 0.25);
    setTimeout(() => this._tone(659, 659, 0.09, 'sine', 0.25), 60);
    setTimeout(() => this._tone(784, 784, 0.12, 'sine', 0.25), 120);
  }

  /** Ticket moved / reassigned: quiet short tick */
  playMove() {
    this._init();
    this._tone(520, 520, 0.04, 'triangle', 0.12);
  }

  /** Task reverted to inbox: descending minor third A4→F4 */
  playRevert() {
    this._init();
    this._tone(440, 349, 0.12, 'sine', 0.18);
  }

  /** Error: short noise burst + low sawtooth buzz */
  playError() {
    this._init();
    this._noise(0.08, 0.10);
    this._tone(180, 120, 0.15, 'sawtooth', 0.2);
  }

  /**
   * Temporarily dip the ambient gain so an event sound is audible over it.
   * @param {number} dBDepth  Attenuation in dB (positive value; e.g. 6 → −6 dB)
   * @param {number} durationMs  How long to stay ducked before restoring
   */
  _duckAmbient(dBDepth = 6, durationMs = 300) {
    if (!this._ambientActive || !this._ambientGain || !this._ctx) return;
    const now = this._ctx.currentTime;
    const current = this._ambientGain.gain.value;
    const factor = Math.pow(10, -Math.abs(dBDepth) / 20);
    const ducked = current * factor;
    this._ambientGain.gain.cancelScheduledValues(now);
    this._ambientGain.gain.setValueAtTime(current, now);
    this._ambientGain.gain.linearRampToValueAtTime(ducked, now + 0.04);
    this._ambientGain.gain.setValueAtTime(ducked, now + durationMs / 1000);
    this._ambientGain.gain.linearRampToValueAtTime(current, now + durationMs / 1000 + 0.15);
  }

  // ── Channel join / leave sounds ────────────────────────────────

  playClientJoin() {
    this._init();
    // Bright ascending two-note chime: D5 → A5
    this._tone(587, 587, 0.06, 'sine', 0.15);
    setTimeout(() => this._tone(880, 880, 0.08, 'sine', 0.12), 80);
  }

  playClientLeave() {
    this._init();
    // Soft descending note: A4 → E4
    this._tone(440, 330, 0.15, 'sine', 0.1);
  }

  // ── Per-activity ambient sounds ──────────────────────────────

  playActivitySound(type) {
    this._init();
    switch (type) {
      case 'radar':
        this._tone(880, 880, 0.08, 'sine', 0.3);
        break;
      case 'oscilloscope':
        this._tone(440, 440, 0.3, 'triangle', 0.15);
        break;
      case 'terminal':
      case 'code_scroll':
        this._noise(0.03, 0.08);
        break;
      case 'log_tail':
        this._tone(660, 660, 0.05, 'square', 0.1);
        break;
      case 'network_topology':
        this._tone(523, 659, 0.12, 'sine', 0.15);
        break;
      case 'countdown':
        this._tone(880, 880, 0.1, 'square', 0.2);
        setTimeout(() => this._tone(1108, 1108, 0.1, 'square', 0.2), 150);
        break;
      case 'facial_recognition':
        this._tone(1200, 800, 0.15, 'sawtooth', 0.1);
        break;
      case 'hex_dump':
        this._noise(0.05, 0.06);
        break;
      case 'resource_gauges':
        this._tone(330, 330, 0.2, 'sine', 0.12);
        break;
      case 'geo_map':
        this._tone(440, 554, 0.2, 'sine', 0.12);
        break;
      case 'notifications':
        this._tone(880, 1108, 0.08, 'sine', 0.2);
        setTimeout(() => this._tone(1108, 880, 0.08, 'sine', 0.15), 100);
        break;
      case 'matrix_rain':
        this._noise(0.04, 0.05);
        this._tone(200, 150, 0.15, 'sine', 0.08);
        break;
      case 'audio_spectrum':
        this._tone(220, 440, 0.12, 'triangle', 0.12);
        break;
      case 'progress_bars':
        this._tone(550, 550, 0.06, 'square', 0.08);
        break;
      case 'dna_sequence':
        this._tone(300, 350, 0.1, 'sine', 0.1);
        this._noise(0.03, 0.04);
        break;
      case 'graph':
        this._tone(392, 392, 0.15, 'sine', 0.1);
        break;
      case 'orbital_view':
        this._tone(260, 340, 0.2, 'sine', 0.08);
        this._tone(520, 680, 0.15, 'triangle', 0.05);
        break;
      case 'camera_feed':
        this._noise(0.06, 0.04);
        break;
      case 'cipher_decrypt':
        this._noise(0.02, 0.06);
        this._tone(600, 800, 0.08, 'square', 0.08);
        break;
      case 'data_table':
        this._tone(700, 700, 0.04, 'square', 0.06);
        break;
      case 'system_topology':
        this._tone(440, 480, 0.1, 'sine', 0.08);
        this._noise(0.02, 0.03);
        break;
      case 'globe_arcs':
        this._tone(350, 500, 0.15, 'sine', 0.1);
        break;
      case 'chat_intercept':
        this._noise(0.04, 0.08);
        this._tone(500, 600, 0.08, 'square', 0.08);
        break;
      case 'wireframe_3d':
        this._tone(110, 110, 0.2, 'sine', 0.08);
        break;
      case 'power_grid':
        this._tone(60, 60, 0.15, 'sawtooth', 0.06);
        this._noise(0.02, 0.04);
        break;
      case 'game_of_life':
        this._tone(800, 800, 0.03, 'sine', 0.08);
        this._tone(600, 600, 0.03, 'sine', 0.06);
        break;
      case 'satellite_telemetry':
        this._tone(1200, 800, 0.15, 'sine', 0.07);
        this._tone(600, 600, 0.05, 'sine', 0.04);
        break;
      case 'packet_sniffer':
        this._tone(2000, 1800, 0.04, 'square', 0.05);
        this._noise(0.02, 0.04);
        break;
      case 'seismograph':
        this._tone(80, 40, 0.3, 'sine', 0.1);
        this._tone(120, 60, 0.2, 'sine', 0.06);
        break;
      case 'access_control':
        this._tone(660, 660, 0.06, 'square', 0.08);
        this._tone(880, 880, 0.04, 'square', 0.06);
        break;
      case 'sdr_waterfall':
        this._tone(300, 1200, 0.2, 'sine', 0.08);     // frequency sweep
        this._noise(0.04, 0.05);
        break;
      case 'qam_constellation':
        this._tone(1000, 1000, 0.04, 'sine', 0.1);    // data burst blip
        this._tone(1500, 1500, 0.03, 'sine', 0.06);
        break;
      case 'heart_monitor':
        this._tone(880, 880, 0.06, 'sine', 0.15);     // ECG beep
        setTimeout(() => this._tone(880, 880, 0.04, 'sine', 0.08), 120);
        break;
      case 'transit_map':
        this._tone(587, 587, 0.08, 'sine', 0.12);     // arrival chime
        setTimeout(() => this._tone(784, 784, 0.08, 'sine', 0.1), 100);
        break;
      case 'weather_radar':
        this._tone(500, 600, 0.15, 'sine', 0.08);     // sweep tone
        this._noise(0.03, 0.04);
        break;
      case 'stock_list':
      case 'stock_graph':
        this._tone(1200, 1200, 0.03, 'square', 0.06); // ticker click
        setTimeout(() => this._tone(1100, 1100, 0.03, 'square', 0.05), 60);
        break;
      case 'blockchain':
        this._tone(800, 1200, 0.1, 'sine', 0.1);      // digital chime
        this._tone(1600, 2400, 0.06, 'sine', 0.05);
        break;
      case 'flight_tracker':
        this._tone(1400, 1400, 0.04, 'sine', 0.1);    // ATC blip
        this._noise(0.02, 0.03);
        break;
      case 'server_rack':
        this._noise(0.15, 0.04);                       // fan whir
        this._tone(120, 120, 0.1, 'sine', 0.05);
        break;
      case 'cctv_mosaic':
        this._noise(0.06, 0.06);                       // static burst
        this._tone(60, 60, 0.04, 'sawtooth', 0.04);
        break;
      case 'process_monitor':
        this._noise(0.02, 0.05);                       // keystroke click
        this._tone(1800, 1800, 0.02, 'square', 0.04);
        break;
      case 'sonar':
        this._tone(1200, 1200, 0.15, 'sine', 0.12);   // sonar ping
        setTimeout(() => this._tone(1200, 800, 0.3, 'sine', 0.04), 160);
        break;
      case 'warp_drive':
        this._tone(50, 55, 0.3, 'sine', 0.1);         // low throb
        this._tone(800, 1200, 0.15, 'sine', 0.04);     // shimmer
        break;
      case 'mech_bay':
        this._tone(80, 120, 0.2, 'sawtooth', 0.06);   // servo whir
        this._noise(0.05, 0.04);
        break;
      case 'terraforming':
        this._tone(40, 30, 0.3, 'sine', 0.1);         // geological rumble
        this._noise(0.08, 0.04);
        break;
      case 'dungeon_master':
        this._noise(0.04, 0.08);                       // sword clash burst
        this._tone(200, 150, 0.1, 'sawtooth', 0.08);
        break;
      case 'space_elevator':
        this._tone(100, 100, 0.25, 'sine', 0.06);     // cable hum
        this._tone(200, 200, 0.15, 'sine', 0.03);
        break;
      case 'submarine_helm':
        this._tone(800, 400, 0.2, 'sine', 0.08);      // ping sweep
        this._tone(60, 60, 0.15, 'sine', 0.05);
        break;
      case 'wildfire_command':
        this._noise(0.1, 0.06);                        // fire crackle
        this._tone(100, 80, 0.15, 'sine', 0.05);
        break;
      case 'hyperloop':
        this._tone(200, 1000, 0.15, 'sine', 0.08);    // whoosh sweep
        this._noise(0.03, 0.03);
        break;
      case 'genetics_lab':
        this._tone(1800, 1800, 0.06, 'sine', 0.1);    // lab beep
        setTimeout(() => this._tone(1400, 1400, 0.06, 'sine', 0.07), 100);
        break;
      case 'mission_control':
        this._tone(1000, 1200, 0.08, 'sine', 0.1);    // comm chirp
        this._noise(0.02, 0.03);
        break;
      case 'pong':
        this._tone(440, 480, 0.04, 'square', 0.06);   // paddle hit blip
        break;
      case 'tic_tac_toe':
        this._tone(600, 600, 0.05, 'sine', 0.08);     // mark placement
        this._tone(800, 800, 0.03, 'sine', 0.05);
        break;
      default:
        this._tone(440, 440, 0.1, 'sine', 0.1);
    }
  }
}

// ── Ambient Preset Helpers ──────────────────────────────────────
// Each returns an array of stoppable AudioNodes (oscillators, buffer sources).
// All connect to the provided `dest` gain node.

function makeNoise(ctx, dest, now, filterFreq, filterQ, vol, filterType = 'lowpass') {
  const bufLen = ctx.sampleRate * 4;
  const buf = ctx.createBuffer(1, bufLen, ctx.sampleRate);
  const d = buf.getChannelData(0);
  for (let i = 0; i < bufLen; i++) d[i] = Math.random() * 2 - 1;
  const src = ctx.createBufferSource();
  src.buffer = buf;
  src.loop = true;
  const filter = ctx.createBiquadFilter();
  filter.type = filterType;
  filter.frequency.value = filterFreq;
  filter.Q.value = filterQ;
  const gain = ctx.createGain();
  gain.gain.value = vol;
  src.connect(filter);
  filter.connect(gain);
  gain.connect(dest);
  src.start(now);
  return src;
}

function makeOsc(ctx, dest, now, freq, type, vol) {
  const osc = ctx.createOscillator();
  osc.type = type;
  osc.frequency.value = freq;
  const gain = ctx.createGain();
  gain.gain.value = vol;
  osc.connect(gain);
  gain.connect(dest);
  osc.start(now);
  return osc;
}

function makeLFO(ctx, target, now, freq, amount) {
  const lfo = ctx.createOscillator();
  lfo.type = 'sine';
  lfo.frequency.value = freq;
  const lfoGain = ctx.createGain();
  lfoGain.gain.value = amount;
  lfo.connect(lfoGain);
  lfoGain.connect(target);
  lfo.start(now);
  return lfo;
}

// ── Preset Definitions ─────────────────────────────────────────

const AMBIENT_PRESETS = {
  // Original server room: detuned 55Hz drone + filtered noise
  server_room(ctx, dest, now) {
    const o1 = makeOsc(ctx, dest, now, 55, 'sine', 0.6);
    const o2 = makeOsc(ctx, dest, now, 55.5, 'sine', 0.6);
    const n = makeNoise(ctx, dest, now, 400, 1, 0.3);
    return [o1, o2, n];
  },

  // Forest rain: pink-ish filtered noise (low rumble + high patter)
  forest_rain(ctx, dest, now) {
    const low = makeNoise(ctx, dest, now, 200, 0.5, 0.4);        // distant thunder rumble
    const mid = makeNoise(ctx, dest, now, 1200, 0.7, 0.25);      // leaf patter
    const high = makeNoise(ctx, dest, now, 4000, 2.0, 0.15);     // rain hiss
    // Slow LFO on low filter for rolling thunder
    const lowFilter = low; // can't LFO a buffer source directly — but the noise itself fluctuates
    const wind = makeOsc(ctx, dest, now, 0.3, 'sine', 0.08);     // very low sub-rumble
    return [low, mid, high, wind];
  },

  // Drone approaching: rising sawtooth sweep + rotorblade chop
  drone_approaching(ctx, dest, now) {
    const motor = makeOsc(ctx, dest, now, 85, 'sawtooth', 0.25);
    const motor2 = makeOsc(ctx, dest, now, 170, 'sawtooth', 0.12);
    // Blade chop: amplitude-modulated noise
    const chop = makeNoise(ctx, dest, now, 600, 2, 0.2);
    const chopLfo = makeLFO(ctx, chop.context ? dest : dest, now, 12, 0.08); // ~12 Hz chop
    const wind = makeNoise(ctx, dest, now, 2000, 0.5, 0.1);
    return [motor, motor2, chop, chopLfo, wind];
  },

  // Bunker with countdown: deep sub-bass pulse + ticking + distant rumble
  bunker_countdown(ctx, dest, now) {
    const sub = makeOsc(ctx, dest, now, 30, 'sine', 0.5);        // deep sub-bass
    const hum = makeOsc(ctx, dest, now, 60, 'triangle', 0.2);    // fluorescent hum
    const hum2 = makeOsc(ctx, dest, now, 120, 'sine', 0.08);     // harmonic
    const rumble = makeNoise(ctx, dest, now, 150, 0.5, 0.2);     // distant shaking
    // Ticking: square wave pulse at 1Hz
    const tick = makeOsc(ctx, dest, now, 1.0, 'square', 0.03);
    const tickTone = makeOsc(ctx, dest, now, 1000, 'sine', 0.0);
    // Modulate tick tone amplitude with tick
    const tickMod = makeLFO(ctx, dest, now, 1.0, 0.04);
    return [sub, hum, hum2, rumble, tick, tickTone, tickMod];
  },

  // Deep space: very slow detuned pads + cosmic crackle
  deep_space(ctx, dest, now) {
    const pad1 = makeOsc(ctx, dest, now, 65, 'sine', 0.35);
    const pad2 = makeOsc(ctx, dest, now, 65.2, 'sine', 0.35);
    const pad3 = makeOsc(ctx, dest, now, 97.5, 'sine', 0.15);    // fifth
    const pad4 = makeOsc(ctx, dest, now, 130.5, 'sine', 0.08);   // octave
    const crackle = makeNoise(ctx, dest, now, 8000, 5, 0.04);    // sparse crackle
    const sub = makeOsc(ctx, dest, now, 20, 'sine', 0.2);        // infrasonic rumble
    return [pad1, pad2, pad3, pad4, crackle, sub];
  },

  // War room: tense low brass + radio static + heartbeat pulse
  war_room(ctx, dest, now) {
    const brass1 = makeOsc(ctx, dest, now, 73.4, 'sawtooth', 0.12);  // D2
    const brass2 = makeOsc(ctx, dest, now, 82.4, 'sawtooth', 0.08);  // E2
    const static1 = makeNoise(ctx, dest, now, 3000, 3, 0.06);
    const sub = makeOsc(ctx, dest, now, 36.7, 'sine', 0.25);    // sub D1
    // Heartbeat LFO ~ 1.1Hz
    const pulse = makeLFO(ctx, dest, now, 1.1, 0.06);
    const hum = makeOsc(ctx, dest, now, 50, 'sine', 0.15);      // electrical hum
    return [brass1, brass2, static1, sub, pulse, hum];
  },

  // Ocean depth: underwater rumble + whale-like slow sweeps
  ocean_depth(ctx, dest, now) {
    const rumble = makeNoise(ctx, dest, now, 120, 0.3, 0.35);
    const bubble = makeNoise(ctx, dest, now, 800, 8, 0.06);     // bubble pops
    const whale1 = makeOsc(ctx, dest, now, 140, 'sine', 0.12);
    const whale2 = makeOsc(ctx, dest, now, 142, 'sine', 0.12);  // slow beat
    const deep = makeOsc(ctx, dest, now, 25, 'sine', 0.3);      // abyss
    const current = makeNoise(ctx, dest, now, 300, 1, 0.15);
    return [rumble, bubble, whale1, whale2, deep, current];
  },

  // Power plant: 60Hz transformer hum + steam + machinery
  power_plant(ctx, dest, now) {
    const hum60 = makeOsc(ctx, dest, now, 60, 'sine', 0.4);
    const hum120 = makeOsc(ctx, dest, now, 120, 'sine', 0.2);   // 2nd harmonic
    const hum180 = makeOsc(ctx, dest, now, 180, 'sine', 0.08);  // 3rd harmonic
    const steam = makeNoise(ctx, dest, now, 2500, 2, 0.1);
    const machinery = makeNoise(ctx, dest, now, 500, 1, 0.15);
    const throb = makeLFO(ctx, dest, now, 0.5, 0.05);           // slow throb
    return [hum60, hum120, hum180, steam, machinery, throb];
  },

  // Arctic wind: howling bandpassed wind + ice creak
  arctic_wind(ctx, dest, now) {
    const wind1 = makeNoise(ctx, dest, now, 600, 3, 0.3);
    const wind2 = makeNoise(ctx, dest, now, 1500, 2, 0.2);
    const gust = makeNoise(ctx, dest, now, 300, 1, 0.15);       // low gusts
    const creak = makeOsc(ctx, dest, now, 2200, 'sine', 0.02);  // ice stress
    const creak2 = makeOsc(ctx, dest, now, 2203, 'sine', 0.02); // beating creak
    const sub = makeOsc(ctx, dest, now, 40, 'sine', 0.15);      // pressure
    return [wind1, wind2, gust, creak, creak2, sub];
  },

  // Coral reef: underwater bubbles + whale calls + gentle current
  coral_reef(ctx, dest, now) {
    const rumble = makeOsc(ctx, dest, now, 30, 'sine', 0.25);       // deep water
    const current = makeNoise(ctx, dest, now, 500, 1.5, 0.15);      // gentle current
    const bubbles = makeNoise(ctx, dest, now, 2000, 8, 0.08);       // bubble pops
    const bubbleLfo = makeLFO(ctx, dest, now, 0.3, 0.03);           // bubble rhythm
    const whale1 = makeOsc(ctx, dest, now, 120, 'sine', 0.1);       // whale call low
    const whale2 = makeOsc(ctx, dest, now, 180, 'sine', 0.06);      // whale call high
    const whaleBeat = makeOsc(ctx, dest, now, 121, 'sine', 0.08);   // slow beat with whale1
    const shimmer = makeNoise(ctx, dest, now, 4000, 4, 0.03);       // surface light shimmer
    return [rumble, current, bubbles, bubbleLfo, whale1, whale2, whaleBeat, shimmer];
  },

  // Thunderstorm: rain + wind gusts + distant thunder rumble
  thunderstorm(ctx, dest, now) {
    const rain = makeNoise(ctx, dest, now, 3000, 1.5, 0.3);         // continuous rain
    const rainLow = makeNoise(ctx, dest, now, 800, 0.8, 0.15);      // heavy rain body
    const wind = makeNoise(ctx, dest, now, 400, 2, 0.12);           // wind
    const windLfo = makeLFO(ctx, dest, now, 0.15, 0.06);            // wind gusts
    const thunder = makeOsc(ctx, dest, now, 40, 'sine', 0.2);       // rumble base
    const thunder2 = makeOsc(ctx, dest, now, 42, 'sine', 0.18);     // rumble beat
    const crackle = makeNoise(ctx, dest, now, 6000, 6, 0.03);       // lightning crackle
    const sub = makeOsc(ctx, dest, now, 22, 'sine', 0.15);          // sub-bass pressure
    return [rain, rainLow, wind, windLfo, thunder, thunder2, crackle, sub];
  },

  // Warp engine: antimatter core thrumming + plasma shimmer + containment pulses
  warp_engine(ctx, dest, now) {
    const core1 = makeOsc(ctx, dest, now, 40, 'sine', 0.4);         // antimatter drone
    const core2 = makeOsc(ctx, dest, now, 40.3, 'sine', 0.4);       // beating drone
    const plasma1 = makeOsc(ctx, dest, now, 220, 'sine', 0.08);     // plasma shimmer
    const plasma2 = makeOsc(ctx, dest, now, 220.8, 'sine', 0.08);   // detuned shimmer
    const crackle = makeNoise(ctx, dest, now, 6000, 6, 0.03);       // energy discharge
    const pulse = makeLFO(ctx, dest, now, 0.4, 0.08);               // containment field swell
    const sub = makeOsc(ctx, dest, now, 20, 'sine', 0.2);           // deep hull vibration
    const hum = makeOsc(ctx, dest, now, 80, 'triangle', 0.06);      // EPS conduit hum
    return [core1, core2, plasma1, plasma2, crackle, pulse, sub, hum];
  },

  // Mech hangar: hydraulic hiss + servo motors + pneumatic rhythm + PA buzz
  mech_hangar(ctx, dest, now) {
    const hiss = makeNoise(ctx, dest, now, 2500, 4, 0.12);          // hydraulic hiss
    const servo = makeOsc(ctx, dest, now, 80, 'sawtooth', 0.15);    // servo motor drone
    const servo2 = makeOsc(ctx, dest, now, 160, 'sawtooth', 0.06);  // servo harmonic
    const pneumatic = makeLFO(ctx, dest, now, 2.5, 0.06);           // pneumatic clank rhythm
    const pa = makeOsc(ctx, dest, now, 1200, 'sine', 0.02);         // distant PA buzz
    const creak = makeNoise(ctx, dest, now, 400, 2, 0.08);          // metal stress
    const sub = makeOsc(ctx, dest, now, 35, 'sine', 0.2);           // heavy machinery rumble
    return [hiss, servo, servo2, pneumatic, pa, creak, sub];
  },

  // Terraforming drone: ultra-slow processor hum + thin wind + seismic rumble
  terraforming_drone(ctx, dest, now) {
    const processor = makeOsc(ctx, dest, now, 30, 'sine', 0.35);    // atmo processor hum
    const processor2 = makeOsc(ctx, dest, now, 30.15, 'sine', 0.35);// detuned beating
    const wind = makeNoise(ctx, dest, now, 3000, 3, 0.08, 'highpass'); // thin Martian wind
    const seismic = makeLFO(ctx, dest, now, 0.08, 0.06);            // seismic rumble LFO
    const chime = makeOsc(ctx, dest, now, 2400, 'sine', 0.01);      // crystalline chime
    const chimeBeat = makeOsc(ctx, dest, now, 2401, 'sine', 0.01);  // slow chime beat
    const pressHiss = makeNoise(ctx, dest, now, 1500, 5, 0.04);     // pressure equalization
    return [processor, processor2, wind, seismic, chime, chimeBeat, pressHiss];
  },

  // Tavern hearth: crackling fire + room resonance + distant crowd + wind outside
  tavern_fire(ctx, dest, now) {
    const fire = makeNoise(ctx, dest, now, 3000, 3, 0.2);           // fire crackle
    const fireBody = makeNoise(ctx, dest, now, 800, 1.5, 0.12);     // warm fire body
    const fireLfo = makeLFO(ctx, dest, now, 0.7, 0.04);             // fire flicker rhythm
    const room = makeOsc(ctx, dest, now, 60, 'sine', 0.15);         // wooden room resonance
    const crowd = makeNoise(ctx, dest, now, 600, 2, 0.06);          // distant crowd murmur
    const wind = makeNoise(ctx, dest, now, 1800, 4, 0.04, 'highpass'); // wind outside
    const sub = makeOsc(ctx, dest, now, 45, 'sine', 0.1);           // hearth warmth
    return [fire, fireBody, fireLfo, room, crowd, wind, sub];
  },

  // Rocket launch: enormous thrust rumble + exhaust roar + radio crackle + vibration
  rocket_launch(ctx, dest, now) {
    const thrust1 = makeOsc(ctx, dest, now, 25, 'sine', 0.45);      // deep thrust
    const thrust2 = makeOsc(ctx, dest, now, 35, 'sine', 0.35);      // mid thrust
    const thrust3 = makeOsc(ctx, dest, now, 50, 'sine', 0.2);       // upper thrust
    const exhaust = makeNoise(ctx, dest, now, 1200, 0.8, 0.25);     // exhaust roar
    const radio = makeNoise(ctx, dest, now, 2500, 8, 0.04);         // radio comms crackle
    const tick = makeLFO(ctx, dest, now, 1.0, 0.03);                // countdown tick pulse
    const gantry = makeOsc(ctx, dest, now, 95, 'triangle', 0.06);   // gantry vibration
    const gantry2 = makeOsc(ctx, dest, now, 95.4, 'triangle', 0.06);// gantry beating
    return [thrust1, thrust2, thrust3, exhaust, radio, tick, gantry, gantry2];
  },

  // Submarine sonar: sonar ping cycle + hull groans + water flow + cavitation
  submarine_sonar(ctx, dest, now) {
    const ping = makeOsc(ctx, dest, now, 1200, 'sine', 0.04);       // sonar ping tone
    const pingLfo = makeLFO(ctx, dest, now, 0.25, 0.04);            // ~4s ping cycle
    const hull1 = makeOsc(ctx, dest, now, 55, 'sine', 0.2);         // hull compression
    const hull2 = makeOsc(ctx, dest, now, 56.2, 'sine', 0.2);       // hull groan beat
    const water = makeNoise(ctx, dest, now, 300, 1, 0.12);          // water current flow
    const cavitation = makeNoise(ctx, dest, now, 800, 3, 0.06);     // propeller cavitation
    const cavLfo = makeLFO(ctx, dest, now, 3, 0.03);                // cavitation pulse
    const deep = makeOsc(ctx, dest, now, 22, 'sine', 0.15);         // pressure silence bed
    return [ping, pingLfo, hull1, hull2, water, cavitation, cavLfo, deep];
  },

  // Wildfire crackle: roaring fire + embers + hot wind + helicopter rotors + radio
  wildfire_crackle(ctx, dest, now) {
    const roar = makeNoise(ctx, dest, now, 600, 1.5, 0.25);         // roaring fire mid
    const roarLfo = makeLFO(ctx, dest, now, 0.3, 0.08);             // fire intensity swell
    const embers = makeNoise(ctx, dest, now, 4000, 5, 0.08);        // crackling embers
    const wind = makeNoise(ctx, dest, now, 1200, 2, 0.12);          // hot wind gusts
    const heli = makeNoise(ctx, dest, now, 500, 3, 0.06);           // helicopter body
    const heliChop = makeLFO(ctx, dest, now, 8, 0.04);              // rotor chop
    const radio = makeNoise(ctx, dest, now, 2000, 10, 0.03);        // distant radio chatter
    const sub = makeOsc(ctx, dest, now, 35, 'sine', 0.18);          // fire rumble base
    return [roar, roarLfo, embers, wind, heli, heliChop, radio, sub];
  },

  // Hyperloop tube: vacuum resonance + pod whoosh + EM hum + station chime
  hyperloop_tube(ctx, dest, now) {
    const tube = makeOsc(ctx, dest, now, 55, 'sine', 0.3);          // vacuum tube resonance
    const tube2 = makeOsc(ctx, dest, now, 55.3, 'sine', 0.3);       // tube beating
    const em1 = makeOsc(ctx, dest, now, 110, 'sine', 0.08);         // EM harmonic
    const em2 = makeOsc(ctx, dest, now, 165, 'sine', 0.04);         // EM 3rd harmonic
    const whoosh = makeNoise(ctx, dest, now, 1500, 2, 0.08);        // pod pass-by body
    const whooshLfo = makeLFO(ctx, dest, now, 0.2, 0.05);           // pod pass-by rhythm
    const seal = makeNoise(ctx, dest, now, 5000, 6, 0.03, 'highpass'); // air seal hiss
    const chime = makeOsc(ctx, dest, now, 2093, 'sine', 0.01);      // station arrival chime
    return [tube, tube2, em1, em2, whoosh, whooshLfo, seal, chime];
  },

  // Lab clean room: HVAC white noise + fluorescent hum + centrifuge + laminar flow
  genetics_hum(ctx, dest, now) {
    const hvac = makeNoise(ctx, dest, now, 600, 0.5, 0.15);         // HVAC gentle wash
    const fluor = makeOsc(ctx, dest, now, 120, 'sine', 0.04);       // fluorescent light hum
    const fluor2 = makeOsc(ctx, dest, now, 240, 'sine', 0.015);     // fluorescent harmonic
    const centrifuge = makeOsc(ctx, dest, now, 340, 'sine', 0.03);  // centrifuge spin
    const centLfo = makeLFO(ctx, dest, now, 0.15, 0.02);            // centrifuge wobble
    const laminar = makeNoise(ctx, dest, now, 2000, 1.5, 0.08);     // laminar flow hood
    const beep = makeOsc(ctx, dest, now, 1800, 'sine', 0.005);      // PCR machine tone
    return [hvac, fluor, fluor2, centrifuge, centLfo, laminar, beep];
  },

  // Digital warfare: aggressive data stream + server fans + interference + alarm
  digital_warfare(ctx, dest, now) {
    const stream = makeNoise(ctx, dest, now, 2000, 2, 0.15);        // data stream
    const streamMod = makeLFO(ctx, dest, now, 7, 0.06);             // fast modulation
    const fans = makeNoise(ctx, dest, now, 800, 1.5, 0.12);         // server fan roar
    const crackle = makeNoise(ctx, dest, now, 6000, 8, 0.05);       // electrical interference
    const pulse = makeOsc(ctx, dest, now, 0.8, 'sine', 0.08);       // heartbeat-like bass LFO
    const sub = makeOsc(ctx, dest, now, 40, 'sine', 0.25);          // bass foundation
    const alarm = makeOsc(ctx, dest, now, 330, 'square', 0.02);     // klaxon undertone
    const alarm2 = makeOsc(ctx, dest, now, 332, 'square', 0.02);    // detuned alarm beat
    return [stream, streamMod, fans, crackle, pulse, sub, alarm, alarm2];
  },

  // Solar wind: ethereal plasma shimmer + magnetic field oscillations + particle stream
  solar_wind(ctx, dest, now) {
    const plasma1 = makeOsc(ctx, dest, now, 180, 'sine', 0.1);        // plasma shimmer
    const plasma2 = makeOsc(ctx, dest, now, 180.4, 'sine', 0.1);      // detuned shimmer beat
    const mag1 = makeOsc(ctx, dest, now, 0.12, 'sine', 0.06);         // slow magnetic oscillation
    const mag2 = makeOsc(ctx, dest, now, 0.07, 'sine', 0.04);         // secondary magnetic swell
    const particle = makeNoise(ctx, dest, now, 5000, 8, 0.04);        // particle stream hiss
    const drone = makeOsc(ctx, dest, now, 50, 'sine', 0.15);          // deep solar drone
    const drone2 = makeOsc(ctx, dest, now, 50.1, 'sine', 0.15);       // beating drone
    const crackle = makeNoise(ctx, dest, now, 8000, 10, 0.02);        // coronal discharge
    return [plasma1, plasma2, mag1, mag2, particle, drone, drone2, crackle];
  },

  // Train station: diesel idle + steel wheel + PA hum + crowd
  train_station(ctx, dest, now) {
    const diesel = makeOsc(ctx, dest, now, 42, 'sawtooth', 0.15);     // diesel engine idle
    const diesel2 = makeOsc(ctx, dest, now, 84, 'sawtooth', 0.06);    // diesel harmonic
    const dieselLfo = makeLFO(ctx, dest, now, 1.8, 0.04);             // engine throb
    const steel = makeNoise(ctx, dest, now, 3000, 6, 0.04);           // wheel squeal
    const pa = makeOsc(ctx, dest, now, 100, 'sine', 0.03);            // PA system hum
    const crowd = makeNoise(ctx, dest, now, 500, 1.5, 0.08);          // crowd murmur
    const sub = makeOsc(ctx, dest, now, 28, 'sine', 0.12);            // ground vibration
    const airbrake = makeNoise(ctx, dest, now, 1200, 3, 0.05);        // pneumatic hiss
    return [diesel, diesel2, dieselLfo, steel, pa, crowd, sub, airbrake];
  },

  // Jungle night: insect chorus + animal calls + leaf rustle + humidity drone
  jungle_night(ctx, dest, now) {
    const crickets = makeOsc(ctx, dest, now, 4200, 'sine', 0.04);     // cricket chirp tone
    const crickets2 = makeOsc(ctx, dest, now, 4800, 'sine', 0.03);    // second species
    const cricketLfo = makeLFO(ctx, dest, now, 6, 0.03);              // chirp rhythm
    const frog = makeOsc(ctx, dest, now, 320, 'sine', 0.03);          // tree frog croak
    const frogLfo = makeLFO(ctx, dest, now, 2.5, 0.02);               // croak rhythm
    const rustle = makeNoise(ctx, dest, now, 2000, 3, 0.08);          // leaf and canopy rustle
    const humidity = makeOsc(ctx, dest, now, 35, 'sine', 0.12);       // humid air drone
    const drip = makeNoise(ctx, dest, now, 800, 5, 0.03);             // canopy drip
    return [crickets, crickets2, cricketLfo, frog, frogLfo, rustle, humidity, drip];
  },

  // Steel mill: furnace roar + hammer rhythm + molten hiss + conveyor
  steel_mill(ctx, dest, now) {
    const furnace = makeNoise(ctx, dest, now, 300, 0.8, 0.25);        // furnace roar
    const furnace2 = makeOsc(ctx, dest, now, 45, 'sine', 0.3);        // furnace rumble
    const hammer = makeLFO(ctx, dest, now, 1.5, 0.08);                // heavy hammer rhythm
    const molten = makeNoise(ctx, dest, now, 2000, 2, 0.1);           // molten metal hiss
    const conveyor = makeOsc(ctx, dest, now, 75, 'sawtooth', 0.06);   // conveyor chain
    const convLfo = makeLFO(ctx, dest, now, 3.5, 0.03);               // chain rattle
    const steam = makeNoise(ctx, dest, now, 4000, 4, 0.06);           // steam vents
    const groan = makeOsc(ctx, dest, now, 140, 'sine', 0.04);         // steel stress groan
    return [furnace, furnace2, hammer, molten, conveyor, convLfo, steam, groan];
  },

  // Cathedral: organ drone + stone echo + choir pad + bell overtones
  cathedral(ctx, dest, now) {
    const organ1 = makeOsc(ctx, dest, now, 65.4, 'sine', 0.2);        // organ C2
    const organ2 = makeOsc(ctx, dest, now, 98.0, 'sine', 0.12);       // organ G2 (fifth)
    const organ3 = makeOsc(ctx, dest, now, 130.8, 'sine', 0.06);      // organ C3 (octave)
    const choir1 = makeOsc(ctx, dest, now, 262, 'sine', 0.04);        // choir pad C4
    const choir2 = makeOsc(ctx, dest, now, 262.6, 'sine', 0.04);      // choir beat
    const echo = makeNoise(ctx, dest, now, 400, 2, 0.05);             // stone room ambience
    const bell = makeOsc(ctx, dest, now, 523, 'sine', 0.015);         // bell overtone C5
    const sub = makeOsc(ctx, dest, now, 32.7, 'sine', 0.18);          // 16' pipe sub
    return [organ1, organ2, organ3, choir1, choir2, echo, bell, sub];
  },

  // Radio static: shortwave tuning + burst noise + carrier tone
  radio_static(ctx, dest, now) {
    const carrier = makeOsc(ctx, dest, now, 600, 'sine', 0.04);       // carrier tone
    const carrier2 = makeOsc(ctx, dest, now, 602, 'sine', 0.04);      // carrier beat
    const static1 = makeNoise(ctx, dest, now, 4000, 2, 0.12);         // broadband static
    const static2 = makeNoise(ctx, dest, now, 8000, 4, 0.06);         // high hiss
    const sweep = makeOsc(ctx, dest, now, 0.08, 'sine', 0.05);        // slow tuning sweep
    const burst = makeNoise(ctx, dest, now, 1500, 6, 0.08);           // burst noise
    const burstLfo = makeLFO(ctx, dest, now, 0.3, 0.05);              // burst rhythm
    const hum = makeOsc(ctx, dest, now, 50, 'sine', 0.06);            // mains hum bleed
    return [carrier, carrier2, static1, static2, sweep, burst, burstLfo, hum];
  },

  // Ice cave: dripping echo + frozen wind + crystal resonance + deep crack
  ice_cave(ctx, dest, now) {
    const wind = makeNoise(ctx, dest, now, 800, 3, 0.1);              // frozen wind
    const windLfo = makeLFO(ctx, dest, now, 0.1, 0.04);               // wind gusts
    const drip = makeNoise(ctx, dest, now, 2500, 10, 0.04);           // drip echo
    const dripLfo = makeLFO(ctx, dest, now, 0.5, 0.03);               // drip rhythm
    const crystal1 = makeOsc(ctx, dest, now, 2800, 'sine', 0.01);     // ice crystal ring
    const crystal2 = makeOsc(ctx, dest, now, 2802, 'sine', 0.01);     // crystal beat
    const crack = makeOsc(ctx, dest, now, 25, 'sine', 0.15);          // deep ice crack
    const echo = makeNoise(ctx, dest, now, 300, 1.5, 0.06);           // cave resonance
    return [wind, windLfo, drip, dripLfo, crystal1, crystal2, crack, echo];
  },

  // Circuit board: coil whine + clock pulse + data bus hum + capacitor charge
  circuit_board(ctx, dest, now) {
    const coil = makeOsc(ctx, dest, now, 8000, 'sine', 0.015);        // coil whine
    const coil2 = makeOsc(ctx, dest, now, 8003, 'sine', 0.015);       // coil beat
    const clock = makeOsc(ctx, dest, now, 1000, 'square', 0.008);     // clock pulse
    const clockLfo = makeLFO(ctx, dest, now, 0.5, 0.005);             // clock modulation
    const bus = makeOsc(ctx, dest, now, 150, 'sine', 0.06);           // data bus hum
    const bus2 = makeOsc(ctx, dest, now, 300, 'sine', 0.025);         // bus harmonic
    const cap = makeNoise(ctx, dest, now, 3000, 5, 0.04);             // capacitor charge whine
    const psu = makeOsc(ctx, dest, now, 60, 'sine', 0.08);            // PSU transformer hum
    return [coil, coil2, clock, clockLfo, bus, bus2, cap, psu];
  },

  // Volcano: deep rumble + gas vent hiss + magma bubble + seismic tremor
  volcano(ctx, dest, now) {
    const rumble1 = makeOsc(ctx, dest, now, 20, 'sine', 0.4);         // deep earth rumble
    const rumble2 = makeOsc(ctx, dest, now, 22, 'sine', 0.35);        // rumble beat
    const vent = makeNoise(ctx, dest, now, 1500, 2, 0.15);            // gas vent hiss
    const ventLfo = makeLFO(ctx, dest, now, 0.2, 0.06);               // vent surges
    const magma = makeNoise(ctx, dest, now, 400, 1, 0.1);             // magma bubbling
    const magmaLfo = makeLFO(ctx, dest, now, 0.6, 0.04);              // bubble rhythm
    const tremor = makeOsc(ctx, dest, now, 8, 'sine', 0.08);          // seismic tremor
    const crackle = makeNoise(ctx, dest, now, 5000, 6, 0.04);         // lava crackle
    return [rumble1, rumble2, vent, ventLfo, magma, magmaLfo, tremor, crackle];
  },

  // Haunted mansion: creaky wood + wind through gaps + organ drone + distant slam
  haunted(ctx, dest, now) {
    const wind = makeNoise(ctx, dest, now, 600, 4, 0.1);              // wind through gaps
    const windLfo = makeLFO(ctx, dest, now, 0.12, 0.05);              // wind gusts
    const creak = makeOsc(ctx, dest, now, 280, 'sine', 0.02);         // wood creak
    const creak2 = makeOsc(ctx, dest, now, 283, 'sine', 0.02);        // creak beat
    const organ = makeOsc(ctx, dest, now, 55, 'sine', 0.12);          // low organ drone
    const organ2 = makeOsc(ctx, dest, now, 82.5, 'sine', 0.06);       // organ fifth
    const rattle = makeNoise(ctx, dest, now, 3000, 8, 0.02);          // chain rattle
    const sub = makeOsc(ctx, dest, now, 28, 'sine', 0.15);            // deep dread drone
    return [wind, windLfo, creak, creak2, organ, organ2, rattle, sub];
  },

  // Agent den: ominous throbbing bass — deep sub-bass with slow menacing pulse
  agent_den(ctx, dest, now) {
    const sub1 = makeOsc(ctx, dest, now, 32, 'sine', 0.45);             // deep sub-bass
    const sub2 = makeOsc(ctx, dest, now, 32.4, 'sine', 0.45);           // beating against sub1
    const throb = makeLFO(ctx, dest, now, 0.4, 0.18);                   // slow throb modulation
    const mid = makeOsc(ctx, dest, now, 64, 'triangle', 0.12);          // low-mid menace
    const midLfo = makeLFO(ctx, dest, now, 0.15, 0.06);                 // slow mid swell
    const rumble = makeNoise(ctx, dest, now, 120, 0.8, 0.08);           // dark rumble texture
    const dread = makeOsc(ctx, dest, now, 48, 'sawtooth', 0.04);        // subtle dread edge
    const dreadLfo = makeLFO(ctx, dest, now, 0.08, 0.03);               // very slow menace drift
    return [sub1, sub2, throb, mid, midLfo, rumble, dread, dreadLfo];
  },
};

/** Ordered list of ambient preset names for UI */
const AMBIENT_PRESET_LIST = [
  { key: 'server_room',      label: 'Server Room' },
  { key: 'forest_rain',      label: 'Forest Rain' },
  { key: 'drone_approaching', label: 'Drone Approaching' },
  { key: 'bunker_countdown', label: 'Bunker Countdown' },
  { key: 'deep_space',       label: 'Deep Space' },
  { key: 'war_room',         label: 'War Room' },
  { key: 'ocean_depth',      label: 'Ocean Depth' },
  { key: 'power_plant',      label: 'Power Plant' },
  { key: 'arctic_wind',      label: 'Arctic Wind' },
  { key: 'coral_reef',       label: 'Coral Reef' },
  { key: 'thunderstorm',     label: 'Thunderstorm' },
  { key: 'warp_engine',      label: 'Warp Engine' },
  { key: 'mech_hangar',      label: 'Mech Hangar' },
  { key: 'terraforming_drone', label: 'Terraforming Drone' },
  { key: 'tavern_fire',      label: 'Tavern Hearth' },
  { key: 'rocket_launch',    label: 'Rocket Launch' },
  { key: 'submarine_sonar',  label: 'Submarine Sonar' },
  { key: 'wildfire_crackle', label: 'Wildfire Crackle' },
  { key: 'hyperloop_tube',   label: 'Hyperloop Tube' },
  { key: 'genetics_hum',     label: 'Lab Clean Room' },
  { key: 'digital_warfare',  label: 'Digital Warfare' },
  { key: 'solar_wind',       label: 'Solar Wind' },
  { key: 'train_station',    label: 'Train Station' },
  { key: 'jungle_night',     label: 'Jungle Night' },
  { key: 'steel_mill',       label: 'Steel Mill' },
  { key: 'cathedral',        label: 'Cathedral' },
  { key: 'radio_static',     label: 'Radio Static' },
  { key: 'ice_cave',         label: 'Ice Cave' },
  { key: 'circuit_board',    label: 'Circuit Board' },
  { key: 'volcano',          label: 'Volcano' },
  { key: 'haunted',          label: 'Haunted Mansion' },
  { key: 'agent_den',        label: 'Agent Den' },
];

const ambientAudio = new AudioEngine();
window.AMBIENT_PRESET_LIST = AMBIENT_PRESET_LIST;
window.ambientAudio = ambientAudio;
