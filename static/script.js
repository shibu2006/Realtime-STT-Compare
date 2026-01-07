let isRecording = false;
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
    connectionReady = false;
  } else if (data.status === "started") {
    // Backend connection is ready - flush any buffered audio
    connectionReady = true;
    console.log(`✅ Backend connection ready. Flushing ${pendingAudioChunks.length} buffered audio chunks...`);
    
    // Send all buffered audio chunks
    while (pendingAudioChunks.length > 0) {
      const chunk = pendingAudioChunks.shift();
      socket.emit("audio_stream", chunk);
    }
    console.log("✅ Buffered audio flushed");
  } else if (data.status === "stopped") {
    connectionReady = false;
    // If recording is still active, stop it
    if (isRecording) {
      stopRecording().catch((error) =>
        console.error("Error stopping recording:", error)
      );
    }
  }
});

socket.on("silence_timeout", (data) => {
  console.log("Silence timeout:", data.message);
  // Automatically stop recording when silence timeout is reached
  if (isRecording) {
    stopRecording().catch((error) =>
      console.error("Error stopping recording on timeout:", error)
    );
    // Optionally show a notification to the user
    console.warn(data.message);
  }
});

socket.on("transcription_update", (data) => {
  console.log("✅ Received transcription_update event:", data);
  const searchInput = document.getElementById("searchInput");
  if (!searchInput) {
    console.error("❌ searchInput element not found!");
    return;
  }
  if (data && data.transcription !== undefined && data.transcription !== null) {
    // Update the input field with the transcription
    // Allow empty strings to clear the field if needed
    const oldValue = searchInput.value;
    searchInput.value = data.transcription;
    console.log(`✅ Updated search input: "${oldValue}" -> "${data.transcription}"`);
    
    // Also trigger input event to ensure any listeners are notified
    searchInput.dispatchEvent(new Event('input', { bubbles: true }));
  } else {
    console.warn("⚠️ transcription_update received but transcription field is missing or invalid:", data);
  }
});

async function getMicrophone(useWebAudio = false) {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ 
      audio: {
        channelCount: 1,
        sampleRate: 24000, // Azure OpenAI expects 24kHz
        echoCancellation: true,
        noiseSuppression: true
      }
    });
    
    if (useWebAudio) {
      // For Azure OpenAI: Use Web Audio API to get PCM16
      audioStream = stream;
      return { type: 'webaudio', stream: stream };
    } else {
      // For Deepgram: Use MediaRecorder to get WebM
      return { type: 'mediarecorder', recorder: new MediaRecorder(stream, { mimeType: "audio/webm" }) };
    }
  } catch (error) {
    console.error("Error accessing microphone:", error);
    throw error;
  }
}

async function openMicrophone(microphone, socket, useWebAudio = false) {
  if (useWebAudio && microphone.type === 'webaudio') {
    // Use Web Audio API for PCM16 (Azure OpenAI)
    return new Promise((resolve) => {
      try {
        // Use default sample rate (usually 48kHz or 44.1kHz)
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const actualSampleRate = audioContext.sampleRate;
        console.log(`AudioContext sample rate: ${actualSampleRate}Hz (Azure OpenAI expects 24000Hz)`);
        
        const source = audioContext.createMediaStreamSource(microphone.stream);
        
        // Use smaller buffer size (1024) to match CLI behavior and reduce latency
        // CLI uses CHUNK = 1024 at 24kHz, so we use 2048 at 48kHz to get similar ~1024 samples after resampling
        const bufferSize = 2048;
        
        audioProcessor = audioContext.createScriptProcessor(bufferSize, 1, 1);
        
        // Target sample rate for Azure OpenAI (must match CLI: 24000Hz)
        const targetSampleRate = 24000;
        
        // Pre-calculate resampling ratio
        const resampleRatio = targetSampleRate / actualSampleRate;
        
        // Audio buffer for accumulating samples to send in consistent chunks
        let audioBuffer = new Float32Array(0);
        const targetChunkSize = 1024; // Match CLI CHUNK size exactly
        
        audioProcessor.onaudioprocess = (event) => {
          if (!isRecording) return;
          
          const inputData = event.inputBuffer.getChannelData(0);
          
          // Resample to 24kHz using linear interpolation (simpler, less CPU, good enough for speech)
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
          
          // Append to buffer
          const newBuffer = new Float32Array(audioBuffer.length + resampledData.length);
          newBuffer.set(audioBuffer);
          newBuffer.set(resampledData, audioBuffer.length);
          audioBuffer = newBuffer;
          
          // Send chunks of exactly targetChunkSize (1024 samples) to match CLI behavior
          while (audioBuffer.length >= targetChunkSize) {
            const chunk = audioBuffer.slice(0, targetChunkSize);
            audioBuffer = audioBuffer.slice(targetChunkSize);
            
            // Convert Float32Array to Int16Array (PCM16)
            const pcm16Data = new Int16Array(chunk.length);
            for (let i = 0; i < chunk.length; i++) {
              const s = Math.max(-1, Math.min(1, chunk[i]));
              pcm16Data[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }
            
            // If connection is ready, send immediately; otherwise buffer
            if (connectionReady) {
              socket.emit("audio_stream", pcm16Data.buffer);
            } else {
              // Buffer audio while waiting for connection (keep up to 5 seconds of audio)
              pendingAudioChunks.push(pcm16Data.buffer);
              // Limit buffer size to ~5 seconds (24000 samples/sec / 1024 samples/chunk * 5 sec = ~117 chunks)
              const maxChunks = 120;
              if (pendingAudioChunks.length > maxChunks) {
                pendingAudioChunks.shift(); // Remove oldest chunk
              }
            }
          }
        };
        
        source.connect(audioProcessor);
        // Connect to a silent gain node to avoid audio feedback
        const gainNode = audioContext.createGain();
        gainNode.gain.value = 0; // Silent
        audioProcessor.connect(gainNode);
        gainNode.connect(audioContext.destination);
        
        console.log("Client: Web Audio API microphone opened (PCM16, chunk size: 1024 samples at 24kHz)");
        document.body.classList.add("recording");
        const micButton = document.getElementById("micButton");
        const languageSelect = document.getElementById("languageSelect");
        micButton.classList.add("recording");
        languageSelect.disabled = true;
        resolve();
      } catch (error) {
        console.error("Error setting up Web Audio API:", error);
        throw error;
      }
    });
  } else if (microphone.type === 'mediarecorder') {
    // Use MediaRecorder for WebM (Deepgram)
    return new Promise((resolve) => {
      microphone.recorder.onstart = () => {
        console.log("Client: MediaRecorder microphone opened (WebM)");
        document.body.classList.add("recording");
        const micButton = document.getElementById("micButton");
        const languageSelect = document.getElementById("languageSelect");
        micButton.classList.add("recording");
        languageSelect.disabled = true;
        resolve();
      };
      microphone.recorder.ondataavailable = async (event) => {
        console.log("client: microphone data received");
        if (event.data.size > 0) {
          const arrayBuffer = await event.data.arrayBuffer();
          socket.emit("audio_stream", arrayBuffer);
        }
      };
      microphone.recorder.start(1000);
    });
  } else {
    throw new Error("Unknown microphone type");
  }
}

async function startRecording() {
  isRecording = true;
  connectionReady = false; // Reset connection state
  pendingAudioChunks = []; // Clear any old buffered audio
  
  const searchInput = document.getElementById("searchInput");
  searchInput.value = ""; // Clear the input when starting a new recording
  
  // Check which API is selected to determine audio capture method
  const apiSelect = document.getElementById("apiSelect");
  const useWebAudio = apiSelect.value === "Azure OpenAI"; // Use Web Audio API for Azure OpenAI
  
  // For non-Azure APIs, mark connection as ready immediately (they don't need buffering)
  if (!useWebAudio) {
    connectionReady = true;
  }
  
  microphone = await getMicrophone(useWebAudio);
  console.log(`Client: Waiting to open microphone (${useWebAudio ? 'Web Audio API' : 'MediaRecorder'})`);
  await openMicrophone(microphone, socket, useWebAudio);
}

async function stopRecording() {
  if (isRecording === true) {
    if (microphone && microphone.type === 'mediarecorder') {
      // Stop MediaRecorder
      microphone.recorder.stop();
      if (microphone.stream) {
        microphone.stream.getTracks().forEach((track) => track.stop());
      }
    } else if (microphone && microphone.type === 'webaudio') {
      // Stop Web Audio API
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
    pendingAudioChunks = []; // Clear buffered audio
    console.log("Client: Microphone closed");
    document.body.classList.remove("recording");
    const micButton = document.getElementById("micButton");
    const languageSelect = document.getElementById("languageSelect");
    micButton.classList.remove("recording");
    languageSelect.disabled = false; // Re-enable language selection after recording stops
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const micButton = document.getElementById("micButton");
  const searchButton = document.getElementById("searchButton");
  const searchInput = document.getElementById("searchInput");
  const languageSelect = document.getElementById("languageSelect");
  const apiSelect = document.getElementById("apiSelect");
  const recordCheckbox = document.getElementById("record");

  // Store all language options for later use (clone them to preserve original state)
  const allLanguageOptions = Array.from(languageSelect.options).map(opt => ({
    value: opt.value,
    text: opt.text,
    selected: opt.selected
  }));

  // Function to update language options based on selected API
  function updateLanguageOptions() {
    const selectedAPI = apiSelect.value;
    
    // Clear current options
    languageSelect.innerHTML = '';
    
    if (selectedAPI === "Azure OpenAI" || selectedAPI === "ElvenLabs ScribeV2") {
      // Show only "Auto" option for Azure OpenAI or ElvenLabs ScribeV2
      const autoOption = allLanguageOptions.find(opt => opt.value === "Auto");
      if (autoOption) {
        const newOption = new Option(autoOption.text, autoOption.value, true, true);
        languageSelect.appendChild(newOption);
      }
    } else {
      // Show all language options EXCEPT "Auto" for Deepgram API
      // Restore original selected state (English should be selected by default)
      allLanguageOptions.forEach(option => {
        // Skip "Auto" option for Deepgram API
        if (option.value !== "Auto") {
          const newOption = new Option(option.text, option.value, option.selected, option.selected);
          languageSelect.appendChild(newOption);
        }
      });
    }
  }

  // Handle API selection change
  apiSelect.addEventListener("change", () => {
    updateLanguageOptions();
  });

  // Initialize language options on page load
  updateLanguageOptions();

  // Handle microphone button click
  micButton.addEventListener("click", () => {
    if (!isRecording) {
      if (!socket.connected) {
        console.error("Socket not connected. Please refresh the page.");
        alert("Connection lost. Please refresh the page.");
        return;
      }
      const selectedLanguage = languageSelect.value;
      const selectedAPI = apiSelect.value;
      socket.emit("toggle_transcription", { 
        action: "start",
        language: selectedLanguage,
        api: selectedAPI
      });
      startRecording().catch((error) =>
        console.error("Error starting recording:", error)
      );
    } else {
      const selectedAPI = apiSelect.value;
      socket.emit("toggle_transcription", { 
        action: "stop",
        api: selectedAPI
      });
      stopRecording().catch((error) =>
        console.error("Error stopping recording:", error)
      );
    }
  });

  // Handle search button click
  searchButton.addEventListener("click", () => {
    const searchQuery = searchInput.value.trim();
    if (searchQuery) {
      // You can add search functionality here
      console.log("Searching for:", searchQuery);
    }
  });

  // Handle Enter key in search input
  searchInput.addEventListener("keypress", (e) => {
    if (e.key === "Enter") {
      searchButton.click();
    }
  });
});
