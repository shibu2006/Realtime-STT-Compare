let isRecording = false;
let isInitializing = false; // Track if microphone/connection is initializing
let pendingStop = false; // Track if stop was requested during initialization
let socket;
let microphone;
let audioContext = null;
let audioProcessor = null;
let audioStream = null;
let pendingAudioChunks = []; // Buffer audio while waiting for connection
let connectionReady = false; // Track if backend connection is ready

// Connect to SocketIO on the same port as the web server
socket = io();

socket.on("connect", () => {
  console.log("Client: Connected to SocketIO server");
});

socket.on("disconnect", () => {
  console.log("Client: Disconnected from SocketIO server");
});

socket.on("connect_error", (error) => {
  console.error("Client: SocketIO connection error:", error);
});

socket.on("transcription_status", (data) => {
  console.log("Transcription status:", data);
  if (data.status === "error") {
    console.error("Transcription error:", data.message);
    alert(`Transcription error: ${data.message}`);

    // Reset state on error
    connectionReady = false;
    isTranscribing = false;
    stopRecordingImmediate(); // Force stop

  } else if (data.status === "started") {
    // Backend connection is ready - flush any buffered audio
    connectionReady = true;
    isTranscribing = true;
    console.log(`✅ Backend connection ready. Flushing ${pendingAudioChunks.length} buffered audio chunks...`);

    // Send all buffered audio chunks
    while (pendingAudioChunks.length > 0) {
      const chunk = pendingAudioChunks.shift();
      if (socket.connected) {
        socket.emit("audio_stream", chunk);
      }
    }
    console.log("✅ Buffered audio flushed");
  } else if (data.status === "stopped") {
    connectionReady = false;
    isTranscribing = false;
  }
});

socket.on("silence_timeout", (data) => {
  console.log("Silence timeout:", data.message);
  // Automatically stop recording when silence timeout is reached
  stopRecordingHandler(); // Use handler to update UI properly
  // Optionally show a notification to the user
  console.warn(data.message);
});

let currentTranscription = ""; // Store accumulated transcription
let isTranscribing = false; // Track if we're currently transcribing
let stopTimeout = null; // Timeout for delayed stop

socket.on("transcription_update", (data) => {
  console.log("✅ Received transcription_update event:", data);
  const searchInput = document.getElementById("searchInput");
  if (!searchInput) {
    console.error("❌ searchInput element not found!");
    return;
  }
  if (data && data.transcription !== undefined && data.transcription !== null) {
    const newTranscription = data.transcription.trim();

    if (newTranscription) {
      // Get current API to handle transcription differently
      const apiSelect = document.getElementById("apiSelect");
      const selectedAPI = apiSelect ? apiSelect.value : "Deepgram API";

      if (selectedAPI === "ElvenLabs ScribeV2") {
        currentTranscription = newTranscription;
      } else if (selectedAPI === "Azure OpenAI") {
        currentTranscription = newTranscription;
      } else {
        // Deepgram API: accumulate transcriptions
        if (currentTranscription === "") {
          currentTranscription = newTranscription;
        } else if (newTranscription.toLowerCase().startsWith(currentTranscription.toLowerCase())) {
          currentTranscription = newTranscription;
        } else {
          currentTranscription += " " + newTranscription;
        }
      }

      const oldValue = searchInput.value;
      searchInput.value = currentTranscription;

      // Also trigger input event to ensure any listeners are notified
      searchInput.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
});

async function getMicrophone(useWebAudio = false) {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        sampleRate: 24000,
        echoCancellation: true,
        noiseSuppression: true
      }
    });

    if (useWebAudio) {
      audioStream = stream;
      return { type: 'webaudio', stream: stream };
    } else {
      return { type: 'mediarecorder', recorder: new MediaRecorder(stream, { mimeType: "audio/webm" }) };
    }
  } catch (error) {
    console.error("Error accessing microphone:", error);
    throw error;
  }
}

async function openMicrophone(microphone, socket, useWebAudio = false) {
  // Check if stop was requested while we were getting the mic
  if (pendingStop) {
    console.log("🛑 Stop requested during mic init - aborting openMicrophone");
    return;
  }

  if (useWebAudio && microphone.type === 'webaudio') {
    return new Promise((resolve) => {
      try {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const actualSampleRate = audioContext.sampleRate;
        const source = audioContext.createMediaStreamSource(microphone.stream);
        const bufferSize = 2048;

        audioProcessor = audioContext.createScriptProcessor(bufferSize, 1, 1);
        const targetSampleRate = 24000;
        const resampleRatio = targetSampleRate / actualSampleRate;

        let audioBuffer = new Float32Array(0);
        const targetChunkSize = 1024;

        audioProcessor.onaudioprocess = (event) => {
          if (!isRecording && !isInitializing) return; // Guard clause

          const inputData = event.inputBuffer.getChannelData(0);

          // Resampling logic
          let resampledData;
          if (actualSampleRate !== targetSampleRate) {
            const outputLength = Math.floor(inputData.length * resampleRatio);
            resampledData = new Float32Array(outputLength);

            for (let i = 0; i < outputLength; i++) {
              const srcIndex = i / resampleRatio;
              const index = Math.floor(srcIndex);
              const frac = srcIndex - index;

              const sample1 = inputData[index] || 0;
              const sample2 = inputData[Math.min(inputData.length - 1, index + 1)] || 0;
              resampledData[i] = sample1 + frac * (sample2 - sample1);
            }
          } else {
            resampledData = inputData;
          }

          const newBuffer = new Float32Array(audioBuffer.length + resampledData.length);
          newBuffer.set(audioBuffer);
          newBuffer.set(resampledData, audioBuffer.length);
          audioBuffer = newBuffer;

          while (audioBuffer.length >= targetChunkSize) {
            const chunk = audioBuffer.slice(0, targetChunkSize);
            audioBuffer = audioBuffer.slice(targetChunkSize);

            const pcm16Data = new Int16Array(chunk.length);
            for (let i = 0; i < chunk.length; i++) {
              const s = Math.max(-1, Math.min(1, chunk[i]));
              pcm16Data[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }

            if (connectionReady) {
              socket.emit("audio_stream", pcm16Data.buffer);
            } else {
              pendingAudioChunks.push(pcm16Data.buffer);
              const maxChunks = 120;
              if (pendingAudioChunks.length > maxChunks) {
                pendingAudioChunks.shift();
              }
            }
          }
        };

        source.connect(audioProcessor);
        const gainNode = audioContext.createGain();
        gainNode.gain.value = 0;
        audioProcessor.connect(gainNode);
        gainNode.connect(audioContext.destination);

        console.log("Client: Web Audio API microphone opened");

        // IMPORTANT: We do NOT add 'recording' class here anymore. 
        // It is handled optimistically in startRecordingHandler.

        resolve();
      } catch (error) {
        console.error("Error setting up Web Audio API:", error);
        throw error;
      }
    });
  } else if (microphone.type === 'mediarecorder') {
    return new Promise((resolve) => {
      microphone.recorder.onstart = () => {
        console.log("Client: MediaRecorder microphone opened");
        // 'recording' class handled optimistically
        resolve();
      };

      microphone.recorder.ondataavailable = async (event) => {
        if (!isRecording && !isInitializing) return;
        if (event.data.size > 0) {
          const arrayBuffer = await event.data.arrayBuffer();
          if (socket.connected) {
            socket.emit("audio_stream", arrayBuffer);
          }
        }
      };
      microphone.recorder.start(1000); // 1-second chunks for Deepgram/other
    });
  } else {
    throw new Error("Unknown microphone type");
  }
}

async function startRecording() {
  if (isRecording || isInitializing) return;

  isInitializing = true;
  pendingStop = false;
  isRecording = true; // Set true immediately for logic check

  // Optimistic UI: Set recording state immediately
  document.body.classList.add("recording");
  const micButton = document.getElementById("micButton");
  const languageSelect = document.getElementById("languageSelect");
  if (micButton) {
    micButton.classList.add("recording");
    micButton.classList.add("pressed"); // Also add pressed for immediate feedback
  }
  if (languageSelect) {
    languageSelect.disabled = true;
  }

  isTranscribing = false;
  connectionReady = false;
  pendingAudioChunks = [];
  currentTranscription = "";

  const searchInput = document.getElementById("searchInput");
  if (searchInput) {
    searchInput.value = "";
  }

  const apiSelect = document.getElementById("apiSelect");
  const useWebAudio = apiSelect && (apiSelect.value === "Azure OpenAI" || apiSelect.value === "ElvenLabs ScribeV2");

  if (!useWebAudio) {
    connectionReady = true;
  }

  try {
    microphone = await getMicrophone(useWebAudio);

    // Check pendingStop *after* getting mic, before opening
    if (pendingStop) {
      console.log("🛑 Stop requested after getMicrophone - cleaning up");
      isInitializing = false;
      // Clean up the stream we just got
      if (microphone.stream) {
        microphone.stream.getTracks().forEach(t => t.stop());
      }
      stopRecordingImmediate();
      return;
    }

    console.log(`Client: Waiting to open microphone (${useWebAudio ? 'Web Audio API' : 'MediaRecorder'})`);
    await openMicrophone(microphone, socket, useWebAudio);

    isInitializing = false;

    // Check pendingStop *after* opening mic
    if (pendingStop) {
      console.log("🛑 Stop requested after openMicrophone - stopping now");
      stopRecordingImmediate();
    }

  } catch (error) {
    console.error("FAILED to start recording:", error);
    isInitializing = false;
    isRecording = false;
    alert("Could not access microphone. Please verify permissions.");
    stopRecordingImmediate(); // Revert UI
  }
}

// Internal function to clean up resources
function stopRecordingImmediate() {
  // Visual Cleanup
  document.body.classList.remove("recording");
  const micButton = document.getElementById("micButton");
  const languageSelect = document.getElementById("languageSelect");
  if (micButton) {
    micButton.classList.remove("recording");
    micButton.classList.remove("pressed");
    micButton.style.opacity = "";
    micButton.title = "Hold to speak (or press and hold spacebar)";
  }
  if (languageSelect) {
    languageSelect.disabled = false;
  }

  // Logic Cleanup
  if (microphone && microphone.type === 'mediarecorder') {
    if (microphone.recorder.state !== 'inactive') {
      microphone.recorder.stop();
    }
    if (microphone.stream) {
      microphone.stream.getTracks().forEach((track) => track.stop());
    }
  } else if (microphone && microphone.type === 'webaudio') {
    if (audioProcessor) {
      audioProcessor.disconnect();
      audioProcessor = null;
    }
    if (audioContext) {
      audioContext.close().catch(err => console.error("Error closing audio context:", err));
      audioContext = null;
    }
    if (audioStream) {
      audioStream.getTracks().forEach((track) => track.stop());
      audioStream = null;
    }
  }

  microphone = null;
  isRecording = false;
  connectionReady = false;
  pendingAudioChunks = [];
  console.log("Client: Microphone closed and resources cleaned");
}

let lastStartTime = 0;
let lastStopTime = 0;
const DEBOUNCE_DELAY = 200; // Increased debounce slightly

const startRecordingHandler = () => {
  const now = Date.now();
  if (now - lastStartTime < DEBOUNCE_DELAY) return;
  lastStartTime = now;

  if (isRecording || isInitializing) {
    // Already active, ignore
    return;
  }

  if (!socket.connected) {
    console.error("Socket not connected.");
    alert("Connection lost. Please refresh the page.");
    return;
  }

  // Send socket event
  const languageSelect = document.getElementById("languageSelect");
  const apiSelect = document.getElementById("apiSelect");
  const selectedLanguage = languageSelect ? languageSelect.value : "English";
  const selectedAPI = apiSelect ? apiSelect.value : "Deepgram API";

  socket.emit("toggle_transcription", {
    action: "start",
    language: selectedLanguage,
    api: selectedAPI
  });

  // Start recording audio
  startRecording().catch(console.error);
};

const stopRecordingHandler = () => {
  // If not recording and not initializing, nothing to do
  if (!isRecording && !isInitializing) return;

  // If initializing, set pendingStop flag so it stops as soon as it's ready
  if (isInitializing) {
    console.log("⚠️ Stop requested while initializing - setting pendingStop");
    pendingStop = true;
    // Don't return, we can still update UI partially if needed, 
    // but stopRecordingImmediate will handle full cleanup when called.
  }

  const now = Date.now();
  if (now - lastStopTime < DEBOUNCE_DELAY) {
    // If release is too quick, ensure we still process it after delay
    setTimeout(stopRecordingHandler, DEBOUNCE_DELAY);
    return;
  }
  lastStopTime = now;

  const micButton = document.getElementById("micButton");

  // Clear stop timeout if one exists
  if (stopTimeout) clearTimeout(stopTimeout);

  // Visual feedback for processing
  if (micButton) {
    micButton.style.opacity = "0.7";
    micButton.classList.add("processing"); // New class for spinning/processing state
  }

  console.log("🔄 Voice stopped - waiting for final transcription...");

  stopTimeout = setTimeout(() => {
    // Remove processing state
    if (micButton) micButton.classList.remove("processing");

    const apiSelect = document.getElementById("apiSelect");
    const selectedAPI = apiSelect ? apiSelect.value : "Deepgram API";

    if (socket.connected) {
      socket.emit("toggle_transcription", {
        action: "stop",
        api: selectedAPI
      });
    }

    stopRecordingImmediate();
    stopTimeout = null;
  }, 1500);
};

document.addEventListener("DOMContentLoaded", () => {
  const micButton = document.getElementById("micButton");
  const searchButton = document.getElementById("searchButton");
  const searchInput = document.getElementById("searchInput");
  const languageSelect = document.getElementById("languageSelect");
  const apiSelect = document.getElementById("apiSelect");

  if (!micButton || !searchButton || !searchInput || !languageSelect || !apiSelect) {
    console.error("Required DOM elements not found.");
    return;
  }

  const allLanguageOptions = Array.from(languageSelect.options).map(opt => ({
    value: opt.value,
    text: opt.text,
    selected: opt.selected
  }));

  function updateLanguageOptions() {
    const selectedAPI = apiSelect.value;
    languageSelect.innerHTML = '';

    if (selectedAPI === "Azure OpenAI" || selectedAPI === "ElvenLabs ScribeV2") {
      const autoOption = allLanguageOptions.find(opt => opt.value === "Auto");
      if (autoOption) {
        languageSelect.appendChild(new Option(autoOption.text, autoOption.value, true, true));
      }
    } else {
      allLanguageOptions.forEach(option => {
        if (option.value !== "Auto") {
          languageSelect.appendChild(new Option(option.text, option.value, option.selected, option.selected));
        }
      });
    }
  }

  apiSelect.addEventListener("change", updateLanguageOptions);
  updateLanguageOptions();

  // Pointer Events
  micButton.addEventListener("mousedown", (e) => {
    e.preventDefault();
    startRecordingHandler();
  });

  micButton.addEventListener("mouseup", (e) => {
    e.preventDefault();
    stopRecordingHandler();
  });

  micButton.addEventListener("mouseleave", () => {
    stopRecordingHandler();
  });

  // Touch Events
  micButton.addEventListener("touchstart", (e) => {
    e.preventDefault();
    startRecordingHandler();
  }, { passive: false });

  micButton.addEventListener("touchend", (e) => {
    e.preventDefault();
    stopRecordingHandler();
  }, { passive: false });

  // Keyboard Events
  let spacebarPressed = false;
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space" && document.activeElement !== searchInput && !spacebarPressed) {
      e.preventDefault();
      spacebarPressed = true;
      startRecordingHandler();
    }
  });

  document.addEventListener("keyup", (e) => {
    if (e.code === "Space" && spacebarPressed) {
      e.preventDefault();
      spacebarPressed = false;
      stopRecordingHandler();
    }
  });

  window.addEventListener("blur", () => {
    if (spacebarPressed) {
      spacebarPressed = false;
      stopRecordingHandler();
    }
  });

  searchButton.addEventListener("click", () => {
    const searchQuery = searchInput.value.trim();
    if (searchQuery) {
      console.log("Searching for:", searchQuery);
    }
  });

  searchInput.addEventListener("keypress", (e) => {
    if (e.key === "Enter") {
      searchButton.click();
    }
  });
});
