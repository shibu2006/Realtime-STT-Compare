"""
ElevenLabs Scribe V2 Realtime API Handler
Handles WebSocket connections and audio transcription using ElevenLabs Scribe v2 Realtime API
Based on the working elevenlabs_scribev2.py implementation
"""
import os
import json
import base64
import logging
import threading
import time
import asyncio
from typing import Optional, TYPE_CHECKING
from flask_socketio import SocketIO

logger = logging.getLogger(__name__)

# Configure performance logger
performance_logger = logging.getLogger('elevenlabs_performance')
performance_logger.setLevel(logging.INFO)
performance_handler = logging.FileHandler('voicesearch_performance.log')
performance_formatter = logging.Formatter('%(asctime)s - %(filename)s:%(lineno)d - %(message)s')
performance_handler.setFormatter(performance_formatter)
performance_logger.addHandler(performance_handler)
performance_logger.propagate = False

# Try to import websockets for async WebSocket connection
try:
    import websockets
    from websockets.sync.client import connect as ws_connect
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("websockets not available - ElevenLabs Scribe V2 will not work. Install with: pip install websockets")

# Get silence timeout from environment (in milliseconds, convert to seconds)
ELEVENLABS_SILENCE_TIMEOUT_MS = int(os.getenv("SILENCE_TIMEOUT", "5000"))
ELEVENLABS_SILENCE_TIMEOUT_SEC = ELEVENLABS_SILENCE_TIMEOUT_MS / 1000.0

# Audio configuration - must match ElevenLabs requirements
ELEVENLABS_SAMPLE_RATE = 16000  # 16kHz for pcm_16000 format
ELEVENLABS_AUDIO_CHUNK_SIZE = 4096  # Match the working implementation

# ElevenLabs connection state
elevenlabs_ws = None
elevenlabs_thread: Optional[threading.Thread] = None
elevenlabs_session_start_time = None
elevenlabs_transcription_count = 0
elevenlabs_last_transcription_time = None
elevenlabs_last_audio_send_time = None
elevenlabs_socketio: Optional[SocketIO] = None
elevenlabs_current_transcript = ""
elevenlabs_connection_open = False
elevenlabs_session_started = threading.Event()
elevenlabs_silence_timer = None
elevenlabs_silence_timer_lock = threading.Lock()
elevenlabs_language = "Auto"
elevenlabs_audio_buffer = bytearray()
elevenlabs_audio_buffer_lock = threading.Lock()
elevenlabs_stop_event = threading.Event()


def reset_elevenlabs_silence_timer():
    """Reset the silence timeout timer when transcription is received"""
    global elevenlabs_silence_timer
    with elevenlabs_silence_timer_lock:
        if elevenlabs_silence_timer:
            elevenlabs_silence_timer.cancel()
        elevenlabs_silence_timer = threading.Timer(ELEVENLABS_SILENCE_TIMEOUT_SEC, handle_elevenlabs_silence_timeout)
        elevenlabs_silence_timer.start()


def stop_elevenlabs_silence_timer():
    """Stop the silence timeout timer"""
    global elevenlabs_silence_timer
    with elevenlabs_silence_timer_lock:
        if elevenlabs_silence_timer:
            elevenlabs_silence_timer.cancel()
            elevenlabs_silence_timer = None


def handle_elevenlabs_silence_timeout():
    """Handle silence timeout - automatically stop transcription"""
    global elevenlabs_ws, elevenlabs_session_start_time, elevenlabs_transcription_count
    global elevenlabs_last_transcription_time, elevenlabs_last_audio_send_time, elevenlabs_current_transcript
    global elevenlabs_connection_open, elevenlabs_socketio
    
    logger.info(f"ElevenLabs silence timeout reached ({ELEVENLABS_SILENCE_TIMEOUT_MS}ms). Stopping transcription automatically.")
    performance_logger.info(f"SILENCE_TIMEOUT | Timeout: {ELEVENLABS_SILENCE_TIMEOUT_MS}ms")
    
    # Close the WebSocket connection
    elevenlabs_connection_open = False
    elevenlabs_stop_event.set()
    
    if elevenlabs_ws:
        try:
            elevenlabs_ws.close()
            logger.info("ElevenLabs connection closed due to silence timeout")
        except Exception as e:
            logger.error(f"Error closing ElevenLabs connection on timeout: {e}")
        elevenlabs_ws = None
    
    # Clean up session tracking
    if elevenlabs_session_start_time:
        session_duration_ms = (time.perf_counter() - elevenlabs_session_start_time) * 1000
        logger.info(
            f"ElevenLabs session ended | Duration: {session_duration_ms:.2f}ms | "
            f"TotalTranscriptions: {elevenlabs_transcription_count} | Reason: SilenceTimeout"
        )
        performance_logger.info(
            f"SESSION_END | TotalDuration: {session_duration_ms:.2f}ms | "
            f"TotalTranscriptions: {elevenlabs_transcription_count} | Reason: SilenceTimeout"
        )
        elevenlabs_session_start_time = None
        elevenlabs_transcription_count = 0
        elevenlabs_last_transcription_time = None
        elevenlabs_last_audio_send_time = None
        elevenlabs_current_transcript = ""
    
    # Notify frontend to stop recording
    if elevenlabs_socketio:
        elevenlabs_socketio.emit('silence_timeout', {
            'message': f'Recording stopped due to {ELEVENLABS_SILENCE_TIMEOUT_MS}ms silence timeout',
            'api': 'ElevenLabs ScribeV2'
        })
    
    stop_elevenlabs_silence_timer()


def initialize_elevenlabs_connection(socketio_instance: SocketIO, language_name: str = "Auto"):
    """
    Initialize ElevenLabs Scribe V2 WebSocket connection
    
    Args:
        socketio_instance: Flask-SocketIO instance for emitting events
        language_name: Language for transcription. "Auto" for auto-detection.
    
    Returns:
        bool: True if connection initialization started successfully
    """
    global elevenlabs_ws, elevenlabs_thread, elevenlabs_session_start_time
    global elevenlabs_transcription_count, elevenlabs_last_transcription_time, elevenlabs_last_audio_send_time
    global elevenlabs_socketio, elevenlabs_current_transcript, elevenlabs_connection_open
    global elevenlabs_language, elevenlabs_audio_buffer, elevenlabs_session_started, elevenlabs_stop_event
    
    if not WEBSOCKETS_AVAILABLE:
        logger.error("websockets library not available - cannot initialize ElevenLabs connection")
        return False
    
    elevenlabs_socketio = socketio_instance
    elevenlabs_current_transcript = ""
    elevenlabs_language = language_name
    elevenlabs_session_started.clear()
    elevenlabs_stop_event.clear()
    
    # Clear audio buffer
    with elevenlabs_audio_buffer_lock:
        elevenlabs_audio_buffer = bytearray()
    
    # Get ElevenLabs API key from environment
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        logger.error("ELEVENLABS_API_KEY environment variable is not set")
        return False
    
    # Close existing connection if any
    if elevenlabs_ws:
        try:
            logger.info("Closing existing ElevenLabs connection")
            elevenlabs_ws.close()
        except Exception as e:
            logger.warning(f"Error closing existing ElevenLabs connection: {e}")
        elevenlabs_ws = None
    
    # Stop any existing silence timer
    stop_elevenlabs_silence_timer()
    
    # Reset session tracking
    elevenlabs_session_start_time = time.perf_counter()
    elevenlabs_transcription_count = 0
    elevenlabs_last_transcription_time = None
    elevenlabs_last_audio_send_time = None
    elevenlabs_current_transcript = ""
    elevenlabs_connection_open = False
    
    # Build WebSocket URL with proper query parameters
    model_id = "scribe_v2_realtime"
    ws_url = (
        f"wss://api.elevenlabs.io/v1/speech-to-text/realtime"
        f"?model_id={model_id}"
        f"&audio_format=pcm_16000"
        f"&commit_strategy=vad"
        f"&vad_silence_threshold_secs=1.0"
        f"&vad_threshold=0.5"
    )
    
    # Only add language_code if not Auto (omit for auto-detection)
    if language_name and language_name != "Auto":
        # Map language names to codes
        lang_map = {
            "English": "en", "German": "de", "Spanish": "es", "French": "fr",
            "Japanese": "ja", "Portuguese": "pt", "Russian": "ru", "Italian": "it",
            "Korean": "ko", "Hindi": "hi", "Chinese": "zh", "Dutch": "nl",
            "Swedish": "sv", "Finnish": "fi", "Danish": "da", "Norwegian": "no"
        }
        lang_code = lang_map.get(language_name)
        if lang_code:
            ws_url += f"&language_code={lang_code}"
    
    logger.info(f"Initializing ElevenLabs connection to: {ws_url}")
    
    def run_websocket():
        """Run WebSocket connection in a separate thread"""
        global elevenlabs_ws, elevenlabs_connection_open, elevenlabs_session_started
        global elevenlabs_current_transcript, elevenlabs_transcription_count
        global elevenlabs_last_transcription_time, elevenlabs_session_start_time
        
        try:
            headers = {"xi-api-key": api_key}
            elevenlabs_ws = ws_connect(ws_url, additional_headers=headers)
            logger.info("✅ Connected to ElevenLabs Scribe v2 Realtime")
            
            # Start receiving messages
            while not elevenlabs_stop_event.is_set():
                try:
                    message = elevenlabs_ws.recv(timeout=0.1)
                    if message:
                        handle_elevenlabs_message(message)
                except TimeoutError:
                    continue
                except Exception as e:
                    if elevenlabs_stop_event.is_set():
                        break
                    error_str = str(e).lower()
                    if "closed" in error_str or "connection" in error_str:
                        logger.info("ElevenLabs WebSocket connection closed")
                        break
                    logger.error(f"Error receiving ElevenLabs message: {e}")
                    break
        
        except Exception as e:
            logger.error(f"ElevenLabs WebSocket error: {e}")
            if elevenlabs_socketio:
                elevenlabs_socketio.emit('transcription_status', {
                    'status': 'error',
                    'message': f'ElevenLabs connection error: {e}'
                })
        finally:
            elevenlabs_connection_open = False
            stop_elevenlabs_silence_timer()
            if elevenlabs_session_start_time:
                session_duration_ms = (time.perf_counter() - elevenlabs_session_start_time) * 1000
                performance_logger.info(
                    f"SESSION_END | TotalDuration: {session_duration_ms:.2f}ms | "
                    f"TotalTranscriptions: {elevenlabs_transcription_count}"
                )
    
    # Start WebSocket in a separate thread
    elevenlabs_thread = threading.Thread(target=run_websocket, daemon=True)
    elevenlabs_thread.start()
    
    # Wait for session to start (with timeout)
    max_wait_time = 5.0
    if elevenlabs_session_started.wait(timeout=max_wait_time):
        logger.info("ElevenLabs WebSocket connection started and session ready")
        return True
    else:
        logger.warning(f"ElevenLabs session did not start within {max_wait_time}s - connection may still be establishing")
        return True


def handle_elevenlabs_message(message: str):
    """Handle incoming messages from ElevenLabs"""
    global elevenlabs_transcription_count, elevenlabs_last_transcription_time
    global elevenlabs_socketio, elevenlabs_current_transcript, elevenlabs_connection_open
    global elevenlabs_session_started, elevenlabs_session_start_time, elevenlabs_last_audio_send_time
    
    try:
        data = json.loads(message)
        message_type = data.get("type", data.get("message_type"))
        
        logger.debug(f"ElevenLabs received event: {message_type}")
        
        if message_type == "session_started":
            session_id = data.get("session_id", "N/A")
            logger.info(f"✅ ElevenLabs session started: {session_id}")
            config = data.get("config", {})
            if config:
                logger.debug(f"   Config: {json.dumps(config, indent=2)}")
            
            elevenlabs_connection_open = True
            elevenlabs_session_started.set()
            elevenlabs_session_start_time = time.perf_counter()
            
            # Start silence timeout timer
            reset_elevenlabs_silence_timer()
            
            # Log session start
            performance_logger.info(
                f"SESSION_START | Language: {elevenlabs_language} | Model: ElevenLabs Scribe V2 | Timestamp: {time.time()}"
            )
            
            # Notify frontend that connection is ready
            if elevenlabs_socketio:
                elevenlabs_socketio.emit('transcription_status', {'status': 'started', 'api': 'ElevenLabs ScribeV2'})
        
        elif message_type == "partial_transcript":
            text = data.get("text", "")
            if text:
                # Reset silence timer on partial transcript
                reset_elevenlabs_silence_timer()
                logger.info(f"ElevenLabs partial transcript: {text}")
                # Emit partial transcript for real-time feedback
                if elevenlabs_socketio:
                    elevenlabs_socketio.emit('transcription_update', {'transcription': text})
                    logger.info(f"✅ Emitted transcription_update event with partial: '{text}'")
        
        elif message_type in ("committed_transcript", "final_transcript", "committed_transcript_with_timestamps"):
            text = data.get("text", "")
            if text:
                elevenlabs_current_transcript = text
                current_time = time.perf_counter()
                
                # Calculate performance metrics
                time_since_start_ms = (current_time - elevenlabs_session_start_time) * 1000 if elevenlabs_session_start_time else 0
                if elevenlabs_last_transcription_time:
                    time_since_last_ms = (current_time - elevenlabs_last_transcription_time) * 1000
                else:
                    time_since_last_ms = 0
                
                if elevenlabs_last_audio_send_time:
                    transcription_response_time_ms = (current_time - elevenlabs_last_audio_send_time) * 1000
                else:
                    transcription_response_time_ms = 0
                
                elevenlabs_transcription_count += 1
                elevenlabs_last_transcription_time = current_time
                
                # Log performance metrics
                performance_logger.info(
                    f"TRANSCRIPTION | Count: {elevenlabs_transcription_count} | "
                    f"ResponseTime: {transcription_response_time_ms:.2f}ms | "
                    f"TimeSinceStart: {time_since_start_ms:.2f}ms | "
                    f"TimeSinceLast: {time_since_last_ms:.2f}ms | "
                    f"Text: \"{text}\""
                )
                
                # Reset silence timer
                reset_elevenlabs_silence_timer()
                
                logger.info(f"ElevenLabs final transcript: {text}")
                if elevenlabs_socketio:
                    elevenlabs_socketio.emit('transcription_update', {'transcription': text})
                    logger.info(f"✅ Emitted transcription_update event with final: '{text}'")
        
        elif message_type == "commit_throttled":
            logger.warning("⚠️ ElevenLabs commit throttled")
        
        elif message_type in ("error", "auth_error", "quota_exceeded", "transcriber_error", "input_error", "rate_limited"):
            error = data.get("error", data.get("message", "Unknown error"))
            logger.error(f"❌ ElevenLabs {message_type}: {error}")
            performance_logger.error(f"ERROR | Type: {message_type} | Message: {error}")
            if elevenlabs_socketio:
                elevenlabs_socketio.emit('transcription_status', {
                    'status': 'error',
                    'message': f'ElevenLabs error: {error}'
                })
        else:
            logger.debug(f"🔍 ElevenLabs message type '{message_type}': {json.dumps(data)[:200]}")
    
    except json.JSONDecodeError:
        logger.warning(f"⚠️ ElevenLabs received non-JSON message: {message[:200]}")
    except Exception as e:
        logger.error(f"Error processing ElevenLabs message: {e}")


def send_audio_to_elevenlabs(audio_data: bytes) -> bool:
    """
    Send audio data to ElevenLabs WebSocket
    
    Args:
        audio_data: Audio data in bytes (PCM16 format, 16kHz)
    
    Returns:
        bool: True if audio was sent successfully
    """
    global elevenlabs_ws, elevenlabs_last_audio_send_time, elevenlabs_connection_open, elevenlabs_audio_buffer
    
    if not elevenlabs_ws:
        logger.warning("ElevenLabs WebSocket connection is not initialized")
        return False
    
    # Check if session has started
    if not elevenlabs_connection_open:
        # Buffer audio while connection is establishing
        with elevenlabs_audio_buffer_lock:
            elevenlabs_audio_buffer.extend(audio_data)
            if len(elevenlabs_audio_buffer) > ELEVENLABS_AUDIO_CHUNK_SIZE * 10:
                elevenlabs_audio_buffer = elevenlabs_audio_buffer[-ELEVENLABS_AUDIO_CHUNK_SIZE * 5:]
        logger.debug(f"ElevenLabs connection establishing - buffered {len(audio_data)} bytes")
        return False
    
    try:
        # Add incoming data to buffer
        with elevenlabs_audio_buffer_lock:
            elevenlabs_audio_buffer.extend(audio_data)
        
        # Send buffered audio in chunks
        bytes_sent = 0
        while True:
            with elevenlabs_audio_buffer_lock:
                if len(elevenlabs_audio_buffer) < ELEVENLABS_AUDIO_CHUNK_SIZE:
                    break
                chunk = bytes(elevenlabs_audio_buffer[:ELEVENLABS_AUDIO_CHUNK_SIZE])
                elevenlabs_audio_buffer = elevenlabs_audio_buffer[ELEVENLABS_AUDIO_CHUNK_SIZE:]
            
            # Encode audio as base64 (ElevenLabs format)
            audio_base64 = base64.b64encode(chunk).decode('utf-8')
            
            # Message format per ElevenLabs API
            message = {
                "message_type": "input_audio_chunk",
                "audio_base_64": audio_base64,
                "sample_rate": ELEVENLABS_SAMPLE_RATE
            }
            
            # Track when audio is sent
            elevenlabs_last_audio_send_time = time.perf_counter()
            elevenlabs_ws.send(json.dumps(message))
            bytes_sent += len(chunk)
        
        if bytes_sent > 0:
            logger.debug(f"📤 Sent {bytes_sent} bytes to ElevenLabs")
        return True
    
    except Exception as e:
        error_msg = str(e)
        if "closed" in error_msg.lower():
            logger.warning(f"ElevenLabs WebSocket connection is closed - cannot send audio")
            elevenlabs_connection_open = False
            if elevenlabs_socketio:
                elevenlabs_socketio.emit('transcription_status', {
                    'status': 'error',
                    'message': 'ElevenLabs connection closed. Please restart transcription.'
                })
        else:
            logger.error(f"Error sending audio to ElevenLabs: {e}")
        return False


def close_elevenlabs_connection():
    """Close ElevenLabs WebSocket connection"""
    global elevenlabs_ws, elevenlabs_thread, elevenlabs_current_transcript
    global elevenlabs_connection_open, elevenlabs_audio_buffer, elevenlabs_stop_event
    
    # Signal stop
    elevenlabs_stop_event.set()
    elevenlabs_connection_open = False
    
    # Stop silence timer
    stop_elevenlabs_silence_timer()
    
    # Clear audio buffer
    with elevenlabs_audio_buffer_lock:
        elevenlabs_audio_buffer = bytearray()
    
    # Close WebSocket
    ws_to_close = elevenlabs_ws
    elevenlabs_ws = None
    
    if ws_to_close:
        try:
            logger.info("Closing ElevenLabs WebSocket connection")
            ws_to_close.close()
        except Exception as e:
            logger.debug(f"Error closing ElevenLabs connection (may be already closed): {e}")
    
    # Reset transcript
    elevenlabs_current_transcript = ""
    
    logger.info("🔌 ElevenLabs disconnected")
