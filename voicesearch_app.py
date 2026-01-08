import logging
import os
import time
import threading
from typing import TYPE_CHECKING, Optional, cast
from flask import Flask, render_template
from flask_socketio import SocketIO
from dotenv import load_dotenv
from deepgram import (
    DeepgramClient,
    LiveTranscriptionEvents,
    LiveOptions
)

# Import Azure OpenAI handler
# Define stub functions first to satisfy type checker
def initialize_azure_openai_connection(socketio_instance: SocketIO, language_name: str = "English") -> bool:
    """Stub function - will be replaced if import succeeds"""
    return False

def send_audio_to_azure_openai(audio_data: bytes) -> bool:
    """Stub function - will be replaced if import succeeds"""
    return False

def close_azure_openai_connection() -> None:
    """Stub function - will be replaced if import succeeds"""
    pass

try:
    from azure_openai_handler import (
        initialize_azure_openai_connection as _init_azure,
        send_audio_to_azure_openai as _send_azure,
        close_azure_openai_connection as _close_azure
    )
    # Replace stub functions with real ones
    initialize_azure_openai_connection = _init_azure
    send_audio_to_azure_openai = _send_azure
    close_azure_openai_connection = _close_azure
    AZURE_OPENAI_AVAILABLE = True
except ImportError as e:
    logging.warning(f"Azure OpenAI handler not available: {e}")
    AZURE_OPENAI_AVAILABLE = False

# Import ElevenLabs handler
# Define stub functions first to satisfy type checker
def initialize_elevenlabs_connection(socketio_instance: SocketIO, language_name: str = "Auto") -> bool:
    """Stub function - will be replaced if import succeeds"""
    return False

def send_audio_to_elevenlabs(audio_data: bytes) -> bool:
    """Stub function - will be replaced if import succeeds"""
    return False

def close_elevenlabs_connection() -> None:
    """Stub function - will be replaced if import succeeds"""
    pass

try:
    from elevenlabs_handler import (
        initialize_elevenlabs_connection as _init_elevenlabs,
        send_audio_to_elevenlabs as _send_elevenlabs,
        close_elevenlabs_connection as _close_elevenlabs
    )
    # Replace stub functions with real ones
    initialize_elevenlabs_connection = _init_elevenlabs
    send_audio_to_elevenlabs = _send_elevenlabs
    close_elevenlabs_connection = _close_elevenlabs
    ELEVENLABS_AVAILABLE = True
except ImportError as e:
    logging.warning(f"ElevenLabs handler not available: {e}")
    ELEVENLABS_AVAILABLE = False


def resample_audio_24k_to_16k(audio_bytes: bytes) -> bytes:
    """
    Resample PCM16 audio from 24kHz to 16kHz for ElevenLabs
    Uses simple linear interpolation for downsampling
    
    Args:
        audio_bytes: PCM16 audio data at 24kHz
    
    Returns:
        PCM16 audio data at 16kHz
    """
    import struct
    
    # Convert bytes to int16 samples
    num_samples = len(audio_bytes) // 2
    samples = struct.unpack(f'<{num_samples}h', audio_bytes)
    
    # Resample ratio: 16000/24000 = 2/3
    output_length = int(num_samples * 16000 / 24000)
    resampled = []
    
    for i in range(output_length):
        src_index = i * 24000 / 16000
        index = int(src_index)
        frac = src_index - index
        
        sample1 = samples[index] if index < num_samples else 0
        sample2 = samples[min(num_samples - 1, index + 1)] if index + 1 < num_samples else sample1
        
        # Linear interpolation
        value = int(sample1 + frac * (sample2 - sample1))
        # Clamp to int16 range
        value = max(-32768, min(32767, value))
        resampled.append(value)
    
    # Convert back to bytes
    return struct.pack(f'<{len(resampled)}h', *resampled)

if TYPE_CHECKING:
    from deepgram.clients import LiveClient

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler('voicesearch_app.log'),
        logging.StreamHandler()  # Also log to console
    ]
)

logger = logging.getLogger(__name__)

# Configure performance logger
performance_logger = logging.getLogger('performance')
performance_logger.setLevel(logging.INFO)
performance_handler = logging.FileHandler('voicesearch_performance.log')
performance_formatter = logging.Formatter('%(asctime)s - %(filename)s:%(lineno)d - %(message)s')
performance_handler.setFormatter(performance_formatter)
performance_logger.addHandler(performance_handler)
performance_logger.propagate = False  # Don't propagate to root logger

# Initialize Flask app
# Use threading mode to avoid gevent/eventlet monkey-patching conflicts with Deepgram's synchronous WebSocket client
# Threading mode doesn't monkey-patch, which is safer for synchronous WebSocket libraries
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

API_KEY = os.getenv("DEEPGRAM_API_KEY")

if not API_KEY:
    logger.warning("DEEPGRAM_API_KEY environment variable is not set - Deepgram API will not work")
else:
    # Validate API key format (Deepgram API keys typically start with a specific pattern)
    if len(API_KEY) < 20:
        logger.warning(f"API key seems unusually short ({len(API_KEY)} characters). Please verify it's correct.")
    else:
        logger.info(f"API key loaded successfully (length: {len(API_KEY)} characters)")

# Get silence timeout from environment (in milliseconds, convert to seconds)
SILENCE_TIMEOUT_MS = int(os.getenv("SILENCE_TIMEOUT", "5000"))  # Default 5 seconds if not set
SILENCE_TIMEOUT_SEC = SILENCE_TIMEOUT_MS / 1000.0

# Get server configuration from environment
HOST = os.getenv("HOST", "0.0.0.0")  # Default to 0.0.0.0 for external access
PORT = int(os.getenv("PORT", "8000"))  # Default to port 8000

# Track performance metrics
session_start_time = None
transcription_count = 0
last_transcription_time = None
last_audio_send_time = None  # Track when audio was last sent to Deepgram
silence_timer = None  # Timer for silence timeout
silence_timer_lock = threading.Lock()  # Lock for thread-safe timer operations

# Initialize Deepgram client (simplified to match reference implementation)
# Only initialize if API_KEY is available
deepgram = DeepgramClient(API_KEY) if API_KEY else None

dg_connection: Optional['LiveClient'] = None

def reset_silence_timer():
    """Reset the silence timeout timer when transcription is received"""
    global silence_timer
    with silence_timer_lock:
        if silence_timer:
            silence_timer.cancel()
        silence_timer = threading.Timer(SILENCE_TIMEOUT_SEC, handle_silence_timeout)
        silence_timer.start()

def stop_silence_timer():
    """Stop the silence timeout timer"""
    global silence_timer
    with silence_timer_lock:
        if silence_timer:
            silence_timer.cancel()
            silence_timer = None

def handle_silence_timeout():
    """Handle silence timeout - automatically stop transcription"""
    global dg_connection, session_start_time, transcription_count, last_transcription_time, last_audio_send_time
    logger.info(f"Silence timeout reached ({SILENCE_TIMEOUT_MS}ms). Stopping transcription automatically.")
    performance_logger.info(f"SILENCE_TIMEOUT | Timeout: {SILENCE_TIMEOUT_MS}ms")
    
    if dg_connection:
        try:
            dg_connection.finish()
            logger.info("Deepgram connection finished due to silence timeout")
        except Exception as e:
            logger.error(f"Error finishing Deepgram connection on timeout: {e}")
        dg_connection = None
    
    # Clean up session tracking
    if session_start_time:
        session_duration_ms = (time.perf_counter() - session_start_time) * 1000
        performance_logger.info(
            f"SESSION_END | TotalDuration: {session_duration_ms:.2f}ms | "
            f"TotalTranscriptions: {transcription_count} | Reason: SilenceTimeout"
        )
        session_start_time = None
        transcription_count = 0
        last_transcription_time = None
        last_audio_send_time = None
    
    # Notify frontend to stop recording
    socketio.emit('silence_timeout', {'message': f'Recording stopped due to {SILENCE_TIMEOUT_MS}ms silence timeout'})
    stop_silence_timer()

# Language configuration dictionary
LANGUAGES = {
    "English": ("nova-3", "en-US"),
    "German": ("nova-3", "de"),
    "Spanish": ("nova-3", "es"),
    "French": ("nova-3", "fr"),
    "Japanese": ("nova-3", "ja"),
    "Portuguese": ("nova-3", "pt-BR"),
    "Russian": ("nova-3", "ru"),
    "Italian": ("nova-3", "it"),
    "Korean": ("nova-3", "ko"),
    "Hindi": ("nova-3", "hi"),
    "Swedish": ("nova-3", "sv"),
    "Dutch": ("nova-3", "nl"),
    "Finnish": ("nova-3", "fi"),
    "Danish": ("nova-3", "da"),
    "Norwegian": ("nova-3", "no"),
    "Chinese": ("nova-2", "zh-CN"),
    "Hindi-English": ("nova", "hi-Latn")
}

def initialize_deepgram_connection(language_name="English"):
    global dg_connection, session_start_time, transcription_count, last_transcription_time, last_audio_send_time
    logger.info(f"Initializing Deepgram connection with language: {language_name}")
    
    # Close existing connection if any
    if dg_connection:
        try:
            logger.info("Closing existing Deepgram connection")
            dg_connection.finish()
        except Exception as e:
            logger.warning(f"Error closing existing connection: {e}")
        dg_connection = None
    
    # Reset performance tracking for new session
    session_start_time = time.perf_counter()
    transcription_count = 0
    last_transcription_time = None
    last_audio_send_time = None
    
    # Get model and language code from LANGUAGES dictionary
    if language_name in LANGUAGES:
        model, language_code = LANGUAGES[language_name]
    else:
        # Default to English if language not found
        model, language_code = LANGUAGES["English"]
        logger.warning(f"Language '{language_name}' not found, defaulting to English")
    
    logger.info(f"Initializing Deepgram with model: {model}, language: {language_code}")
    
    # Initialize Deepgram client and connection
    if not deepgram:
        logger.error("Deepgram client not initialized - API_KEY is missing")
        return False
    
    try:
        connection =  deepgram.listen.live.v("1") #deepgram.listen.websocket.v("1")
        logger.info("Deepgram Live connection object created successfully")
    except Exception as e:
        logger.error(f"Failed to create Deepgram Live connection object: {type(e).__name__}: {e}")
        logger.exception("Full traceback:")
        return False
    
    # Type cast to help type checker understand this is a LiveClient
    if TYPE_CHECKING:
        from deepgram.clients import LiveClient
        connection = cast('LiveClient', connection)
    
    # Update global variable (already declared as global at function start)
    dg_connection = connection

    # Create callbacks with captured model and language_name values
    def on_open(self, open, **kwargs):
        global session_start_time
        logger.info(f"Deepgram connection opened: {open}")
        session_start_time = time.perf_counter()
        performance_logger.info(f"SESSION_START | Language: {language_name} | Model: {model} | Timestamp: {time.time()}")
        # Start silence timeout timer
        reset_silence_timer()

    def on_message(self, result, **kwargs):
        global transcription_count, last_transcription_time, last_audio_send_time
        transcript = result.channel.alternatives[0].transcript
        if len(transcript) > 0:
            # Reset silence timer when transcription is received
            reset_silence_timer()
            
            # Calculate time since session start and time since last transcription
            current_time = time.perf_counter()
            time_since_start_ms = (current_time - session_start_time) * 1000 if session_start_time else 0
            
            # Calculate time since last transcription (response latency)
            if last_transcription_time:
                time_since_last_ms = (current_time - last_transcription_time) * 1000
            else:
                time_since_last_ms = 0
            
            # Calculate transcription response time (time from audio send to transcription received)
            if last_audio_send_time:
                transcription_response_time_ms = (current_time - last_audio_send_time) * 1000
            else:
                transcription_response_time_ms = 0
            
            transcription_count += 1
            last_transcription_time = current_time
            
            # Log performance metrics with transcription response time
            performance_logger.info(
                f"TRANSCRIPTION | Count: {transcription_count} | "
                f"ResponseTime: {transcription_response_time_ms:.2f}ms | "
                f"TimeSinceStart: {time_since_start_ms:.2f}ms | "
                f"TimeSinceLast: {time_since_last_ms:.2f}ms | "
                f"Text: \"{transcript}\""
            )
            
            logger.info(f"Deepgram received transcript: {transcript}")
            socketio.emit('transcription_update', {'transcription': transcript})

    def on_close(self, close, **kwargs):
        global session_start_time, transcription_count, last_transcription_time, last_audio_send_time
        logger.info(f"Deepgram connection closed: {close}")
        # Stop silence timer when connection closes
        stop_silence_timer()
        if session_start_time:
            session_duration_ms = (time.perf_counter() - session_start_time) * 1000
            performance_logger.info(
                f"SESSION_END | TotalDuration: {session_duration_ms:.2f}ms | "
                f"TotalTranscriptions: {transcription_count}"
            )
            session_start_time = None
            transcription_count = 0
            last_transcription_time = None
            last_audio_send_time = None

    def on_error(self, error, **kwargs):
        logger.error(f"Deepgram connection error: {error}")
        performance_logger.error(f"ERROR | Message: {error}")

    connection.on(LiveTranscriptionEvents.Open, on_open)  # type: ignore[arg-type]
    connection.on(LiveTranscriptionEvents.Transcript, on_message)  # type: ignore[arg-type]
    connection.on(LiveTranscriptionEvents.Close, on_close)  # type: ignore[arg-type]
    connection.on(LiveTranscriptionEvents.Error, on_error)  # type: ignore[arg-type]
    
    # Define the options for the live transcription
    options = LiveOptions(
        model=model, 
        language=language_code
    )
    
    try:
        logger.info("Attempting to start Deepgram Live connection...")
        logger.info(f"API Key present: {bool(API_KEY)}, Length: {len(API_KEY) if API_KEY else 0}")
        if connection.start(options) is False:
            logger.error("Failed to start Deepgram connection - start() returned False")
            logger.error("This could be due to:")
            logger.error("1. Invalid API key")
            logger.error("2. Network connectivity issues")
            logger.error("3. Deepgram service unavailable")
            # Clean up the connection object
            dg_connection = None
            return False
        
        logger.info("Deepgram connection started successfully")
        return True
    except Exception as e:
        logger.error(f"Exception while starting Deepgram connection: {type(e).__name__}: {e}")
        logger.exception("Full traceback:")
        # Clean up the connection object
        dg_connection = None
        return False

@app.route('/')
def index():
    logger.info("Serving index page")
    return render_template('index.html')

# Store current API provider for audio routing
current_api_provider = "Deepgram API"

@socketio.on('audio_stream')
def handle_audio_stream(data):
    """
    Handle audio stream from client
    Routes audio to the appropriate API provider based on current connection
    """
    global last_audio_send_time, current_api_provider
    
    # Extract audio bytes
    # The browser sends audio as ArrayBuffer/bytes directly
    if isinstance(data, bytes):
        audio_bytes = data
    elif isinstance(data, bytearray):
        audio_bytes = bytes(data)
    elif isinstance(data, dict):
        # If data is a dict, try to get audio bytes
        audio_bytes = data.get("audio")
        if isinstance(audio_bytes, (bytes, bytearray)):
            audio_bytes = bytes(audio_bytes)
        else:
            logger.warning("Audio data format not recognized in dict")
            return
    else:
        # Try to convert to bytes
        try:
            audio_bytes = bytes(data)
        except Exception as e:
            logger.error(f"Error converting audio data to bytes: {e}")
            return
    
    # Route audio to appropriate API based on current provider
    if current_api_provider == "Azure OpenAI":
        if AZURE_OPENAI_AVAILABLE:
            # Note: Azure OpenAI expects PCM16 format at 24kHz
            success = send_audio_to_azure_openai(audio_bytes)
            if success:
                logger.debug(f"✅ Audio stream data sent to Azure OpenAI ({len(audio_bytes)} bytes)")
            else:
                # This is expected during initial connection establishment - use debug level
                logger.debug(f"⏳ Audio not sent yet - Azure OpenAI connection establishing ({len(audio_bytes)} bytes)")
        else:
            logger.warning("Audio stream received but Azure OpenAI is not available")
    elif current_api_provider == "ElvenLabs ScribeV2":
        if ELEVENLABS_AVAILABLE:
            # ElevenLabs expects PCM16 format at 16kHz - need to resample from 24kHz
            # The browser sends 24kHz, ElevenLabs needs 16kHz
            resampled_audio = resample_audio_24k_to_16k(audio_bytes)
            success = send_audio_to_elevenlabs(resampled_audio)
            if success:
                logger.debug(f"✅ Audio stream data sent to ElevenLabs ({len(resampled_audio)} bytes)")
            else:
                logger.debug(f"⏳ Audio not sent yet - ElevenLabs connection establishing ({len(audio_bytes)} bytes)")
        else:
            logger.warning("Audio stream received but ElevenLabs is not available")
    else:  # Default to Deepgram API
        if dg_connection:
            try:
                # Track when audio is sent to Deepgram for response time calculation
                last_audio_send_time = time.perf_counter()
                dg_connection.send(audio_bytes)
                logger.debug(f"Audio stream data sent to Deepgram ({len(audio_bytes)} bytes)")
            except Exception as e:
                logger.error(f"Error sending audio data to Deepgram: {e}")
        else:
            logger.warning("Audio stream received but Deepgram connection is not initialized")

@socketio.on('toggle_transcription')
def handle_toggle_transcription(data):
    global current_api_provider
    logger.info(f"Toggle transcription event received: {data}")
    action = data.get("action")
    api_provider = data.get("api", "Deepgram API")  # Default to Deepgram API
    language_name = data.get("language", "English")  # Default to English if not provided
    
    # Update current API provider
    current_api_provider = api_provider
    
    if action == "start":
        if api_provider == "Azure OpenAI":
            if not AZURE_OPENAI_AVAILABLE:
                socketio.emit('transcription_status', {
                    'status': 'error',
                    'message': 'Azure OpenAI handler not available. Please check dependencies.'
                })
                return
            
            logger.info(f"Starting Azure OpenAI connection with language: {language_name}")
            success = initialize_azure_openai_connection(socketio, language_name)
            if success:
                socketio.emit('transcription_status', {'status': 'started', 'api': 'Azure OpenAI'})
            else:
                socketio.emit('transcription_status', {
                    'status': 'error',
                    'message': 'Failed to start Azure OpenAI connection'
                })
        elif api_provider == "ElvenLabs ScribeV2":
            if not ELEVENLABS_AVAILABLE:
                socketio.emit('transcription_status', {
                    'status': 'error',
                    'message': 'ElevenLabs handler not available. Please check dependencies (pip install websockets).'
                })
                return
            
            # Check for API key
            if not os.getenv("ELEVENLABS_API_KEY"):
                socketio.emit('transcription_status', {
                    'status': 'error',
                    'message': 'ELEVENLABS_API_KEY environment variable is not set'
                })
                return
            
            logger.info(f"Starting ElevenLabs connection with language: {language_name}")
            success = initialize_elevenlabs_connection(socketio, language_name)
            if success:
                # Note: transcription_status 'started' is emitted by the handler when session starts
                logger.info("ElevenLabs connection initialization started")
            else:
                socketio.emit('transcription_status', {
                    'status': 'error',
                    'message': 'Failed to start ElevenLabs connection'
                })
        else:  # Default to Deepgram API
            if not API_KEY:
                socketio.emit('transcription_status', {
                    'status': 'error',
                    'message': 'DEEPGRAM_API_KEY environment variable is not set'
                })
                return
            
            logger.info(f"Starting Deepgram connection with language: {language_name}")
            success = initialize_deepgram_connection(language_name)
            if success:
                socketio.emit('transcription_status', {'status': 'started', 'language': language_name, 'api': 'Deepgram API'})
            else:
                socketio.emit('transcription_status', {'status': 'error', 'message': 'Failed to start Deepgram connection'})
    
    elif action == "stop":
        if api_provider == "Azure OpenAI":
            logger.info("Stopping Azure OpenAI connection")
            close_azure_openai_connection()
            socketio.emit('transcription_status', {'status': 'stopped'})
        elif api_provider == "ElvenLabs ScribeV2":
            logger.info("Stopping ElevenLabs connection")
            if ELEVENLABS_AVAILABLE:
                close_elevenlabs_connection()
            socketio.emit('transcription_status', {'status': 'stopped'})
        else:  # Default to Deepgram API
            logger.info("Stopping Deepgram connection")
            # Stop silence timer when manually stopping
            stop_silence_timer()
            if dg_connection:
                try:
                    dg_connection.finish()
                    logger.info("Deepgram connection finished")
                except Exception as e:
                    logger.error(f"Error finishing Deepgram connection: {e}")
            socketio.emit('transcription_status', {'status': 'stopped'})

@socketio.on('connect')
def server_connect():
    logger.info('Client connected to SocketIO')

@socketio.on('disconnect')
def server_disconnect():
    global dg_connection
    logger.info('Client disconnected from SocketIO')
    # Clean up all connections when client disconnects
    stop_silence_timer()
    
    # Clean up Deepgram connection
    if dg_connection:
        try:
            logger.info('Closing Deepgram connection on client disconnect')
            dg_connection.finish()
        except Exception as e:
            logger.error(f"Error closing Deepgram connection on disconnect: {e}")
        dg_connection = None
    
    # Clean up Azure OpenAI connection
    if AZURE_OPENAI_AVAILABLE:
        try:
            close_azure_openai_connection()
            logger.info('Closed Azure OpenAI connection on client disconnect')
        except Exception as e:
            logger.error(f"Error closing Azure OpenAI connection on disconnect: {e}")
    
    # Clean up ElevenLabs connection
    if ELEVENLABS_AVAILABLE:
        try:
            close_elevenlabs_connection()
            logger.info('Closed ElevenLabs connection on client disconnect')
        except Exception as e:
            logger.error(f"Error closing ElevenLabs connection on disconnect: {e}")

@socketio.on('restart_deepgram')
def restart_deepgram(data):
    language_name = data.get("language", "English") if data else "English"
    logger.info(f'Restarting Deepgram connection with language: {language_name}')
    initialize_deepgram_connection(language_name)

if __name__ == '__main__':
    logger.info(f"Starting Flask-SocketIO server on {HOST}:{PORT}")
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True, host=HOST, port=PORT)
