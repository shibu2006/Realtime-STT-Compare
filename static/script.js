let isRecording = false;
let isInitializing = false; // Track if microphone/connection is initializing
let pendingStop = false; // Track if stop was requested during initialization
let waitingForFinalTranscription = false; // Track if we're waiting for transcription after button release
let socket;
let microphone;
let audioContext = null;
let audioProcessor = null;
let audioStream = null;
let pendingAudioChunks = []; // Buffer audio while waiting for connection
let connectionReady = false; // Track if backend connection is ready
let speechAnalyser = null; // AnalyserNode for volume detection
let speechTimeout = null; // Timer for service timeout
let lastSpeechTime = 0; // Timestamp of last detected speech
let lastTranscriptionTime = 0; // Timestamp of last received transcription
let speechDetectedInSession = false; // Flag to track if user spoke in current session
let reconnectAttempts = 0; // Track reconnection attempts for auto-retry
let awaitingReconnectionResult = false; // Track if we're waiting for reconnection to complete
let reconnectionTimeoutId = null; // Timeout to force stop if reconnection doesn't succeed
const RECONNECTION_TIMEOUT_MS = 5000; // 5 seconds max wait for reconnection result

const SPEECH_THRESHOLD = 0.02; // Volume threshold to consider as speech (0.0 to 1.0)
const SERVICE_TIMEOUT_MS = 5000; // 5 seconds timeout for Azure OpenAI (needs more time than Deepgram)
const MAX_RECONNECT_ATTEMPTS = 3; // Maximum automatic reconnection attempts per session

// Recent Transcriptions - localStorage key and max items
const RECENT_TRANSCRIPTIONS_KEY = 'voiceTranscribe_recentTranscriptions';
const MAX_RECENT_ITEMS = 10;

// Load recent transcriptions from localStorage
function getRecentTranscriptions() {
  try {
    const stored = localStorage.getItem(RECENT_TRANSCRIPTIONS_KEY);
    if (!stored) return [];

    const parsed = JSON.parse(stored);

    // Migration: Convert old string array to object array
    if (Array.isArray(parsed) && parsed.length > 0 && typeof parsed[0] === 'string') {
      return parsed.map(text => ({ text, provider: 'Deepgram API' })); // Default to Deepgram for old items
    }

    return parsed;
  } catch (e) {
    console.error('Error loading recent transcriptions:', e);
    return [];
  }
}

// Save recent transcriptions to localStorage
function saveRecentTranscriptions(items) {
  try {
    localStorage.setItem(RECENT_TRANSCRIPTIONS_KEY, JSON.stringify(items));
  } catch (e) {
    console.error('Error saving recent transcriptions:', e);
  }
}

// Add a new transcription to recent list
function addRecentTranscription(text, provider) {
  if (!text || text.trim().length === 0) return;

  const trimmedText = text.trim();
  const providerName = provider || 'Unknown';

  let recent = getRecentTranscriptions();

  // Remove duplicate if exists (checking both text and provider, or just text? 
  // User might want to try same text with different provider. 
  // Let's filter by text only to avoid cluttering with same text multiple times, 
  // but maybe we update the provider? Let's keep it simple: unique by text).
  recent = recent.filter(item => item.text.toLowerCase() !== trimmedText.toLowerCase());

  // Add to beginning
  recent.unshift({ text: trimmedText, provider: providerName });

  // Keep only max items
  if (recent.length > MAX_RECENT_ITEMS) {
    recent = recent.slice(0, MAX_RECENT_ITEMS);
  }

  saveRecentTranscriptions(recent);
  renderRecentTranscriptions();
}

// Clear all recent transcriptions
function clearRecentTranscriptions() {
  saveRecentTranscriptions([]);
  renderRecentTranscriptions();
}

// Render recent transcriptions in the UI
// Delete a specific transcription from recent list
function deleteRecentTranscription(index) {
  let recent = getRecentTranscriptions();
  if (index >= 0 && index < recent.length) {
    recent.splice(index, 1);
    saveRecentTranscriptions(recent);
    renderRecentTranscriptions();
  }
}

// Render recent transcriptions in the UI
function renderRecentTranscriptions() {
  const container = document.getElementById('recentItems');
  const section = document.getElementById('recentSection');

  if (!container || !section) return;

  const recent = getRecentTranscriptions();

  if (recent.length === 0) {
    section.style.display = 'none';
    return;
  }

  section.style.display = 'block';
  container.innerHTML = '';

  recent.forEach((item, index) => {
    // Main container
    const wrapper = document.createElement('div');
    wrapper.className = 'recent-item-wrapper';

    const button = document.createElement('div');
    button.className = 'recent-item'; // functionality moved to click listener on wrapper or button? 
    // Let's keep button as a div to avoid nested button issues if we add a delete button inside.
    // Actually, making the whole thing a div and handling clicks is easier.

    // Create text span
    const textSpan = document.createElement('span');
    textSpan.textContent = `"${item.text}"`;

    // Create provider badge
    const badge = document.createElement('span');
    badge.className = 'provider-badge';
    badge.textContent = item.provider;

    // Style badge based on provider
    if (item.provider === 'Deepgram API') badge.classList.add('badge-deepgram');
    else if (item.provider === 'Azure OpenAI') badge.classList.add('badge-azure');
    else if (item.provider === 'ElevenLabs ScribeV2' || item.provider === 'ElvenLabs ScribeV2') badge.classList.add('badge-elevenlabs');

    // Create delete button
    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'recent-item-delete';
    deleteBtn.innerHTML = '<i class="fas fa-times"></i>';
    deleteBtn.title = "Remove item";
    deleteBtn.onclick = (e) => {
      e.stopPropagation();
      deleteRecentTranscription(index);
    };

    button.appendChild(textSpan);
    button.appendChild(badge);
    button.appendChild(deleteBtn);

    button.addEventListener('click', () => {
      const searchInput = document.getElementById('searchInput');
      if (searchInput) {
        searchInput.value = item.text;
        searchInput.focus();
      }
    });

    container.appendChild(button);
  });
}

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
    showStatusMessage(`Transcription error: ${data.message}`);

    // Reset state on error
    connectionReady = false;
    isTranscribing = false;
    reconnectAttempts = 0; // Reset reconnect counter
    awaitingReconnectionResult = false; // Reset reconnection flag
    if (reconnectionTimeoutId) {
      clearTimeout(reconnectionTimeoutId);
      reconnectionTimeoutId = null;
    }
    stopRecordingImmediate(); // Force stop

  } else if (data.status === "retrying") {
    // Show retry message seamlessly - don't stop recording, just inform user
    console.log("🔄 Retrying connection:", data.message);
    showStatusMessage(data.message);
    // Keep trying - don't reset state, connection is being re-established

  } else if (data.status === "processing") {
    // Backend is processing audio (e.g., Azure OpenAI received conversation.item.created)
    // Reset the transcription time to prevent premature timeout/reconnect
    lastTranscriptionTime = Date.now();
    console.log("🔄 Backend processing audio - reset timeout");
    // Don't show any message, just silently reset the timeout

  } else if (data.status === "started") {
    // Backend connection is ready - flush any buffered audio
    connectionReady = true;
    isTranscribing = true;
    reconnectAttempts = 0; // Reset reconnect counter on success
    hideStatusMessage(); // Clear any retry messages

    // Clear any pending stop timeout since we successfully reconnected
    if (stopTimeout) {
      clearTimeout(stopTimeout);
      stopTimeout = null;
    }

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
  showStatusMessage(data.message);
});

let currentTranscription = ""; // Store accumulated transcription
let isTranscribing = false; // Track if we're currently transcribing
let stopTimeout = null; // Timeout for delayed stop

socket.on("transcription_update", (data) => {
  // Reset service timeout whenever we get data
  lastTranscriptionTime = Date.now();
  hideStatusMessage();

  const searchInput = document.getElementById("searchInput");
  if (!searchInput) return;

  if (data && data.transcription !== undefined && data.transcription !== null) {
    const newTranscription = data.transcription.trim();

    if (newTranscription) {
      // Get current API to handle transcription differently
      const apiSelect = document.getElementById("apiSelect");
      const selectedAPI = apiSelect ? apiSelect.value : "Deepgram API";

      if (selectedAPI === "ElevenLabs ScribeV2") {
        // ElevenLabs backend sends already-accumulated transcripts
        // (handles pauses/segments server-side to combine into single response)
        currentTranscription = newTranscription;
      } else if (selectedAPI === "Azure OpenAI") {
        // Azure OpenAI backend sends already-accumulated transcripts
        // (handles conversation items server-side to combine into single response)
        currentTranscription = newTranscription;
      } else {
        // Deepgram API: accumulate transcriptions client-side
        if (currentTranscription === "") {
          currentTranscription = newTranscription;
        } else if (newTranscription.toLowerCase().startsWith(currentTranscription.toLowerCase())) {
          currentTranscription = newTranscription;
        } else {
          currentTranscription += " " + newTranscription;
        }
      }

      searchInput.value = currentTranscription;
      searchInput.dispatchEvent(new Event('input', { bubbles: true }));

      // If user released the button and we're waiting for final transcription, stop now
      if (waitingForFinalTranscription && !awaitingReconnectionResult) {
        console.log("✅ Transcription received after button release - stopping immediately");
        waitingForFinalTranscription = false;
        
        // Clear the pending timeout since we got transcription
        if (stopTimeout) {
          clearTimeout(stopTimeout);
          stopTimeout = null;
        }
        
        // Save to recent transcriptions
        addRecentTranscription(currentTranscription, selectedAPI);
        
        // Stop the transcription and clean up
        if (socket.connected) {
          socket.emit("toggle_transcription", {
            action: "stop",
            api: selectedAPI
          });
        }
        
        stopRecordingImmediate();
        return;
      }

      // If we were awaiting reconnection result and got transcription, stop recording now
      if (awaitingReconnectionResult) {
        console.log("✅ Transcription received after reconnection - stopping recording");
        awaitingReconnectionResult = false;
        waitingForFinalTranscription = false;
        reconnectAttempts = 0;
        if (reconnectionTimeoutId) {
          clearTimeout(reconnectionTimeoutId);
          reconnectionTimeoutId = null;
        }

        // Save to recent transcriptions
        addRecentTranscription(currentTranscription, selectedAPI);

        // Stop the transcription and clean up
        if (socket.connected) {
          socket.emit("toggle_transcription", {
            action: "stop",
            api: selectedAPI
          });
        }

        stopRecordingImmediate();
      }
    }
  }
});

function showStatusMessage(msg) {
  const el = document.getElementById('statusMessage');
  if (el) {
    el.innerText = msg;
    el.classList.add('visible');
  }
}

function hideStatusMessage() {
  const el = document.getElementById('statusMessage');
  if (el) {
    el.classList.remove('visible');
    // Clear text after transition
    setTimeout(() => {
      if (!el.classList.contains('visible')) el.innerText = '';
    }, 300);
  }
}

// Function to check audio level (removed automatic timeout trigger)
function monitorAudioLevel() {
  if (!isRecording || !speechAnalyser) return;

  const dataArray = new Uint8Array(speechAnalyser.frequencyBinCount);
  speechAnalyser.getByteTimeDomainData(dataArray);

  let sum = 0;
  for (let i = 0; i < dataArray.length; i++) {
    const x = (dataArray[i] - 128) / 128.0;
    sum += x * x;
  }
  const rms = Math.sqrt(sum / dataArray.length);

  if (rms > SPEECH_THRESHOLD) {
    lastSpeechTime = Date.now();
    speechDetectedInSession = true;
  }
}

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

    // START AUDIO ANALYSIS FOR TIMEOUT LOGIC
    // We attach analysis to the stream regardless of recording method
    try {
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const source = audioCtx.createMediaStreamSource(stream);
      speechAnalyser = audioCtx.createAnalyser();
      speechAnalyser.fftSize = 256;
      source.connect(speechAnalyser);

      // Start monitoring loop - only tracks speech time now, doesn't trigger alerts
      speechTimeout = setInterval(monitorAudioLevel, 100);
    } catch (e) {
      console.error("Failed to setup audio analysis:", e);
    }

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
  isRecording = true;

  // Reset Tracking Variables
  lastSpeechTime = 0;
  lastTranscriptionTime = Date.now(); // Assume we start "fresh"
  speechDetectedInSession = false;
  hideStatusMessage();

  // Optimistic UI
  document.body.classList.add("recording");
  const micButton = document.getElementById("micButton");
  const languageSelect = document.getElementById("languageSelect");
  if (micButton) {
    micButton.classList.add("recording");
    micButton.classList.add("pressed");
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
  const useWebAudio = apiSelect && (apiSelect.value === "Azure OpenAI" || apiSelect.value === "ElevenLabs ScribeV2");

  if (!useWebAudio) {
    connectionReady = true;
  }

  try {
    microphone = await getMicrophone(useWebAudio);

    if (pendingStop) {
      console.log("🛑 Stop requested after getMicrophone - cleaning up");
      isInitializing = false;
      cleanupMicrophone(); // Helper for stream cleanup
      stopRecordingImmediate();
      return;
    }

    console.log(`Client: Waiting to open microphone (${useWebAudio ? 'Web Audio API' : 'MediaRecorder'})`);
    await openMicrophone(microphone, socket, useWebAudio);

    isInitializing = false;

    if (pendingStop) {
      console.log("🛑 Stop requested after openMicrophone - stopping now");
      stopRecordingImmediate();
    }

  } catch (error) {
    console.error("FAILED to start recording:", error);
    isInitializing = false;
    isRecording = false;
    alert("Could not access microphone. Please verify permissions.");
    stopRecordingImmediate();
  }
}

function cleanupMicrophone() {
  if (microphone && microphone.stream) {
    microphone.stream.getTracks().forEach(t => t.stop());
  }
  if (speechTimeout) {
    clearInterval(speechTimeout);
    speechTimeout = null;
  }
  if (speechAnalyser) {
    // speechAnalyser is node, context clean up happens elsewhere generally, 
    // but removing reference is enough
    speechAnalyser = null;
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
    micButton.classList.remove("processing");
    micButton.style.opacity = "";
    micButton.title = "Hold to speak (or press and hold spacebar)";
  }
  if (languageSelect) {
    languageSelect.disabled = false;
  }

  hideStatusMessage(); // Hide any errors on stop
  cleanupMicrophone(); // Clear intervals and analysers

  // Clear reconnection state
  if (reconnectionTimeoutId) {
    clearTimeout(reconnectionTimeoutId);
    reconnectionTimeoutId = null;
  }
  awaitingReconnectionResult = false;
  waitingForFinalTranscription = false;

  // Logic Cleanup
  if (microphone && microphone.type === 'mediarecorder') {
    if (microphone.recorder.state !== 'inactive') {
      microphone.recorder.stop();
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
      // audioStream tracks stopped in cleanupMicrophone already but safe to double check if needed
      // but here we just null it
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
const DEBOUNCE_DELAY = 200;

const startRecordingHandler = () => {
  const now = Date.now();
  if (now - lastStartTime < DEBOUNCE_DELAY) return;
  lastStartTime = now;

  if (isRecording || isInitializing) return;

  if (!socket.connected) {
    console.error("Socket not connected.");
    alert("Connection lost. Please refresh the page.");
    return;
  }

  const languageSelect = document.getElementById("languageSelect");
  const apiSelect = document.getElementById("apiSelect");
  const selectedLanguage = languageSelect ? languageSelect.value : "English";
  const selectedAPI = apiSelect ? apiSelect.value : "Deepgram API";

  socket.emit("toggle_transcription", {
    action: "start",
    language: selectedLanguage,
    api: selectedAPI
  });

  startRecording().catch(console.error);
};

let timeoutCheckId = null; // Track the timeout check separately

const stopRecordingHandler = () => {
  if (!isRecording && !isInitializing) return;

  if (isInitializing) {
    console.log("⚠️ Stop requested while initializing - setting pendingStop");
    pendingStop = true;
  }

  const now = Date.now();
  if (now - lastStopTime < DEBOUNCE_DELAY) {
    setTimeout(stopRecordingHandler, DEBOUNCE_DELAY);
    return;
  }
  lastStopTime = now;

  const micButton = document.getElementById("micButton");

  // Clear any pending timeouts
  if (stopTimeout) clearTimeout(stopTimeout);
  if (timeoutCheckId) clearTimeout(timeoutCheckId);

  if (micButton) {
    micButton.style.opacity = "0.7";
    micButton.classList.add("processing");
  }

  console.log("🔄 Voice stopped - waiting for final transcription...");
  
  // Set flag so transcription_update handler can stop immediately when transcription arrives
  waitingForFinalTranscription = true;

  // Main stop timeout - this is a fallback if transcription doesn't arrive
  // The transcription_update handler will stop immediately when transcription arrives
  stopTimeout = setTimeout(() => {
    if (micButton) micButton.classList.remove("processing");

    const apiSelect = document.getElementById("apiSelect");
    const languageSelect = document.getElementById("languageSelect");
    const selectedAPI = apiSelect ? apiSelect.value : "Deepgram API";
    const selectedLanguage = languageSelect ? languageSelect.value : "English";

    // Check for timeout ONLY if:
    // 1. User spoke in this session
    // 2. Speech was detected recently (within 2s of button release)
    // 3. No transcription was received AT ALL, or the accumulated text is empty
    // This avoids false positives when transcription is just slow but arrives eventually
    const hasValidTranscription = currentTranscription && currentTranscription.trim().length > 0;
    const speechWasRecent = (lastStopTime - lastSpeechTime) < 2000;

    if (speechDetectedInSession && speechWasRecent && !hasValidTranscription) {
      const timeSinceLastTranscription = Date.now() - lastTranscriptionTime;
      if (timeSinceLastTranscription > SERVICE_TIMEOUT_MS) {
        console.warn("⚠️ Service timeout detected - no transcription received!");

        // Attempt automatic reconnection if we haven't exceeded max attempts
        if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
          reconnectAttempts++;
          awaitingReconnectionResult = true; // Flag that we're waiting for reconnection
          console.log(`🔄 Attempting automatic reconnection (${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})...`);
          showStatusMessage(`Reconnecting... (attempt ${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})`);

          // Trigger reconnection on the backend
          if (socket.connected) {
            socket.emit("reconnect_transcription", {
              api: selectedAPI,
              language: selectedLanguage
            });
          }

          // Set a timeout to force stop if reconnection doesn't succeed
          if (reconnectionTimeoutId) {
            clearTimeout(reconnectionTimeoutId);
          }
          reconnectionTimeoutId = setTimeout(() => {
            if (awaitingReconnectionResult && isRecording) {
              console.error("⏱️ Reconnection timeout - forcing stop");
              showStatusMessage("Connection timed out. Please try again.");
              awaitingReconnectionResult = false;
              waitingForFinalTranscription = false;
              reconnectAttempts = 0;

              // Stop the transcription and clean up
              if (socket.connected) {
                socket.emit("toggle_transcription", {
                  action: "stop",
                  api: selectedAPI
                });
              }
              stopRecordingImmediate();
            }
            reconnectionTimeoutId = null;
          }, RECONNECTION_TIMEOUT_MS);

          // Don't stop recording yet, wait for reconnection result
          return;
        } else {
          // Max reconnection attempts reached
          console.error("❌ Max reconnection attempts reached. Please try again.");
          showStatusMessage("Connection failed. Please try again.");
          reconnectAttempts = 0; // Reset for next session
          awaitingReconnectionResult = false; // Reset flag
          waitingForFinalTranscription = false;
        }
      }
    } else {
      // Reset reconnect attempts on successful transcription
      reconnectAttempts = 0;
      awaitingReconnectionResult = false;
    }

    // Save transcription to recent list before stopping
    if (hasValidTranscription) {
      addRecentTranscription(currentTranscription, selectedAPI);
    }

    waitingForFinalTranscription = false;

    if (socket.connected) {
      socket.emit("toggle_transcription", {
        action: "stop",
        api: selectedAPI
      });
    }

    stopRecordingImmediate();
    stopTimeout = null;
    timeoutCheckId = null;
  }, SERVICE_TIMEOUT_MS);
};

document.addEventListener("DOMContentLoaded", () => {
  const micButton = document.getElementById("micButton");
  const searchButton = document.getElementById("searchButton");
  const searchInput = document.getElementById("searchInput");
  const languageSelect = document.getElementById("languageSelect");
  const apiSelect = document.getElementById("apiSelect");
  const customPlaceholder = document.getElementById("customPlaceholder");

  if (!micButton || !searchButton || !searchInput || !languageSelect || !apiSelect) {
    console.error("Required DOM elements not found.");
    return;
  }

  // Custom placeholder handling
  function updatePlaceholderVisibility() {
    if (customPlaceholder) {
      if (searchInput.value.length > 0 || document.activeElement === searchInput) {
        customPlaceholder.classList.add("hidden");
      } else {
        customPlaceholder.classList.remove("hidden");
      }
    }
  }

  searchInput.addEventListener("focus", updatePlaceholderVisibility);
  searchInput.addEventListener("blur", updatePlaceholderVisibility);
  searchInput.addEventListener("input", updatePlaceholderVisibility);

  // Initial state
  updatePlaceholderVisibility();

  const allLanguageOptions = Array.from(languageSelect.options).map(opt => ({
    value: opt.value,
    text: opt.text,
    selected: opt.selected
  }));

  function updateLanguageOptions() {
    const selectedAPI = apiSelect.value;

    // Clear text when switching providers
    if (searchInput) {
      searchInput.value = "";
    }
    currentTranscription = "";

    languageSelect.innerHTML = '';

    if (selectedAPI === "Azure OpenAI" || selectedAPI === "ElevenLabs ScribeV2") {
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

  // Pointer Events - Hold-to-record with click-to-stop
  // - If NOT recording: Hold for HOLD_THRESHOLD_MS to start recording
  // - If ALREADY recording: Any click stops recording immediately
  // This allows quick clicks to stop, but requires hold to start
  let holdTimeout = null;
  let recordingStartedThisPress = false; // Track if we started recording in THIS press cycle
  const HOLD_THRESHOLD_MS = 150; // Minimum hold duration before recording starts

  micButton.addEventListener("mousedown", (e) => {
    e.preventDefault();
    recordingStartedThisPress = false;

    // If already recording, prepare to stop on mouseup (no need to wait)
    if (isRecording || isInitializing) {
      // Recording is active - we'll stop on mouseup
      return;
    }

    // Not recording - delay starting until user has held for threshold
    holdTimeout = setTimeout(() => {
      recordingStartedThisPress = true;
      startRecordingHandler();
    }, HOLD_THRESHOLD_MS);
  });

  micButton.addEventListener("mouseup", (e) => {
    e.preventDefault();

    // Cancel pending start if released before threshold
    if (holdTimeout) {
      clearTimeout(holdTimeout);
      holdTimeout = null;
    }

    // If recording is active (either from this press or a previous one), stop it
    if (isRecording || isInitializing) {
      stopRecordingHandler();
      recordingStartedThisPress = false;
    }
  });

  micButton.addEventListener("mouseleave", () => {
    // Cancel pending start if mouse leaves before threshold
    if (holdTimeout) {
      clearTimeout(holdTimeout);
      holdTimeout = null;
    }

    // If we started recording in this press cycle, stop on leave
    if (recordingStartedThisPress && (isRecording || isInitializing)) {
      stopRecordingHandler();
      recordingStartedThisPress = false;
    }
  });

  // Touch Events - same logic for mobile
  let touchHoldTimeout = null;
  let touchRecordingStartedThisPress = false;

  micButton.addEventListener("touchstart", (e) => {
    e.preventDefault();
    touchRecordingStartedThisPress = false;

    // If already recording, prepare to stop on touchend
    if (isRecording || isInitializing) {
      return;
    }

    // Not recording - delay starting until user has held for threshold
    touchHoldTimeout = setTimeout(() => {
      touchRecordingStartedThisPress = true;
      startRecordingHandler();
    }, HOLD_THRESHOLD_MS);
  }, { passive: false });

  micButton.addEventListener("touchend", (e) => {
    e.preventDefault();

    if (touchHoldTimeout) {
      clearTimeout(touchHoldTimeout);
      touchHoldTimeout = null;
    }

    // If recording is active, stop it
    if (isRecording || isInitializing) {
      stopRecordingHandler();
      touchRecordingStartedThisPress = false;
    }
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

  // Initialize recent transcriptions
  renderRecentTranscriptions();

  // Clear all button handler
  const clearAllBtn = document.getElementById('clearAllBtn');
  if (clearAllBtn) {
    clearAllBtn.addEventListener('click', clearRecentTranscriptions);
  }
});
