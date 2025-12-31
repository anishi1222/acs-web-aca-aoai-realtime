class PcmSenderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 24000;
    this.inRate = sampleRate; // AudioWorklet global sample rate
    this.phase = 0;
    this.out = new Float32Array(480); // 20ms @ 24kHz
    this.outLen = 0;
    this._levelTick = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const ch0 = input[0];

    // Generic (cheap) resampling to 24kHz based on actual input sample rate.
    // Uses phase accumulator + nearest-sample pick; good enough for PoC speech.
    const inc = this.targetRate / this.inRate;
    for (let i = 0; i < ch0.length; i++) {
      this.phase += inc;
      if (this.phase >= 1.0) {
        this.phase -= 1.0;
        this.out[this.outLen++] = ch0[i];
        if (this.outLen === this.out.length) {
          // Simple per-chunk AGC: if the signal is too quiet, boost it.
          let absMax = 0;
          for (let k = 0; k < this.out.length; k++) {
            const a = Math.abs(this.out[k]);
            if (a > absMax) absMax = a;
          }
          let gain = 1.0;
          if (absMax > 0 && absMax < 0.02) gain = 8.0;
          else if (absMax > 0 && absMax < 0.05) gain = 4.0;
          else if (absMax > 0 && absMax < 0.10) gain = 2.0;

          // Float32 -> PCM16 little-endian
          const pcm16 = new Int16Array(this.out.length);
          let peak = 0;
          for (let j = 0; j < this.out.length; j++) {
            const s = Math.max(-1, Math.min(1, this.out[j] * gain));
            pcm16[j] = (s < 0 ? s * 0x8000 : s * 0x7fff) | 0;
            const a = Math.abs(pcm16[j]);
            if (a > peak) peak = a;
          }
          // Send level occasionally (avoid spamming the main thread).
          this._levelTick++;
          if (this._levelTick % 5 === 0) {
            this.port.postMessage({ type: "level", peak });
          }
          this.port.postMessage({ type: "pcm", sampleRate: this.targetRate, data: pcm16 }, [pcm16.buffer]);
          this.outLen = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("pcm-sender", PcmSenderProcessor);
