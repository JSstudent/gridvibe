class StreamingResampler {
    constructor(inputRate, outputRate) {
        this.inputRate = inputRate;
        this.outputRate = outputRate;
        this.ratio = inputRate / outputRate;
        this.buffer = new Float32Array(8192);
        this.bufferLength = 0;
        this.position = 0;
    }

    append(samples) {
        this._ensureCapacity(this.bufferLength + samples.length);
        this.buffer.set(samples, this.bufferLength);
        this.bufferLength += samples.length;
    }

    _ensureCapacity(requiredLength) {
        if (requiredLength <= this.buffer.length) {
            return;
        }

        let nextLength = this.buffer.length;
        while (nextLength < requiredLength) {
            nextLength *= 2;
        }

        const nextBuffer = new Float32Array(nextLength);
        nextBuffer.set(this.buffer.subarray(0, this.bufferLength));
        this.buffer = nextBuffer;
    }

    _pruneConsumed(keepSamples = 0) {
        const pruneCount = Math.max(0, Math.floor(this.position) - keepSamples);
        if (pruneCount <= 0) {
            return;
        }

        if (pruneCount < this.bufferLength) {
            this.buffer.copyWithin(0, pruneCount, this.bufferLength);
        }
        this.bufferLength -= pruneCount;
        this.position -= pruneCount;
    }

    process() {
        if (this.inputRate === this.outputRate) {
            return this._copy();
        }
        if (this.inputRate > this.outputRate) {
            return this._downsample();
        }
        return this._upsample();
    }

    _copy() {
        const available = this.bufferLength - Math.floor(this.position);
        if (available <= 0) {
            return new Float32Array(0);
        }

        const output = new Float32Array(available);
        let outputIndex = 0;
        while (this.position + 1 <= this.bufferLength) {
            output[outputIndex++] = this.buffer[Math.floor(this.position)];
            this.position += 1;
        }
        this._pruneConsumed(0);
        return outputIndex === output.length ? output : output.subarray(0, outputIndex);
    }

    _downsample() {
        const estimated = Math.max(
            0,
            Math.floor((this.bufferLength - this.position) / this.ratio)
        );
        if (estimated <= 0) {
            return new Float32Array(0);
        }

        const output = new Float32Array(estimated);
        let outputIndex = 0;
        while (this.position + this.ratio <= this.bufferLength) {
            const intervalStart = this.position;
            const intervalEnd = intervalStart + this.ratio;
            let cursor = intervalStart;
            let weightedSum = 0;

            while (cursor < intervalEnd) {
                const sampleIndex = Math.floor(cursor);
                const sliceEnd = Math.min(intervalEnd, sampleIndex + 1);
                weightedSum += this.buffer[sampleIndex] * (sliceEnd - cursor);
                cursor = sliceEnd;
            }

            output[outputIndex++] = weightedSum / this.ratio;
            this.position = intervalEnd;
        }

        this._pruneConsumed(0);
        return outputIndex === output.length ? output : output.subarray(0, outputIndex);
    }

    _upsample() {
        const estimated = Math.max(
            0,
            Math.floor((this.bufferLength - this.position - 1) / this.ratio)
        );
        if (estimated <= 0) {
            return new Float32Array(0);
        }

        const output = new Float32Array(estimated);
        let outputIndex = 0;
        while (this.position + 1 < this.bufferLength) {
            const sampleIndex = Math.floor(this.position);
            const fraction = this.position - sampleIndex;
            const start = this.buffer[sampleIndex];
            const end = this.buffer[sampleIndex + 1];
            output[outputIndex++] = start + ((end - start) * fraction);
            this.position += this.ratio;
        }

        this._pruneConsumed(1);
        return outputIndex === output.length ? output : output.subarray(0, outputIndex);
    }
}

class GridVibeVoiceProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super();
        const processorOptions = options?.processorOptions || {};
        this.targetSampleRate = processorOptions.targetSampleRate || 16000;
        this.chunkSize = processorOptions.chunkSize || 640;
        this.outputChunk = new Int16Array(this.chunkSize);
        this.outputIndex = 0;
        this.resampler = new StreamingResampler(sampleRate, this.targetSampleRate);

        this.port.onmessage = event => {
            const message = event.data || {};
            if (message.type === 'flush') {
                this._flush(message.flushId);
            }
        };

        this.port.postMessage({
            type: 'format',
            sourceSampleRate: sampleRate,
            targetSampleRate: this.targetSampleRate,
            chunkSize: this.chunkSize
        });
    }

    process(inputs, outputs) {
        const outputChannels = outputs[0];
        if (outputChannels?.[0]) {
            outputChannels[0].fill(0);
        }

        const inputChannels = inputs[0];
        if (!inputChannels?.[0]?.length) {
            return true;
        }

        this.resampler.append(inputChannels[0]);
        const resampled = this.resampler.process();
        if (resampled.length > 0) {
            this._enqueue(resampled);
        }
        return true;
    }

    _enqueue(samples) {
        for (let index = 0; index < samples.length; index += 1) {
            const sample = Math.max(-1, Math.min(1, samples[index]));
            this.outputChunk[this.outputIndex++] = sample < 0
                ? sample * 0x8000
                : sample * 0x7FFF;

            if (this.outputIndex === this.chunkSize) {
                this._emitChunk(this.outputIndex);
            }
        }
    }

    _emitChunk(length) {
        const packet = new Int16Array(length);
        packet.set(this.outputChunk.subarray(0, length));
        this.outputIndex = 0;
        this.port.postMessage({
            type: 'audio',
            audio: packet.buffer
        }, [packet.buffer]);
    }

    _flush(flushId) {
        if (this.outputIndex > 0) {
            this._emitChunk(this.outputIndex);
        }
        this.port.postMessage({
            type: 'flush-complete',
            flushId
        });
    }
}

registerProcessor('gridvibe-voice-processor', GridVibeVoiceProcessor);

