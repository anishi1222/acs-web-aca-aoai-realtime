class PcmRingPlayer extends AudioWorkletProcessor {
  constructor() {
    super();

    // Ring buffer sized in samples at the AudioWorklet sampleRate.
    // 2 seconds buffer is a good compromise.
    this._capacity = Math.max(1, Math.floor(sampleRate * 2));
    this._ring = new Float32Array(this._capacity);
    this._read = 0;
    this._write = 0;
    this._available = 0;

    this._lastStatAt = 0;

    // Debug counters to detect random clicks cause (underrun vs overflow).
    this._underrunCount = 0;
    this._droppedNewSamples = 0;

    // De-click (avoid pops when audio starts/stops/clears).
    this._rampSamples = Math.max(32, Math.floor(sampleRate * 0.003)); // ~3ms
    this._fadeOutRemaining = 0;
    this._fadeInRemaining = 0;
    this._lastOut = 0;
    this._inSilence = true;
    this._clearAfterFade = false;

    this.port.onmessage = (e) => {
      const msg = e.data || {};
      const type = msg.type;
      if (type === "clear") {
        // Ramp out to 0 to avoid a click, then clear the buffer.
        if (!this._inSilence) {
          this._fadeOutRemaining = this._rampSamples;
          this._clearAfterFade = true;
        } else {
          this._read = 0;
          this._write = 0;
          this._available = 0;
          this._lastOut = 0;
        }
        return;
      }
      if (type === "push_f32") {
        const arr = msg.data;
        if (!arr || !arr.length) return;
        this._push(arr);
        return;
      }
    };
  }

  _push(arr) {
    // On overflow, drop *newest* samples (truncate input) to preserve timeline.
    // Dropping oldest creates audible time compression/warping.
    const need = arr.length;
    if (need >= this._capacity) {
      // Keep only the tail that fits.
      this._droppedNewSamples += need - this._capacity;
      arr = arr.subarray(need - this._capacity);
    }

    const room = this._capacity - this._available;
    if (room <= 0) {
      this._droppedNewSamples += arr.length;
      return;
    }
    if (arr.length > room) {
      this._droppedNewSamples += arr.length - room;
      arr = arr.subarray(0, room);
    }

    let offset = 0;
    while (offset < arr.length) {
      const spaceToEnd = this._capacity - this._write;
      const toCopy = Math.min(spaceToEnd, arr.length - offset);
      this._ring.set(arr.subarray(offset, offset + toCopy), this._write);
      this._write = (this._write + toCopy) % this._capacity;
      this._available += toCopy;
      offset += toCopy;
    }
  }

  process(_inputs, outputs) {
    const out = outputs[0];
    if (!out || out.length === 0) return true;

    const ch0 = out[0];
    const frames = ch0.length;

    // Fill output with available samples; fade on start/stop to avoid clicks.
    for (let i = 0; i < frames; i++) {
      // Fade-out has priority.
      if (this._fadeOutRemaining > 0) {
        const g = this._fadeOutRemaining / this._rampSamples;
        const v = this._lastOut * g;
        ch0[i] = v;
        this._lastOut = v;
        this._fadeOutRemaining -= 1;
        if (this._fadeOutRemaining <= 0) {
          this._lastOut = 0;
          this._inSilence = true;
          if (this._clearAfterFade) {
            this._clearAfterFade = false;
            this._read = 0;
            this._write = 0;
            this._available = 0;
          }
        }
        continue;
      }

      if (this._available > 0) {
        // If we were silent and audio resumes, fade in.
        if (this._inSilence && this._fadeInRemaining === 0) {
          this._fadeInRemaining = this._rampSamples;
          this._inSilence = false;
        }

        let v = this._ring[this._read];
        this._read = (this._read + 1) % this._capacity;
        this._available -= 1;

        if (this._fadeInRemaining > 0) {
          const g = 1 - this._fadeInRemaining / this._rampSamples;
          v = v * g;
          this._fadeInRemaining -= 1;
        }

        ch0[i] = v;
        this._lastOut = v;
      } else {
        // Underrun: ramp out to 0 once.
        if (!this._inSilence) {
          this._underrunCount += 1;
          this._fadeOutRemaining = this._rampSamples;
          this._clearAfterFade = false;
          const g = this._fadeOutRemaining / this._rampSamples;
          const v = this._lastOut * g;
          ch0[i] = v;
          this._lastOut = v;
          this._fadeOutRemaining -= 1;
        } else {
          ch0[i] = 0;
          this._lastOut = 0;
        }
      }
    }

    // Mirror to other channels if any.
    for (let c = 1; c < out.length; c++) {
      out[c].set(ch0);
    }

    // Periodic stats (2Hz) for UI/debug/adaptive feeding.
    const now = currentTime;
    if (now - this._lastStatAt > 0.1) {
      this._lastStatAt = now;
      this.port.postMessage({
        type: "stats",
        bufferedSamples: this._available,
        sampleRate,
        underrunCount: this._underrunCount,
        droppedNewSamples: this._droppedNewSamples,
      });
    }

    return true;
  }
}

registerProcessor("pcm-ring-player", PcmRingPlayer);
