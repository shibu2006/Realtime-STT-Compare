"""
Azure OpenAI Realtime API Handler
Handles WebSocket connections and audio transcription using Azure OpenAI's Realtime API
"""
import os
import json
import base64
import logging
import threading
import time
import io
from typing import Optional, TYPE_CHECKING
from flask_socketio import SocketIO

logger = logging.getLogger(__name__)

# Configure performance logger (same as in voicesearch_app.py)
performance_logger = logging.getLogger('azure_performance')
performance_logger.setLevel(logging.INFO)
performance_handler = logging.FileHandler('voicesearch_performance.log')
performance_formatter = logging.Formatter('%(asctime)s - %(message)s')
performance_handler.setFormatter(performance_formatter)
performance_logger.addHandler(performance_handler)
performance_logger.propagate = False  # Don't propagate to root logger

# Try to import pydub for audio conversion (optional)
try:
    from pydub import AudioSegment
    PYDUB_AVAILABLE = True
except ImportError:
    PYDUB_AVAILABLE = False
    logger.warning("pydub not available - WebM to PCM16 conversion will not work. Install with: pip install pydub")
    logger.warning("Note: pydub requires ffmpeg to be installed on the system")

if TYPE_CHECKING:
    from websocket import WebSocketApp  # type: ignore
else:
    try:
        from websocket import WebSocketApp  # type: ignore
    except ImportError:
        import websocket  # type: ignore
        WebSocketApp = websocket.WebSocketApp  # type: ignore

# Get silence timeout from environment (in milliseconds, convert to seconds)
AZURE_SILENCE_TIMEOUT_MS = int(os.getenv("SILENCE_TIMEOUT", "5000"))  # Default 5 seconds if not set
AZURE_SILENCE_TIMEOUT_SEC = AZURE_SILENCE_TIMEOUT_MS / 1000.0

# Audio chunk size to match CLI (1024 samples * 2 bytes per sample = 2048 bytes)
AZURE_AUDIO_CHUNK_SIZE = 1024 * 2  # 2048 bytes for PCM16

# Azure OpenAI connection state
azure_openai_ws: Optional['WebSocketApp'] = None  # type: ignore
azure_openai_thread: Optional[threading.Thread] = None
azure_session_start_time = None
azure_transcription_count = 0
azure_last_transcription_time = None
azure_last_audio_send_time = None
azure_socketio: Optional[SocketIO] = None
azure_current_transcript = ""  # Accumulate transcription deltas
azure_connection_open = False  # Track if WebSocket connection is actually open
azure_silence_timer = None  # Timer for silence timeout
azure_silence_timer_lock = threading.Lock()  # Lock for thread-safe timer operations
azure_language = "Auto"  # Track user's language selection (for logging only - model auto-detects)
azure_model = "gpt-4o-mini-transcribe"  # Track current model for logging
azure_audio_buffer = bytearray()  # Buffer for accumulating audio data
azure_audio_buffer_lock = threading.Lock()  # Lock for thread-safe buffer operations


def reset_azure_silence_timer():
    """Reset the silence timeout timer when transcription is received"""
    global azure_silence_timer
    with azure_silence_timer_lock:
        if azure_silence_timer:
            azure_silence_timer.cancel()
        azure_silence_timer = threading.Timer(AZURE_SILENCE_TIMEOUT_SEC, handle_azure_silence_timeout)
        azure_silence_timer.start()

def stop_azure_silence_timer():
    """Stop the silence timeout timer"""
    global azure_silence_timer
    with azure_silence_timer_lock:
        if azure_silence_timer:
            azure_silence_timer.cancel()
            azure_silence_timer = None

def handle_azure_silence_timeout():
    """Handle silence timeout - automatically stop transcription"""
    global azure_openai_ws, azure_session_start_time, azure_transcription_count
    global azure_last_transcription_time, azure_last_audio_send_time, azure_current_transcript
    global azure_connection_open, azure_socketio
    
    logger.info(f"Azure OpenAI silence timeout reached ({AZURE_SILENCE_TIMEOUT_MS}ms). Stopping transcription automatically.")
    performance_logger.info(f"SILENCE_TIMEOUT | Timeout: {AZURE_SILENCE_TIMEOUT_MS}ms")
    
    # Close the WebSocket connection
    azure_connection_open = False
    if azure_openai_ws:
        try:
            azure_openai_ws.close()
            logger.info("Azure OpenAI connection closed due to silence timeout")
        except Exception as e:
            logger.error(f"Error closing Azure OpenAI connection on timeout: {e}")
        azure_openai_ws = None
    
    # Clean up session tracking
    if azure_session_start_time:
        session_duration_ms = (time.perf_counter() - azure_session_start_time) * 1000
        logger.info(
            f"Azure OpenAI session ended | Duration: {session_duration_ms:.2f}ms | "
            f"TotalTranscriptions: {azure_transcription_count} | Reason: SilenceTimeout"
        )
        performance_logger.info(
            f"SESSION_END | TotalDuration: {session_duration_ms:.2f}ms | "
            f"TotalTranscriptions: {azure_transcription_count} | Reason: SilenceTimeout"
        )
        azure_session_start_time = None
        azure_transcription_count = 0
        azure_last_transcription_time = None
        azure_last_audio_send_time = None
        azure_current_transcript = ""
    
    # Notify frontend to stop recording
    if azure_socketio:
        azure_socketio.emit('silence_timeout', {
            'message': f'Recording stopped due to {AZURE_SILENCE_TIMEOUT_MS}ms silence timeout',
            'api': 'Azure OpenAI'
        })
    
    stop_azure_silence_timer()


def initialize_azure_openai_connection(socketio_instance: SocketIO, language_name: str = "Auto"):
    """
    Initialize Azure OpenAI WebSocket connection with automatic language detection
    
    Args:
        socketio_instance: Flask-SocketIO instance for emitting events
        language_name: User's language selection for logging/tracking purposes only.
                      The model always auto-detects the actual spoken language.
                      Default: "Auto"
    
    Note:
        The Azure OpenAI gpt-4o-mini-transcribe model automatically detects and
        transcribes in any supported language without needing language hints.
    """
    global azure_openai_ws, azure_openai_thread, azure_session_start_time
    global azure_transcription_count, azure_last_transcription_time, azure_last_audio_send_time
    global azure_socketio, azure_current_transcript, azure_connection_open, azure_language, azure_model
    
    azure_socketio = socketio_instance
    azure_current_transcript = ""  # Reset transcript accumulator
    azure_language = language_name  # Store language for logging
    
    # Clear audio buffer
    with azure_audio_buffer_lock:
        azure_audio_buffer = bytearray()
    
    # Get Azure OpenAI credentials from environment
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    
    if not api_key:
        logger.error("AZURE_OPENAI_API_KEY environment variable is not set")
        return False
    
    if not endpoint:
        logger.error("AZURE_OPENAI_ENDPOINT environment variable is not set")
        return False
    
    # Parse endpoint: remove https:// or http:// if present, remove trailing slashes
    endpoint_host = endpoint.strip()
    if '://' in endpoint_host:
        endpoint_host = endpoint_host.split('://')[1]
    # Remove any trailing path or slashes
    endpoint_host = endpoint_host.split('/')[0].rstrip('/')
    
    # Construct WebSocket URL
    url = f"wss://{endpoint_host}/openai/realtime?api-version=2025-04-01-preview&intent=transcription"
    headers = {"api-key": api_key}
    
    logger.info(f"Initializing Azure OpenAI connection to: {url}")
    
    # Close existing connection if any
    if azure_openai_ws:
        try:
            logger.info("Closing existing Azure OpenAI connection")
            azure_openai_ws.close()
        except Exception as e:
            logger.warning(f"Error closing existing Azure OpenAI connection: {e}")
        azure_openai_ws = None
    
    # Stop any existing silence timer
    stop_azure_silence_timer()
    
    # Reset session tracking
    azure_session_start_time = time.perf_counter()
    azure_transcription_count = 0
    azure_last_transcription_time = None
    azure_last_audio_send_time = None
    azure_current_transcript = ""  # Reset transcript accumulator
    azure_connection_open = False  # Reset connection state
    
    def on_open(ws):
        """Called when WebSocket connection is opened"""
        global azure_session_start_time, azure_connection_open, azure_language, azure_model, azure_openai_ws, azure_audio_buffer
        
        # Check if this connection is still wanted (race condition protection)
        # If azure_openai_ws is None, it means the connection was closed before it opened
        if azure_openai_ws is None or azure_openai_ws != ws:
            logger.warning("Azure OpenAI WebSocket opened but connection is no longer needed - closing")
            try:
                ws.close()
            except:
                pass
            return
        
        logger.info("Azure OpenAI WebSocket connection opened")
        azure_session_start_time = time.perf_counter()
        
        # Clear any buffered audio from previous sessions
        with azure_audio_buffer_lock:
            azure_audio_buffer = bytearray()
        
        # Log session start to performance log
        performance_logger.info(
            f"SESSION_START | Language: {azure_language} | Model: {azure_model} | Timestamp: {time.time()}"
        )
        
        # Start silence timeout timer
        reset_azure_silence_timer()
        
        # Send session configuration
        # Note: No language prompt is added - we let the model auto-detect the language
        # The azure_language variable is only used for logging/tracking user selection
        session_config = {
            "type": "transcription_session.update",
            "session": {
                "input_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "gpt-4o-mini-transcribe"
                    # No prompt - Azure OpenAI gpt-4o-mini-transcribe auto-detects language
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    # prefix_padding_ms: Include audio BEFORE speech detection starts
                    # This is critical for web UI where users start speaking immediately
                    # 1000ms ensures we don't miss the first words like "My name is..."
                    "prefix_padding_ms": 1000,
                    # silence_duration_ms: How long to wait for silence before ending turn
                    # 500ms gives better sentence boundaries and prevents premature cutoff
                    "silence_duration_ms": 500
                }
            }
        }
        
        logger.info(f"Azure OpenAI using auto-detection for language (user selected: {azure_language})")
        
        try:
            # Double-check connection is still valid before sending
            if azure_openai_ws is None or azure_openai_ws != ws:
                logger.warning("Azure OpenAI connection closed during config send - aborting")
                azure_connection_open = False
                return
            
            # Verify the socket is still connected
            if not hasattr(ws, 'sock') or ws.sock is None:
                logger.warning("Azure OpenAI WebSocket socket is None - connection closed")
                azure_connection_open = False
                return
            
            ws.send(json.dumps(session_config))
            logger.info("Azure OpenAI session configuration sent")
            
            # Mark connection as open AFTER config is sent successfully
            azure_connection_open = True
            
        except Exception as e:
            logger.error(f"Error sending session configuration: {e}")
            azure_connection_open = False
    
    def on_message(ws, message):
        """Handle incoming messages from Azure OpenAI"""
        global azure_transcription_count, azure_last_transcription_time, azure_socketio, azure_current_transcript
        try:
            data = json.loads(message)
            event_type = data.get("type", "")
            
            # Log all event types for debugging
            logger.info(f"Azure OpenAI received event: {event_type}")
            if event_type and "transcription" in event_type.lower():
                logger.info(f"Azure OpenAI transcription event: {event_type} | Data: {json.dumps(data)[:200]}")
            
            # Handle incremental transcription updates (deltas)
            if event_type == "conversation.item.input_audio_transcription.delta":
                transcript_piece = data.get("delta", "")
                if transcript_piece and azure_socketio:
                    # Accumulate the delta into the current transcript
                    azure_current_transcript += transcript_piece
                    current_time = time.perf_counter()
                    
                    # Calculate performance metrics
                    time_since_start_ms = (current_time - azure_session_start_time) * 1000 if azure_session_start_time else 0
                    if azure_last_transcription_time:
                        time_since_last_ms = (current_time - azure_last_transcription_time) * 1000
                    else:
                        time_since_last_ms = 0
                    
                    # Calculate transcription response time (time from audio send to transcription received)
                    if azure_last_audio_send_time:
                        transcription_response_time_ms = (current_time - azure_last_audio_send_time) * 1000
                    else:
                        transcription_response_time_ms = 0
                    
                    azure_transcription_count += 1
                    azure_last_transcription_time = current_time
                    
                    # Log performance metrics
                    performance_logger.info(
                        f"TRANSCRIPTION | Count: {azure_transcription_count} | "
                        f"ResponseTime: {transcription_response_time_ms:.2f}ms | "
                        f"TimeSinceStart: {time_since_start_ms:.2f}ms | "
                        f"TimeSinceLast: {time_since_last_ms:.2f}ms | "
                        f"Text: \"{azure_current_transcript}\""
                    )
                    
                    # Reset silence timer when transcription is received
                    reset_azure_silence_timer()
                    
                    logger.info(f"Azure OpenAI transcript delta: '{transcript_piece}' | Full: '{azure_current_transcript}'")
                    # Emit the accumulated full transcript (similar to Deepgram behavior)
                    if azure_socketio:
                        azure_socketio.emit('transcription_update', {'transcription': azure_current_transcript})
                        logger.info(f"✅ Emitted transcription_update event with: '{azure_current_transcript}'")
                    else:
                        logger.error("❌ azure_socketio is None - cannot emit transcription_update!")
            
            # Handle completed transcription
            elif event_type == "conversation.item.input_audio_transcription.completed":
                transcript = data.get("transcript", "")
                if transcript and azure_socketio:
                    # Update accumulated transcript with final version
                    azure_current_transcript = transcript
                    current_time = time.perf_counter()
                    
                    # Calculate performance metrics
                    time_since_start_ms = (current_time - azure_session_start_time) * 1000 if azure_session_start_time else 0
                    if azure_last_transcription_time:
                        time_since_last_ms = (current_time - azure_last_transcription_time) * 1000
                    else:
                        time_since_last_ms = 0
                    
                    # Calculate transcription response time
                    if azure_last_audio_send_time:
                        transcription_response_time_ms = (current_time - azure_last_audio_send_time) * 1000
                    else:
                        transcription_response_time_ms = 0
                    
                    azure_transcription_count += 1
                    azure_last_transcription_time = current_time
                    
                    # Log performance metrics
                    performance_logger.info(
                        f"TRANSCRIPTION | Count: {azure_transcription_count} | "
                        f"ResponseTime: {transcription_response_time_ms:.2f}ms | "
                        f"TimeSinceStart: {time_since_start_ms:.2f}ms | "
                        f"TimeSinceLast: {time_since_last_ms:.2f}ms | "
                        f"Text: \"{azure_current_transcript}\""
                    )
                    
                    # Reset silence timer when transcription is received
                    reset_azure_silence_timer()
                    logger.info(f"Azure OpenAI completed transcript: {transcript}")
                    if azure_socketio:
                        azure_socketio.emit('transcription_update', {'transcription': azure_current_transcript})
                        logger.info(f"✅ Emitted transcription_update event with completed: '{azure_current_transcript}'")
                    else:
                        logger.error("❌ azure_socketio is None - cannot emit transcription_update!")
            
            # Handle conversation item updates
            elif event_type == "conversation.item.input_audio_transcription.final":
                transcript = data.get("transcript", "")
                if transcript and azure_socketio:
                    azure_current_transcript = transcript
                    current_time = time.perf_counter()
                    
                    # Calculate performance metrics
                    time_since_start_ms = (current_time - azure_session_start_time) * 1000 if azure_session_start_time else 0
                    if azure_last_transcription_time:
                        time_since_last_ms = (current_time - azure_last_transcription_time) * 1000
                    else:
                        time_since_last_ms = 0
                    
                    # Calculate transcription response time
                    if azure_last_audio_send_time:
                        transcription_response_time_ms = (current_time - azure_last_audio_send_time) * 1000
                    else:
                        transcription_response_time_ms = 0
                    
                    azure_transcription_count += 1
                    azure_last_transcription_time = current_time
                    
                    # Log performance metrics
                    performance_logger.info(
                        f"TRANSCRIPTION | Count: {azure_transcription_count} | "
                        f"ResponseTime: {transcription_response_time_ms:.2f}ms | "
                        f"TimeSinceStart: {time_since_start_ms:.2f}ms | "
                        f"TimeSinceLast: {time_since_last_ms:.2f}ms | "
                        f"Text: \"{azure_current_transcript}\""
                    )
                    
                    # Reset silence timer when transcription is received
                    reset_azure_silence_timer()
                    logger.info(f"Azure OpenAI final transcript: {transcript}")
                    if azure_socketio:
                        azure_socketio.emit('transcription_update', {'transcription': azure_current_transcript})
                        logger.info(f"✅ Emitted transcription_update event with final: '{azure_current_transcript}'")
                    else:
                        logger.error("❌ azure_socketio is None - cannot emit transcription_update!")
            
            # Handle conversation item created (new item started - reset accumulator)
            elif event_type == "conversation.item.created":
                # Reset transcript accumulator when a new item is created
                azure_current_transcript = ""
                logger.info("Azure OpenAI new conversation item created - resetting transcript")
            
            # Handle other item events
            elif event_type == "item":
                item_data = data.get("item", {})
                if isinstance(item_data, dict):
                    # Check for transcript in various possible locations
                    transcript = item_data.get("transcript") or item_data.get("transcription")
                    if transcript and azure_socketio:
                        azure_current_transcript = transcript
                        current_time = time.perf_counter()
                        
                        # Calculate performance metrics
                        time_since_start_ms = (current_time - azure_session_start_time) * 1000 if azure_session_start_time else 0
                        if azure_last_transcription_time:
                            time_since_last_ms = (current_time - azure_last_transcription_time) * 1000
                        else:
                            time_since_last_ms = 0
                        
                        # Calculate transcription response time
                        if azure_last_audio_send_time:
                            transcription_response_time_ms = (current_time - azure_last_audio_send_time) * 1000
                        else:
                            transcription_response_time_ms = 0
                        
                        azure_transcription_count += 1
                        azure_last_transcription_time = current_time
                        
                        # Log performance metrics
                        performance_logger.info(
                            f"TRANSCRIPTION | Count: {azure_transcription_count} | "
                            f"ResponseTime: {transcription_response_time_ms:.2f}ms | "
                            f"TimeSinceStart: {time_since_start_ms:.2f}ms | "
                            f"TimeSinceLast: {time_since_last_ms:.2f}ms | "
                            f"Text: \"{azure_current_transcript}\""
                        )
                        
                        # Reset silence timer when transcription is received
                        reset_azure_silence_timer()
                        logger.info(f"Azure OpenAI item transcript: {transcript}")
                        if azure_socketio:
                            azure_socketio.emit('transcription_update', {'transcription': azure_current_transcript})
                            logger.info(f"✅ Emitted transcription_update event with item: '{azure_current_transcript}'")
                        else:
                            logger.error("❌ azure_socketio is None - cannot emit transcription_update!")
        
        except Exception as e:
            logger.error(f"Error processing Azure OpenAI message: {e}")
            logger.exception("Full traceback:")
    
    def on_error(ws, error):
        """Handle WebSocket errors"""
        global azure_connection_open, azure_openai_ws
        
        # Filter out expected race condition errors (connection closed during startup)
        error_str = str(error) if error else ""
        if "NoneType" in error_str and "sock" in error_str:
            # This is expected when connection is closed quickly after starting
            logger.debug(f"Azure OpenAI WebSocket closed during connection - ignoring race condition error")
            azure_connection_open = False
            return
        
        logger.error(f"Azure OpenAI WebSocket error: {error}")
        # Log error to performance log
        error_msg = error_str if error_str else "Unknown error"
        if isinstance(error, dict):
            error_msg = json.dumps(error)
        performance_logger.error(f"ERROR | Message: {error_msg}")
        azure_connection_open = False
        
        # Only notify frontend for real errors, not race conditions
        if azure_socketio and azure_openai_ws is not None:
            azure_socketio.emit('transcription_status', {
                'status': 'error',
                'message': f'Azure OpenAI connection error: {error}'
            })
    
    def on_close(ws, close_status_code, close_msg):
        """Handle WebSocket close"""
        global azure_session_start_time, azure_transcription_count, azure_last_transcription_time, azure_current_transcript, azure_connection_open
        logger.info(f"Azure OpenAI WebSocket connection closed: {close_status_code} - {close_msg}")
        azure_connection_open = False
        
        # Stop silence timer when connection closes
        stop_azure_silence_timer()
        
        if azure_session_start_time:
            session_duration_ms = (time.perf_counter() - azure_session_start_time) * 1000
            logger.info(
                f"Azure OpenAI session ended | Duration: {session_duration_ms:.2f}ms | "
                f"TotalTranscriptions: {azure_transcription_count}"
            )
            performance_logger.info(
                f"SESSION_END | TotalDuration: {session_duration_ms:.2f}ms | "
                f"TotalTranscriptions: {azure_transcription_count}"
            )
            azure_session_start_time = None
            azure_transcription_count = 0
            azure_last_transcription_time = None
            azure_current_transcript = ""  # Reset transcript accumulator
    
    try:
        # Create WebSocket connection
        azure_openai_ws = WebSocketApp(
            url,
            header=headers,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        
        # Start WebSocket in a separate thread
        def run_websocket():
            if azure_openai_ws:  # Type guard
                azure_openai_ws.run_forever()
        
        azure_openai_thread = threading.Thread(target=run_websocket, daemon=True)
        azure_openai_thread.start()
        
        # Wait for connection to open (with timeout)
        max_wait_time = 5.0  # Maximum wait time in seconds
        wait_interval = 0.1  # Check every 100ms
        waited_time = 0.0
        
        while not azure_connection_open and waited_time < max_wait_time:
            time.sleep(wait_interval)
            waited_time += wait_interval
        
        if azure_connection_open:
            logger.info("Azure OpenAI WebSocket connection started and opened successfully")
            return True
        else:
            logger.warning(f"Azure OpenAI WebSocket connection started but did not open within {max_wait_time}s")
            logger.warning("Connection may have failed - check logs for errors")
            # Don't return False yet - connection might still open asynchronously
            # Return True but connection state will prevent sending until it opens
            return True
    
    except Exception as e:
        logger.error(f"Failed to initialize Azure OpenAI connection: {type(e).__name__}: {e}")
        logger.exception("Full traceback:")
        azure_openai_ws = None
        return False


def convert_webm_to_pcm16(webm_data: bytes) -> Optional[bytes]:
    """
    Convert WebM audio to PCM16 format
    
    Args:
        webm_data: WebM audio data in bytes
        
    Returns:
        PCM16 audio data in bytes, or None if conversion fails
    """
    if not PYDUB_AVAILABLE:
        logger.warning("⚠️ pydub not available - cannot convert WebM to PCM16. Audio may not work correctly.")
        logger.warning("Install pydub with: pip install pydub")
        logger.warning("Note: pydub requires ffmpeg to be installed on the system")
        return None
    
    try:
        # Load WebM audio
        # AudioSegment is only available if PYDUB_AVAILABLE is True
        if not PYDUB_AVAILABLE:
            return None
        from pydub import AudioSegment  # type: ignore
        audio = AudioSegment.from_file(io.BytesIO(webm_data), format="webm")
        
        # Convert to PCM16: 16-bit, mono, 24kHz (matching Azure OpenAI requirements)
        audio = audio.set_sample_width(2)  # 16-bit = 2 bytes
        audio = audio.set_channels(1)  # Mono
        audio = audio.set_frame_rate(24000)  # 24kHz
        
        # Export to raw PCM16 bytes
        pcm16_data = audio.raw_data
        
        logger.debug(f"Converted WebM to PCM16: {len(webm_data)} bytes -> {len(pcm16_data)} bytes")
        return pcm16_data
    
    except Exception as e:
        logger.error(f"Error converting WebM to PCM16: {e}")
        logger.exception("Full traceback:")
        return None


def is_likely_pcm16(audio_data: bytes) -> bool:
    """
    Heuristic to check if audio data is likely PCM16 format.
    PCM16 data should be divisible by 2 (16-bit = 2 bytes per sample)
    and have reasonable size patterns.
    """
    # PCM16 data should be divisible by 2 (16-bit samples)
    if len(audio_data) % 2 != 0:
        return False
    
    # Check if the data looks like raw PCM (not a container format)
    # WebM files typically start with specific headers, PCM16 is raw audio
    # This is a simple heuristic - if it's not obviously WebM, assume PCM16
    if len(audio_data) > 4:
        # WebM files start with specific bytes
        webm_signature = audio_data[:4]
        if webm_signature == b'\x1a\x45\xdf\xa3':  # WebM container signature
            return False
    
    return True


def send_audio_to_azure_openai(audio_data: bytes):
    """
    Send audio data to Azure OpenAI WebSocket
    
    Args:
        audio_data: Audio data in bytes (PCM16 from Web Audio API)
    """
    global azure_openai_ws, azure_last_audio_send_time, azure_connection_open, azure_audio_buffer
    
    if not azure_openai_ws:
        logger.warning("Azure OpenAI WebSocket connection is not initialized")
        return False
    
    # Check if connection is actually open
    if not azure_connection_open:
        # Buffer audio while connection is establishing
        with azure_audio_buffer_lock:
            azure_audio_buffer.extend(audio_data)
            if len(azure_audio_buffer) > AZURE_AUDIO_CHUNK_SIZE * 10:
                # Prevent buffer from growing too large - keep only recent audio
                azure_audio_buffer = azure_audio_buffer[-AZURE_AUDIO_CHUNK_SIZE * 5:]
        logger.debug(f"Azure OpenAI connection establishing - buffered {len(audio_data)} bytes")
        return False
    
    # Double-check socket state before sending
    try:
        if not hasattr(azure_openai_ws, 'sock') or azure_openai_ws.sock is None:
            logger.debug("Azure OpenAI WebSocket socket is None - connection not ready")
            azure_connection_open = False
            return False
        
        if not azure_openai_ws.sock.connected:
            logger.warning("Azure OpenAI WebSocket socket is not connected")
            azure_connection_open = False
            return False
    except (AttributeError, Exception) as e:
        logger.debug(f"Cannot verify Azure OpenAI WebSocket socket state: {e}")
        azure_connection_open = False
        return False
    
    try:
        # Add incoming data to buffer
        with azure_audio_buffer_lock:
            azure_audio_buffer.extend(audio_data)
        
        # Send buffered audio in chunks matching CLI behavior
        bytes_sent = 0
        while True:
            with azure_audio_buffer_lock:
                if len(azure_audio_buffer) < AZURE_AUDIO_CHUNK_SIZE:
                    break
                chunk = bytes(azure_audio_buffer[:AZURE_AUDIO_CHUNK_SIZE])
                azure_audio_buffer = azure_audio_buffer[AZURE_AUDIO_CHUNK_SIZE:]
            
            # Encode audio chunk as base64
            audio_base64 = base64.b64encode(chunk).decode('utf-8')
            
            # Send audio buffer append message
            message = {
                "type": "input_audio_buffer.append",
                "audio": audio_base64
            }
            
            # Track when audio is sent for response time calculation
            azure_last_audio_send_time = time.perf_counter()
            azure_openai_ws.send(json.dumps(message))
            bytes_sent += len(chunk)
        
        if bytes_sent > 0:
            logger.debug(f"📤 Sent {bytes_sent} bytes to Azure OpenAI")
        return True
    
    except Exception as e:
        error_msg = str(e)
        if "closed" in error_msg.lower() or "socket is already closed" in error_msg.lower():
            logger.warning(f"Azure OpenAI WebSocket connection is closed - cannot send audio. Error: {error_msg}")
            azure_connection_open = False
            if azure_socketio:
                azure_socketio.emit('transcription_status', {
                    'status': 'error',
                    'message': 'Azure OpenAI connection closed. Please restart transcription.'
                })
        else:
            logger.error(f"Error sending audio data to Azure OpenAI: {e}")
            logger.exception("Full traceback:")
        return False


def close_azure_openai_connection():
    """Close Azure OpenAI WebSocket connection"""
    global azure_openai_ws, azure_openai_thread, azure_current_transcript, azure_connection_open, azure_audio_buffer
    
    # Set connection_open to False first to prevent new operations
    azure_connection_open = False
    
    # Stop silence timer when manually closing connection
    stop_azure_silence_timer()
    
    # Clear audio buffer
    with azure_audio_buffer_lock:
        azure_audio_buffer = bytearray()
    
    # Save reference and clear global immediately to prevent race conditions
    ws_to_close = azure_openai_ws
    azure_openai_ws = None
    
    if ws_to_close:
        try:
            logger.info("Closing Azure OpenAI WebSocket connection")
            ws_to_close.close()
        except Exception as e:
            # Ignore errors during close - connection might already be closed
            logger.debug(f"Error closing Azure OpenAI connection (may be already closed): {e}")
    
    # Reset transcript accumulator
    azure_current_transcript = ""
    
    if azure_openai_thread and azure_openai_thread.is_alive():
        # Thread will terminate when WebSocket closes
        pass
