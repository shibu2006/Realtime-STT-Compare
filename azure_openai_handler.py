"""
Azure OpenAI Realtime API Handler
Handles WebSocket connections and audio transcription using Azure OpenAI's Realtime API
Updated to support per-session isolation for multiple concurrent users
"""
import os
import json
import base64
import logging
import threading
import time
import io
from typing import Optional, TYPE_CHECKING, Dict
from flask_socketio import SocketIO

logger = logging.getLogger(__name__)

# Configure performance logger (only add handler if not already added)
performance_logger = logging.getLogger('azure_performance')
performance_logger.setLevel(logging.INFO)
if not performance_logger.handlers:
    performance_handler = logging.FileHandler('voicesearch_performance.log')
    performance_formatter = logging.Formatter('%(asctime)s - %(filename)s:%(lineno)d - %(message)s')
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

# Session storage for Azure OpenAI connections - each session gets isolated state
azure_sessions: Dict[str, 'AzureSession'] = {}
azure_sessions_lock = threading.Lock()

class AzureSession:
    def __init__(self, session_id: str, socketio: SocketIO):
        self.session_id = session_id
        self.socketio = socketio
        self.ws: Optional['WebSocketApp'] = None
        self.thread: Optional[threading.Thread] = None
        self.session_start_time = None
        self.transcription_count = 0
        self.last_transcription_time = None
        self.last_audio_send_time = None
        self.current_transcript = ""  # Full transcript shown to user (accumulated + current segment)
        self.accumulated_transcript = ""  # Finalized/completed segments only
        self.current_segment_transcript = ""  # Current segment being transcribed (from deltas)
        self.connection_open = False
        self.silence_timer = None
        self.silence_timer_started = False  # Track if silence timer has been started (to start only on first audio)
        self.language = "Auto"
        self.model = "gpt-4o-mini-transcribe"
        self.audio_buffer = bytearray()
        self.audio_buffer_lock = threading.Lock()
        
    def reset_performance_metrics(self):
        """Reset performance tracking for new session"""
        self.session_start_time = time.perf_counter()
        self.transcription_count = 0
        self.last_transcription_time = None
        self.last_audio_send_time = None
        self.current_transcript = ""
        self.accumulated_transcript = ""
        self.current_segment_transcript = ""
        self.silence_timer_started = False  # Reset the silence timer started flag

def get_azure_session(session_id: str, socketio: SocketIO) -> AzureSession:
    """Get or create Azure session for user"""
    with azure_sessions_lock:
        if session_id not in azure_sessions:
            azure_sessions[session_id] = AzureSession(session_id, socketio)
        return azure_sessions[session_id]

def cleanup_azure_session(session_id: str):
    """Clean up Azure session on disconnect"""
    with azure_sessions_lock:
        if session_id in azure_sessions:
            session = azure_sessions[session_id]
            # Clean up any active connections
            if session.silence_timer:
                session.silence_timer.cancel()
            if session.ws:
                try:
                    session.ws.close()
                except Exception as e:
                    logger.error(f"Error closing Azure connection for session {session_id}: {e}")
            del azure_sessions[session_id]

def reset_azure_silence_timer(session: AzureSession):
    """Reset the silence timeout timer when transcription is received or audio is sent"""
    if session.silence_timer:
        session.silence_timer.cancel()
    session.silence_timer = threading.Timer(AZURE_SILENCE_TIMEOUT_SEC, lambda: handle_azure_silence_timeout(session))
    session.silence_timer.start()
    session.silence_timer_started = True

def stop_azure_silence_timer(session: AzureSession):
    """Stop the silence timeout timer"""
    if session.silence_timer:
        session.silence_timer.cancel()
        session.silence_timer = None
    session.silence_timer_started = False

def handle_azure_silence_timeout(session: AzureSession):
    """Handle silence timeout - automatically stop transcription for specific session"""
    logger.info(f"Azure OpenAI silence timeout reached ({AZURE_SILENCE_TIMEOUT_MS}ms) for session {session.session_id}. Stopping transcription automatically.")
    performance_logger.info(f"SILENCE_TIMEOUT | Session: {session.session_id} | Timeout: {AZURE_SILENCE_TIMEOUT_MS}ms")
    
    # Close the WebSocket connection
    session.connection_open = False
    if session.ws:
        try:
            session.ws.close()
            logger.info(f"Azure OpenAI connection closed due to silence timeout for session {session.session_id}")
        except Exception as e:
            logger.error(f"Error closing Azure OpenAI connection on timeout for session {session.session_id}: {e}")
        session.ws = None
    
    # Clean up session tracking
    if session.session_start_time:
        session_duration_ms = (time.perf_counter() - session.session_start_time) * 1000
        logger.info(
            f"Azure OpenAI session ended | Session: {session.session_id} | Duration: {session_duration_ms:.2f}ms | "
            f"TotalTranscriptions: {session.transcription_count} | Reason: SilenceTimeout"
        )
        performance_logger.info(
            f"SESSION_END | Session: {session.session_id} | TotalDuration: {session_duration_ms:.2f}ms | "
            f"TotalTranscriptions: {session.transcription_count} | Reason: SilenceTimeout"
        )
        session.session_start_time = None
        session.transcription_count = 0
        session.last_transcription_time = None
        session.last_audio_send_time = None
        session.current_transcript = ""
        session.accumulated_transcript = ""
        session.current_segment_transcript = ""
    
    # Notify ONLY this specific user to stop recording
    session.socketio.emit('silence_timeout', {
        'message': f'Recording stopped due to {AZURE_SILENCE_TIMEOUT_MS}ms silence timeout',
        'api': 'Azure OpenAI'
    }, room=session.session_id)
    
    stop_azure_silence_timer(session)

def initialize_azure_openai_connection(socketio_instance: SocketIO, language_name: str = "Auto", session_id: str = None):
    """
    Initialize Azure OpenAI WebSocket connection with automatic language detection
    
    Args:
        socketio_instance: Flask-SocketIO instance for emitting events
        language_name: User's language selection for logging/tracking purposes only.
                      The model always auto-detects the actual spoken language.
                      Default: "Auto"
        session_id: Session ID for user isolation
    
    Note:
        The Azure OpenAI gpt-4o-mini-transcribe model automatically detects and
        transcribes in any supported language without needing language hints.
    """
    if not session_id:
        logger.error("Session ID is required for Azure OpenAI connection")
        return False
        
    session = get_azure_session(session_id, socketio_instance)
    session.language = language_name
    
    # Clear audio buffer
    with session.audio_buffer_lock:
        session.audio_buffer = bytearray()
    
    # Get Azure OpenAI credentials from environment
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    
    if not api_key:
        logger.error(f"AZURE_OPENAI_API_KEY environment variable is not set for session {session.session_id}")
        return False
    
    if not endpoint:
        logger.error(f"AZURE_OPENAI_ENDPOINT environment variable is not set for session {session.session_id}")
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
    
    logger.info(f"Initializing Azure OpenAI connection for session {session.session_id} to: {url}")
    
    # Close existing connection if any
    if session.ws:
        try:
            logger.info(f"Closing existing Azure OpenAI connection for session {session.session_id}")
            session.ws.close()
        except Exception as e:
            logger.warning(f"Error closing existing Azure OpenAI connection for session {session.session_id}: {e}")
        session.ws = None
    
    # Stop any existing silence timer
    stop_azure_silence_timer(session)
    
    # Reset session tracking
    session.reset_performance_metrics()
    session.connection_open = False
    
    def on_open(ws):
        """Called when WebSocket connection is opened"""
        # Check if this connection is still wanted (race condition protection)
        if session.ws is None or session.ws != ws:
            logger.warning(f"Azure OpenAI WebSocket opened but connection is no longer needed for session {session.session_id} - closing")
            try:
                ws.close()
            except:
                pass
            return
        
        logger.info(f"Azure OpenAI WebSocket connection opened for session {session.session_id}")
        session.session_start_time = time.perf_counter()
        
        # Clear any buffered audio from previous sessions
        with session.audio_buffer_lock:
            session.audio_buffer = bytearray()
        
        # Log session start to performance log
        performance_logger.info(
            f"SESSION_START | Session: {session.session_id} | Language: {session.language} | Model: {session.model} | Timestamp: {time.time()}"
        )
        
        # NOTE: Silence timer is NOT started here - it will be started when first audio is sent
        # This prevents false "silence timeout" messages when user hasn't started speaking yet
        
        # Send session configuration
        session_config = {
            "type": "transcription_session.update",
            "session": {
                "input_audio_format": "pcm16",
                "input_audio_transcription": {
                    "model": "gpt-4o-mini-transcribe"
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 1000,
                    "silence_duration_ms": 500
                }
            }
        }
        
        logger.info(f"Azure OpenAI using auto-detection for language (user selected: {session.language}) for session {session.session_id}")
        
        try:
            # Double-check connection is still valid before sending
            if session.ws is None or session.ws != ws:
                logger.warning(f"Azure OpenAI connection closed during config send for session {session.session_id} - aborting")
                session.connection_open = False
                return
            
            # Verify the socket is still connected
            if not hasattr(ws, 'sock') or ws.sock is None:
                logger.warning(f"Azure OpenAI WebSocket socket is None for session {session.session_id} - connection closed")
                session.connection_open = False
                return
            
            ws.send(json.dumps(session_config))
            logger.info(f"Azure OpenAI session configuration sent for session {session.session_id}")
            
            # Mark connection as open AFTER config is sent successfully
            session.connection_open = True
            
        except Exception as e:
            logger.error(f"Error sending session configuration for session {session.session_id}: {e}")
            session.connection_open = False
    
    def on_message(ws, message):
        """Handle incoming messages from Azure OpenAI"""
        try:
            data = json.loads(message)
            event_type = data.get("type", "")
            
            # Log all event types for debugging
            logger.info(f"Azure OpenAI received event for session {session.session_id}: {event_type}")
            if event_type and "transcription" in event_type.lower():
                logger.info(f"Azure OpenAI transcription event for session {session.session_id}: {event_type} | Data: {json.dumps(data)[:200]}")
            
            # Handle incremental transcription updates (deltas)
            if event_type == "conversation.item.input_audio_transcription.delta":
                transcript_piece = data.get("delta", "")
                if transcript_piece:
                    # Accumulate the delta into the current segment transcript
                    session.current_segment_transcript += transcript_piece
                    
                    # Build full display transcript: accumulated + current segment
                    if session.accumulated_transcript and session.accumulated_transcript.strip():
                        session.current_transcript = session.accumulated_transcript.strip() + " " + session.current_segment_transcript.strip()
                    else:
                        session.current_transcript = session.current_segment_transcript.strip()
                    
                    current_time = time.perf_counter()
                    
                    # Calculate performance metrics
                    time_since_start_ms = (current_time - session.session_start_time) * 1000 if session.session_start_time else 0
                    if session.last_transcription_time:
                        time_since_last_ms = (current_time - session.last_transcription_time) * 1000
                    else:
                        time_since_last_ms = 0
                    
                    # Calculate transcription response time
                    if session.last_audio_send_time:
                        transcription_response_time_ms = (current_time - session.last_audio_send_time) * 1000
                    else:
                        transcription_response_time_ms = 0
                    
                    session.transcription_count += 1
                    session.last_transcription_time = current_time
                    
                    # Log performance metrics
                    performance_logger.info(
                        f"TRANSCRIPTION | Session: {session.session_id} | Count: {session.transcription_count} | "
                        f"ResponseTime: {transcription_response_time_ms:.2f}ms | "
                        f"TimeSinceStart: {time_since_start_ms:.2f}ms | "
                        f"TimeSinceLast: {time_since_last_ms:.2f}ms | "
                        f"Text: \"{session.current_transcript}\""
                    )
                    
                    # Reset silence timer when transcription is received
                    reset_azure_silence_timer(session)
                    
                    logger.info(f"Azure OpenAI transcript delta for session {session.session_id}: '{transcript_piece}' | Segment: '{session.current_segment_transcript}' | Full: '{session.current_transcript}'")
                    # Send transcription ONLY to the specific user who is speaking
                    session.socketio.emit('transcription_update', {'transcription': session.current_transcript}, room=session.session_id)
                    logger.info(f"✅ Emitted transcription_update event for session {session.session_id} with: '{session.current_transcript}'")
            
            # Handle completed/final transcription events - finalize the segment
            elif event_type in ["conversation.item.input_audio_transcription.completed", "conversation.item.input_audio_transcription.final"]:
                transcript = data.get("transcript", "")
                if transcript:
                    # Add this completed segment to accumulated transcript
                    if session.accumulated_transcript and session.accumulated_transcript.strip():
                        session.accumulated_transcript = session.accumulated_transcript.strip() + " " + transcript.strip()
                    else:
                        session.accumulated_transcript = transcript.strip()
                    
                    # Reset current segment (it's now part of accumulated)
                    session.current_segment_transcript = ""
                    
                    # Update display transcript
                    session.current_transcript = session.accumulated_transcript
                    
                    current_time = time.perf_counter()
                    
                    # Calculate performance metrics
                    time_since_start_ms = (current_time - session.session_start_time) * 1000 if session.session_start_time else 0
                    if session.last_transcription_time:
                        time_since_last_ms = (current_time - session.last_transcription_time) * 1000
                    else:
                        time_since_last_ms = 0
                    
                    if session.last_audio_send_time:
                        transcription_response_time_ms = (current_time - session.last_audio_send_time) * 1000
                    else:
                        transcription_response_time_ms = 0
                    
                    session.transcription_count += 1
                    session.last_transcription_time = current_time
                    
                    # Log performance metrics
                    performance_logger.info(
                        f"TRANSCRIPTION_COMPLETED | Session: {session.session_id} | Count: {session.transcription_count} | "
                        f"ResponseTime: {transcription_response_time_ms:.2f}ms | "
                        f"TimeSinceStart: {time_since_start_ms:.2f}ms | "
                        f"TimeSinceLast: {time_since_last_ms:.2f}ms | "
                        f"Segment: \"{transcript}\" | Accumulated: \"{session.accumulated_transcript}\""
                    )
                    
                    # Reset silence timer when transcription is received
                    reset_azure_silence_timer(session)
                    logger.info(f"Azure OpenAI {event_type} for session {session.session_id}: Segment='{transcript}' | Accumulated='{session.accumulated_transcript}'")
                    session.socketio.emit('transcription_update', {'transcription': session.current_transcript}, room=session.session_id)
                    logger.info(f"✅ Emitted transcription_update event for session {session.session_id} with {event_type}: '{session.current_transcript}'")
            
            # Handle conversation item created (new segment started)
            # Reset current_segment_transcript for the new utterance
            elif event_type == "conversation.item.created":
                # Reset current segment for the new conversation item
                # The previous segment should already be in accumulated_transcript via completed event
                session.current_segment_transcript = ""
                logger.info(f"Azure OpenAI new conversation item created for session {session.session_id} - reset segment, accumulated so far: '{session.accumulated_transcript}'")
        
        except Exception as e:
            logger.error(f"Error processing Azure OpenAI message for session {session.session_id}: {e}")
            logger.exception("Full traceback:")
    
    def on_error(ws, error):
        """Handle WebSocket errors"""
        # Filter out expected race condition errors
        error_str = str(error) if error else ""
        if "NoneType" in error_str and "sock" in error_str:
            logger.debug(f"Azure OpenAI WebSocket closed during connection for session {session.session_id} - ignoring race condition error")
            session.connection_open = False
            return
        
        logger.error(f"Azure OpenAI WebSocket error for session {session.session_id}: {error}")
        error_msg = error_str if error_str else "Unknown error"
        if isinstance(error, dict):
            error_msg = json.dumps(error)
        performance_logger.error(f"ERROR | Session: {session.session_id} | Message: {error_msg}")
        session.connection_open = False
        
        # Only notify frontend for real errors, not race conditions
        if session.ws is not None:
            session.socketio.emit('transcription_status', {
                'status': 'error',
                'message': f'Azure OpenAI connection error: {error}'
            }, room=session.session_id)
    
    def on_close(ws, close_status_code, close_msg):
        """Handle WebSocket close"""
        logger.info(f"Azure OpenAI WebSocket connection closed for session {session.session_id}: {close_status_code} - {close_msg}")
        session.connection_open = False
        
        # Stop silence timer when connection closes
        stop_azure_silence_timer(session)
        
        if session.session_start_time:
            session_duration_ms = (time.perf_counter() - session.session_start_time) * 1000
            logger.info(
                f"Azure OpenAI session ended | Session: {session.session_id} | Duration: {session_duration_ms:.2f}ms | "
                f"TotalTranscriptions: {session.transcription_count}"
            )
            performance_logger.info(
                f"SESSION_END | Session: {session.session_id} | TotalDuration: {session_duration_ms:.2f}ms | "
                f"TotalTranscriptions: {session.transcription_count}"
            )
            session.session_start_time = None
            session.transcription_count = 0
            session.last_transcription_time = None
            session.current_transcript = ""
            session.accumulated_transcript = ""
            session.current_segment_transcript = ""
    
    try:
        # Create WebSocket connection
        session.ws = WebSocketApp(
            url,
            header=headers,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        
        # Start WebSocket in a separate thread
        def run_websocket():
            if session.ws:
                session.ws.run_forever()
        
        session.thread = threading.Thread(target=run_websocket, daemon=True)
        session.thread.start()
        
        # Wait for connection to open (with timeout)
        max_wait_time = 5.0
        wait_interval = 0.1
        waited_time = 0.0
        
        while not session.connection_open and waited_time < max_wait_time:
            time.sleep(wait_interval)
            waited_time += wait_interval
        
        if session.connection_open:
            logger.info(f"Azure OpenAI WebSocket connection started and opened successfully for session {session.session_id}")
            return True
        else:
            logger.warning(f"Azure OpenAI WebSocket connection started but did not open within {max_wait_time}s for session {session.session_id}")
            return True
    
    except Exception as e:
        logger.error(f"Failed to initialize Azure OpenAI connection for session {session.session_id}: {type(e).__name__}: {e}")
        logger.exception("Full traceback:")
        session.ws = None
        return False

def send_audio_to_azure_openai(audio_data: bytes, session_id: str = None):
    """
    Send audio data to Azure OpenAI WebSocket for specific session
    
    Args:
        audio_data: Audio data in bytes (PCM16 from Web Audio API)
        session_id: Session ID for user isolation
    
    Returns:
        bool: True if audio was sent successfully
    """
    if not session_id:
        logger.warning("Session ID is required for Azure OpenAI audio sending")
        return False
        
    with azure_sessions_lock:
        if session_id not in azure_sessions:
            logger.warning(f"Azure OpenAI session {session_id} not found")
            return False
        session = azure_sessions[session_id]
    
    if not session.ws:
        logger.warning(f"Azure OpenAI WebSocket connection is not initialized for session {session.session_id}")
        return False
    
    # Check if connection is actually open
    if not session.connection_open:
        # Buffer audio while connection is establishing
        with session.audio_buffer_lock:
            session.audio_buffer.extend(audio_data)
            if len(session.audio_buffer) > AZURE_AUDIO_CHUNK_SIZE * 10:
                session.audio_buffer = session.audio_buffer[-AZURE_AUDIO_CHUNK_SIZE * 5:]
        logger.debug(f"Azure OpenAI connection establishing for session {session.session_id} - buffered {len(audio_data)} bytes")
        return False
    
    # Double-check socket state before sending
    try:
        if not hasattr(session.ws, 'sock') or session.ws.sock is None:
            logger.debug(f"Azure OpenAI WebSocket socket is None for session {session.session_id} - connection not ready")
            session.connection_open = False
            return False
        
        if not session.ws.sock.connected:
            logger.warning(f"Azure OpenAI WebSocket socket is not connected for session {session.session_id}")
            session.connection_open = False
            return False
    except (AttributeError, Exception) as e:
        logger.debug(f"Cannot verify Azure OpenAI WebSocket socket state for session {session.session_id}: {e}")
        session.connection_open = False
        return False
    
    try:
        # Add incoming data to buffer
        with session.audio_buffer_lock:
            session.audio_buffer.extend(audio_data)
        
        # Send buffered audio in chunks
        bytes_sent = 0
        while True:
            with session.audio_buffer_lock:
                if len(session.audio_buffer) < AZURE_AUDIO_CHUNK_SIZE:
                    break
                chunk = bytes(session.audio_buffer[:AZURE_AUDIO_CHUNK_SIZE])
                session.audio_buffer = session.audio_buffer[AZURE_AUDIO_CHUNK_SIZE:]
            
            # Encode audio chunk as base64
            audio_base64 = base64.b64encode(chunk).decode('utf-8')
            
            # Send audio buffer append message
            message = {
                "type": "input_audio_buffer.append",
                "audio": audio_base64
            }
            
            # Track when audio is sent for response time calculation
            session.last_audio_send_time = time.perf_counter()
            
            # Reset silence timer when audio is being sent - user is actively speaking
            reset_azure_silence_timer(session)
            
            session.ws.send(json.dumps(message))
            bytes_sent += len(chunk)
        
        if bytes_sent > 0:
            logger.debug(f"📤 Sent {bytes_sent} bytes to Azure OpenAI for session {session.session_id}")
        return True
    
    except Exception as e:
        error_msg = str(e)
        if "closed" in error_msg.lower() or "socket is already closed" in error_msg.lower():
            logger.warning(f"Azure OpenAI WebSocket connection is closed for session {session.session_id} - cannot send audio. Error: {error_msg}")
            session.connection_open = False
            session.socketio.emit('transcription_status', {
                'status': 'error',
                'message': 'Azure OpenAI connection closed. Please restart transcription.'
            }, room=session.session_id)
        else:
            logger.error(f"Error sending audio data to Azure OpenAI for session {session.session_id}: {e}")
            logger.exception("Full traceback:")
        return False

def close_azure_openai_connection(session_id: str = None):
    """Close Azure OpenAI WebSocket connection for specific session"""
    if not session_id:
        logger.warning("Session ID is required for Azure OpenAI connection closing")
        return
        
    with azure_sessions_lock:
        if session_id not in azure_sessions:
            logger.warning(f"Azure OpenAI session {session_id} not found for closing")
            return
        session = azure_sessions[session_id]
    
    # Set connection_open to False first to prevent new operations
    session.connection_open = False
    
    # Stop silence timer when manually closing connection
    stop_azure_silence_timer(session)
    
    # Clear audio buffer
    with session.audio_buffer_lock:
        session.audio_buffer = bytearray()
    
    # Save reference and clear session immediately to prevent race conditions
    ws_to_close = session.ws
    session.ws = None
    
    if ws_to_close:
        try:
            logger.info(f"Closing Azure OpenAI WebSocket connection for session {session.session_id}")
            ws_to_close.close()
        except Exception as e:
            logger.debug(f"Error closing Azure OpenAI connection for session {session.session_id} (may be already closed): {e}")
    
    # Reset transcript accumulator
    session.current_transcript = ""
    session.accumulated_transcript = ""
    session.current_segment_transcript = ""
    
    if session.thread and session.thread.is_alive():
        # Thread will terminate when WebSocket closes
        pass