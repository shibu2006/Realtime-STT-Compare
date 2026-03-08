/**
 * AudioWorklet processor for voice capture.
 * Replaces deprecated ScriptProcessorNode with AudioWorkletNode.
 * Handles resampling and PCM16 conversion, sends chunks to main thread via MessagePort.
 */
class VoiceCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const { resampleRatio = 1, targetChunkSize = 1024 } = options.processorOptions || {};
    this.resampleRatio = resampleRatio;
    this.targetChunkSize = targetChunkSize;
    this.audioBuffer = new Float32Array(0);
  }

  process(inputs, outputs) {
    const input = inputs[0];
    if (!input || !input.length) return true;

    const inputData = input[0];
    if (!inputData || inputData.length === 0) return true;

    const resampleRatio = this.resampleRatio;
    const targetChunkSize = this.targetChunkSize;

    // Resampling
    let resampledData;
    if (resampleRatio !== 1) {
      const outputLength = Math.floor(inputData.length * resampleRatio);
      resampledData = new Float32Array(outputLength);

      for (let i = 0; i < outputLength; i++) {
        const srcIndex = i / resampleRatio;
        const index = Math.floor(srcIndex);
        const frac = srcIndex - index;

        const sample1 = inputData[index] ?? 0;
        const sample2 = inputData[Math.min(inputData.length - 1, index + 1)] ?? 0;
        resampledData[i] = sample1 + frac * (sample2 - sample1);
      }
    } else {
      resampledData = inputData;
    }

    // Append to buffer
    const newBuffer = new Float32Array(this.audioBuffer.length + resampledData.length);
    newBuffer.set(this.audioBuffer);
    newBuffer.set(resampledData, this.audioBuffer.length);
    this.audioBuffer = newBuffer;

    // Emit chunks when we have enough samples
    while (this.audioBuffer.length >= targetChunkSize) {
      const chunk = this.audioBuffer.slice(0, targetChunkSize);
      this.audioBuffer = this.audioBuffer.slice(targetChunkSize);

      const pcm16Data = new Int16Array(chunk.length);
      for (let i = 0; i < chunk.length; i++) {
        const s = Math.max(-1, Math.min(1, chunk[i]));
        pcm16Data[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }

      this.port.postMessage({ type: 'audio', data: pcm16Data.buffer }, [pcm16Data.buffer]);
    }

    // Pass through to output (required by AudioWorklet)
    const output = outputs[0];
    if (output && output.length > 0 && output[0].length > 0) {
      const outChannel = output[0];
      const copyLength = Math.min(inputData.length, outChannel.length);
      outChannel.set(inputData.subarray(0, copyLength));
    }

    return true;
  }
}

registerProcessor('voice-capture-processor', VoiceCaptureProcessor);
