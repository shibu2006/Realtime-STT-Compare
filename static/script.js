let isRecording = false;
let socket;
let microphone;

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
  } else if (data.status === "stopped") {
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
  const searchInput = document.getElementById("searchInput");
  if (data.transcription && data.transcription.trim()) {
    searchInput.value = data.transcription;
  }
});

async function getMicrophone() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    return new MediaRecorder(stream, { mimeType: "audio/webm" });
  } catch (error) {
    console.error("Error accessing microphone:", error);
    throw error;
  }
}

async function openMicrophone(microphone, socket) {
  return new Promise((resolve) => {
    microphone.onstart = () => {
      console.log("Client: Microphone opened");
      document.body.classList.add("recording");
      const micButton = document.getElementById("micButton");
      const languageSelect = document.getElementById("languageSelect");
      micButton.classList.add("recording");
      languageSelect.disabled = true; // Disable language selection during recording
      resolve();
    };
    microphone.ondataavailable = async (event) => {
      console.log("client: microphone data received");
      if (event.data.size > 0) {
        // Convert Blob to ArrayBuffer for Deepgram
        const arrayBuffer = await event.data.arrayBuffer();
        socket.emit("audio_stream", arrayBuffer);
      }
    };
    microphone.start(1000);
  });
}

async function startRecording() {
  isRecording = true;
  const searchInput = document.getElementById("searchInput");
  searchInput.value = ""; // Clear the input when starting a new recording
  microphone = await getMicrophone();
  console.log("Client: Waiting to open microphone");
  await openMicrophone(microphone, socket);
}

async function stopRecording() {
  if (isRecording === true) {
    microphone.stop();
    microphone.stream.getTracks().forEach((track) => track.stop()); // Stop all tracks
    socket.emit("toggle_transcription", { action: "stop" });
    microphone = null;
    isRecording = false;
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
  const recordCheckbox = document.getElementById("record");

  // Handle microphone button click
  micButton.addEventListener("click", () => {
    if (!isRecording) {
      if (!socket.connected) {
        console.error("Socket not connected. Please refresh the page.");
        alert("Connection lost. Please refresh the page.");
        return;
      }
      const selectedLanguage = languageSelect.value;
      socket.emit("toggle_transcription", { 
        action: "start",
        language: selectedLanguage
      });
      startRecording().catch((error) =>
        console.error("Error starting recording:", error)
      );
    } else {
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
