"""
ElevenLabs Scribe V2 Realtime API Handler
Handles WebSocket connections and audio transcription using ElevenLabs Scribe v2 Realtime API
Updated to support per-session isolation for multiple concurrent users
"""
import os
import json
import base64
import logging
import threading
import time
import asyncio
from typing import Optional, TYPE_CHECKING, Dict
from flask_socketio import SocketIO

logger = logging.getLogger(__name__)

# Configure performance logger (only add handler if not already added)
performance_logger = logging.getLogger('elevenlabs_performance')
performance_logger.setLevel(logging.INFO)
if not performance_logger.handlers:
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

# Session storage for ElevenLabs connections - each session gets isolated state
elevenlabs_sessions: Dict[str, 'ElevenLabsSession'] = {}
elevenlabs_sessions_lock = threading.Lock()

class ElevenLabsSession:
    def __init__(self, session_id: str, socketio: SocketIO):
        self.session_id = session_id
        self.socketio = socketio
        self.ws = None
        self.thread: Optional[threading.Thread] = None
        self.session_start_time = None
        self.transcription_count = 0
        self.last_transcription_time = None
        self.last_audio_send_time = None
        self.current_transcript = ""  # Full transcript shown to user (accumulated + current partial)
        self.accumulated_transcript = ""  # Finalized/committed segments only
        self.connection_open = False
        self.session_started = threading.Event()
        self.silence_timer = None
        self.silence_timer_started = False  # Track if silence timer has been started (to start only on first audio)
        self.language = "Auto"
        self.audio_buffer = bytearray()
        self.audio_buffer_lock = threading.Lock()
        self.stop_event = threading.Event()
        # Track last partial for fallback logging if no committed transcript received
        self.last_partial_text = ""
        self.last_partial_time = None
        
    def reset_performance_metrics(self):
        """Reset performance tracking for new session"""
        self.session_start_time = time.perf_counter()
        self.transcription_count = 0
        self.last_transcription_time = None
        self.last_audio_send_time = None
        self.current_transcript = ""
        self.accumulated_transcript = ""
        self.last_partial_text = ""
        self.last_partial_time = None
        self.silence_timer_started = False  # Reset the silence timer started flag

def get_elevenlabs_session(session_id: str, socketio: SocketIO) -> ElevenLabsSession:
    """Get or create ElevenLabs session for user"""
    with elevenlabs_sessions_lock:
        if session_id not in elevenlabs_sessions:
            elevenlabs_sessions[session_id] = ElevenLabsSession(session_id, socketio)
        return elevenlabs_sessions[session_id]

def cleanup_elevenlabs_session(session_id: str):
    """Clean up ElevenLabs session on disconnect"""
    with elevenlabs_sessions_lock:
        if session_id in elevenlabs_sessions:
            session = elevenlabs_sessions[session_id]
            # Clean up any active connections
            if session.silence_timer:
                session.silence_timer.cancel()
            session.stop_event.set()
            if session.ws:
                try:
                    session.ws.close()
                except Exception as e:
                    logger.error(f"Error closing ElevenLabs connection for session {session_id}: {e}")
            del elevenlabs_sessions[session_id]

def reset_elevenlabs_silence_timer(session: ElevenLabsSession):
    """Reset the silence timeout timer when transcription is received or audio is sent"""
    if session.silence_timer:
        session.silence_timer.cancel()
    session.silence_timer = threading.Timer(ELEVENLABS_SILENCE_TIMEOUT_SEC, lambda: handle_elevenlabs_silence_timeout(session))
    session.silence_timer.start()
    session.silence_timer_started = True

def stop_elevenlabs_silence_timer(session: ElevenLabsSession):
    """Stop the silence timeout timer"""
    if session.silence_timer:
        session.silence_timer.cancel()
        session.silence_timer = None
    session.silence_timer_started = False

def handle_elevenlabs_silence_timeout(session: ElevenLabsSession):
    """Handle silence timeout - automatically stop transcription for specific session"""
    logger.info(f"ElevenLabs silence timeout reached ({ELEVENLABS_SILENCE_TIMEOUT_MS}ms) for session {session.session_id}. Stopping transcription automatically.")
    performance_logger.info(f"SILENCE_TIMEOUT | Session: {session.session_id} | Timeout: {ELEVENLABS_SILENCE_TIMEOUT_MS}ms")
    
    # Close the WebSocket connection
    session.connection_open = False
    session.stop_event.set()
    
    if session.ws:
        try:
            session.ws.close()
            logger.info(f"ElevenLabs connection closed due to silence timeout for session {session.session_id}")
        except Exception as e:
            logger.error(f"Error closing ElevenLabs connection on timeout for session {session.session_id}: {e}")
        session.ws = None
    
    # Clean up session tracking
    if session.session_start_time:
        session_duration_ms = (time.perf_counter() - session.session_start_time) * 1000
        
        # If session ends with 0 committed transcripts but we have a partial, log it as fallback
        if session.transcription_count == 0 and session.last_partial_text:
            if session.last_partial_time and session.last_audio_send_time:
                fallback_response_time_ms = (session.last_partial_time - session.last_audio_send_time) * 1000
            else:
                fallback_response_time_ms = 0
            
            time_since_start_ms = (session.last_partial_time - session.session_start_time) * 1000 if session.last_partial_time else session_duration_ms
            
            performance_logger.info(
                f"TRANSCRIPTION_PARTIAL_FALLBACK | Session: {session.session_id} | Count: 1 | "
                f"ResponseTime: {fallback_response_time_ms:.2f}ms | "
                f"TimeSinceStart: {time_since_start_ms:.2f}ms | "
                f"Text: \"{session.last_partial_text}\" | "
                f"Note: VAD did not commit before silence timeout"
            )
            logger.warning(f"ElevenLabs session ended with uncommitted partial transcript for session {session.session_id}: '{session.last_partial_text}'")
        
        logger.info(
            f"ElevenLabs session ended | Session: {session.session_id} | Duration: {session_duration_ms:.2f}ms | "
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
        session.last_partial_text = ""
        session.last_partial_time = None
    
    # Notify ONLY this specific user to stop recording
    session.socketio.emit('silence_timeout', {
        'message': f'Recording stopped due to {ELEVENLABS_SILENCE_TIMEOUT_MS}ms silence timeout',
        'api': 'ElevenLabs ScribeV2'
    }, room=session.session_id)
    
    stop_elevenlabs_silence_timer(session)

def initialize_elevenlabs_connection(socketio_instance: SocketIO, language_name: str = "Auto", session_id: str = None):
    """
    Initialize ElevenLabs Scribe V2 WebSocket connection
    
    Args:
        socketio_instance: Flask-SocketIO instance for emitting events
        language_name: Language for transcription. "Auto" for auto-detection.
        session_id: Session ID for user isolation
    
    Returns:
        bool: True if connection initialization started successfully
    """
    if not WEBSOCKETS_AVAILABLE:
        logger.error("websockets library not available - cannot initialize ElevenLabs connection")
        return False
        
    if not session_id:
        logger.error("Session ID is required for ElevenLabs connection")
        return False
    
    session = get_elevenlabs_session(session_id, socketio_instance)
    session.language = language_name
    session.session_started.clear()
    session.stop_event.clear()
    
    # Clear audio buffer
    with session.audio_buffer_lock:
        session.audio_buffer = bytearray()
    
    # Get ElevenLabs API key from environment
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        logger.error(f"ELEVENLABS_API_KEY environment variable is not set for session {session.session_id}")
        return False
    
    # Close existing connection if any
    if session.ws:
        try:
            logger.info(f"Closing existing ElevenLabs connection for session {session.session_id}")
            session.ws.close()
        except Exception as e:
            logger.warning(f"Error closing existing ElevenLabs connection for session {session.session_id}: {e}")
        session.ws = None
    
    # Stop any existing silence timer
    stop_elevenlabs_silence_timer(session)
    
    # Reset session tracking
    session.reset_performance_metrics()
    session.connection_open = False
    
    # Build WebSocket URL with proper query parameters
    model_id = "scribe_v2_realtime"
    ws_url = (
        f"wss://api.elevenlabs.io/v1/speech-to-text/realtime"
        f"?model_id={model_id}"
        f"&audio_format=pcm_16000"
        f"&commit_strategy=vad"
        f"&vad_silence_threshold_secs=0.5"
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
    
    logger.info(f"Initializing ElevenLabs connection for session {session.session_id} to: {ws_url}")
    
    def run_websocket():
        """Run WebSocket connection in a separate thread"""
        try:
            headers = {"xi-api-key": api_key}
            session.ws = ws_connect(ws_url, additional_headers=headers)
            logger.info(f"✅ Connected to ElevenLabs Scribe v2 Realtime for session {session.session_id}")
            
            # Start receiving messages
            while not session.stop_event.is_set():
                try:
                    message = session.ws.recv(timeout=0.1)
                    if message:
                        handle_elevenlabs_message(session, message)
                except TimeoutError:
                    continue
                except Exception as e:
                    if session.stop_event.is_set():
                        break
                    error_str = str(e).lower()
                    if "closed" in error_str or "connection" in error_str:
                        logger.info(f"ElevenLabs WebSocket connection closed for session {session.session_id}")
                        break
                    logger.error(f"Error receiving ElevenLabs message for session {session.session_id}: {e}")
                    break
        
        except Exception as e:
            logger.error(f"ElevenLabs WebSocket error for session {session.session_id}: {e}")
            session.socketio.emit('transcription_status', {
                'status': 'error',
                'message': f'ElevenLabs connection error: {e}'
            }, room=session.session_id)
        finally:
            session.connection_open = False
            stop_elevenlabs_silence_timer(session)
            if session.session_start_time:
                session_duration_ms = (time.perf_counter() - session.session_start_time) * 1000
                
                # If session ends with 0 committed transcripts but we have a partial, log it as fallback
                if session.transcription_count == 0 and session.last_partial_text:
                    # Calculate response time from last audio send to last partial
                    if session.last_partial_time and session.last_audio_send_time:
                        fallback_response_time_ms = (session.last_partial_time - session.last_audio_send_time) * 1000
                    else:
                        fallback_response_time_ms = 0
                    
                    time_since_start_ms = (session.last_partial_time - session.session_start_time) * 1000 if session.last_partial_time else session_duration_ms
                    
                    performance_logger.info(
                        f"TRANSCRIPTION_PARTIAL_FALLBACK | Session: {session.session_id} | Count: 1 | "
                        f"ResponseTime: {fallback_response_time_ms:.2f}ms | "
                        f"TimeSinceStart: {time_since_start_ms:.2f}ms | "
                        f"Text: \"{session.last_partial_text}\" | "
                        f"Note: VAD did not commit before session ended"
                    )
                    logger.warning(f"ElevenLabs session ended with uncommitted partial transcript for session {session.session_id}: '{session.last_partial_text}'")
                
                performance_logger.info(
                    f"SESSION_END | Session: {session.session_id} | TotalDuration: {session_duration_ms:.2f}ms | "
                    f"TotalTranscriptions: {session.transcription_count}"
                )
    
    # Start WebSocket in a separate thread
    session.thread = threading.Thread(target=run_websocket, daemon=True)
    session.thread.start()
    
    # Wait for session to start (with timeout)
    max_wait_time = 5.0
    if session.session_started.wait(timeout=max_wait_time):
        logger.info(f"ElevenLabs WebSocket connection started and session ready for session {session.session_id}")
        return True
    else:
        logger.warning(f"ElevenLabs session did not start within {max_wait_time}s for session {session.session_id} - connection may still be establishing")
        return True

def handle_elevenlabs_message(session: ElevenLabsSession, message: str):
    """Handle incoming messages from ElevenLabs for specific session"""
    try:
        data = json.loads(message)
        message_type = data.get("type", data.get("message_type"))
        
        logger.debug(f"ElevenLabs received event for session {session.session_id}: {message_type}")
        
        if message_type == "session_started":
            session_id_from_msg = data.get("session_id", "N/A")
            logger.info(f"✅ ElevenLabs session started for session {session.session_id}: {session_id_from_msg}")
            config = data.get("config", {})
            if config:
                logger.debug(f"   Config for session {session.session_id}: {json.dumps(config, indent=2)}")
            
            session.connection_open = True
            session.session_started.set()
            session.session_start_time = time.perf_counter()
            
            # NOTE: Silence timer is NOT started here - it will be started when first audio is sent
            # This prevents false "silence timeout" messages when user hasn't started speaking yet
            
            # Log session start
            performance_logger.info(
                f"SESSION_START | Session: {session.session_id} | Language: {session.language} | Model: ElevenLabs Scribe V2 | Timestamp: {time.time()}"
            )
            
            # Notify frontend that connection is ready
            session.socketio.emit('transcription_status', {'status': 'started', 'api': 'ElevenLabs ScribeV2'}, room=session.session_id)
        
        elif message_type == "partial_transcript":
            text = data.get("text", "")
            if text:
                # Reset silence timer on partial transcript
                reset_elevenlabs_silence_timer(session)
                
                # Build the full display transcript: accumulated segments + current partial
                if session.accumulated_transcript and session.accumulated_transcript.strip():
                    session.current_transcript = session.accumulated_transcript.strip() + " " + text.strip()
                else:
                    session.current_transcript = text.strip()
                
                # Track last partial for fallback logging if no committed transcript is received
                session.last_partial_text = session.current_transcript
                session.last_partial_time = time.perf_counter()
                
                logger.info(f"ElevenLabs partial transcript for session {session.session_id}: {text} | Display: {session.current_transcript}")
                # Emit the full transcript (accumulated + partial) for real-time feedback
                session.socketio.emit('transcription_update', {'transcription': session.current_transcript}, room=session.session_id)
                logger.info(f"✅ Emitted transcription_update event for session {session.session_id} with partial: '{session.current_transcript}'")
        
        elif message_type in ("committed_transcript", "final_transcript", "committed_transcript_with_timestamps"):
            text = data.get("text", "")
            if text:
                # Add this segment to accumulated transcripts
                # This ensures pauses during speech don't break the transcription
                if session.accumulated_transcript and session.accumulated_transcript.strip():
                    # Add space separator and append new segment
                    session.accumulated_transcript = session.accumulated_transcript.strip() + " " + text.strip()
                else:
                    session.accumulated_transcript = text.strip()
                
                # Update current_transcript to match accumulated (no pending partial)
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
                    f"TRANSCRIPTION | Session: {session.session_id} | Count: {session.transcription_count} | "
                    f"ResponseTime: {transcription_response_time_ms:.2f}ms | "
                    f"TimeSinceStart: {time_since_start_ms:.2f}ms | "
                    f"TimeSinceLast: {time_since_last_ms:.2f}ms | "
                    f"Text: \"{session.current_transcript}\""
                )
                
                # Reset silence timer
                reset_elevenlabs_silence_timer(session)
                
                logger.info(f"ElevenLabs committed transcript for session {session.session_id}: {text} | Accumulated: {session.accumulated_transcript}")
                # Send the accumulated transcript to the frontend
                session.socketio.emit('transcription_update', {'transcription': session.current_transcript}, room=session.session_id)
                logger.info(f"✅ Emitted transcription_update event for session {session.session_id} with final: '{session.current_transcript}'")
        
        elif message_type == "commit_throttled":
            logger.warning(f"⚠️ ElevenLabs commit throttled for session {session.session_id}")
        
        elif message_type in ("error", "auth_error", "quota_exceeded", "transcriber_error", "input_error", "rate_limited"):
            error = data.get("error", data.get("message", "Unknown error"))
            logger.error(f"❌ ElevenLabs {message_type} for session {session.session_id}: {error}")
            performance_logger.error(f"ERROR | Session: {session.session_id} | Type: {message_type} | Message: {error}")
            session.socketio.emit('transcription_status', {
                'status': 'error',
                'message': f'ElevenLabs error: {error}'
            }, room=session.session_id)
        else:
            logger.debug(f"🔍 ElevenLabs message type '{message_type}' for session {session.session_id}: {json.dumps(data)[:200]}")
    
    except json.JSONDecodeError:
        logger.warning(f"⚠️ ElevenLabs received non-JSON message for session {session.session_id}: {message[:200]}")
    except Exception as e:
        logger.error(f"Error processing ElevenLabs message for session {session.session_id}: {e}")

def send_audio_to_elevenlabs(audio_data: bytes, session_id: str = None) -> bool:
    """
    Send audio data to ElevenLabs WebSocket for specific session
    
    Args:
        audio_data: Audio data in bytes (PCM16 format, 16kHz)
        session_id: Session ID for user isolation
    
    Returns:
        bool: True if audio was sent successfully
    """
    if not session_id:
        logger.warning("Session ID is required for ElevenLabs audio sending")
        return False
        
    with elevenlabs_sessions_lock:
        if session_id not in elevenlabs_sessions:
            logger.warning(f"ElevenLabs session {session_id} not found")
            return False
        session = elevenlabs_sessions[session_id]
    
    if not session.ws:
        logger.warning(f"ElevenLabs WebSocket connection is not initialized for session {session.session_id}")
        return False
    
    # Check if session has started
    if not session.connection_open:
        # Buffer audio while connection is establishing
        with session.audio_buffer_lock:
            session.audio_buffer.extend(audio_data)
            if len(session.audio_buffer) > ELEVENLABS_AUDIO_CHUNK_SIZE * 10:
                session.audio_buffer = session.audio_buffer[-ELEVENLABS_AUDIO_CHUNK_SIZE * 5:]
        logger.debug(f"ElevenLabs connection establishing for session {session.session_id} - buffered {len(audio_data)} bytes")
        return False
    
    try:
        # Add incoming data to buffer
        with session.audio_buffer_lock:
            session.audio_buffer.extend(audio_data)
        
        # Send buffered audio in chunks
        bytes_sent = 0
        while True:
            with session.audio_buffer_lock:
                if len(session.audio_buffer) < ELEVENLABS_AUDIO_CHUNK_SIZE:
                    break
                chunk = bytes(session.audio_buffer[:ELEVENLABS_AUDIO_CHUNK_SIZE])
                session.audio_buffer = session.audio_buffer[ELEVENLABS_AUDIO_CHUNK_SIZE:]
            
            # Encode audio as base64 (ElevenLabs format)
            audio_base64 = base64.b64encode(chunk).decode('utf-8')
            
            # Message format per ElevenLabs API
            message = {
                "message_type": "input_audio_chunk",
                "audio_base_64": audio_base64,
                "sample_rate": ELEVENLABS_SAMPLE_RATE
            }
            
            # Track when audio is sent
            session.last_audio_send_time = time.perf_counter()
            
            # Start silence timer on first audio send (if not already started)
            # Timer is only RESET when transcription is received (indicating speech)
            # This way, continuous silence will trigger timeout even if audio data is being sent
            if not session.silence_timer_started:
                reset_elevenlabs_silence_timer(session)
            
            session.ws.send(json.dumps(message))
            bytes_sent += len(chunk)
        
        if bytes_sent > 0:
            logger.debug(f"📤 Sent {bytes_sent} bytes to ElevenLabs for session {session.session_id}")
        return True
    
    except Exception as e:
        error_msg = str(e)
        if "closed" in error_msg.lower():
            logger.warning(f"ElevenLabs WebSocket connection is closed for session {session.session_id} - cannot send audio")
            session.connection_open = False
            session.socketio.emit('transcription_status', {
                'status': 'error',
                'message': 'ElevenLabs connection closed. Please restart transcription.'
            }, room=session.session_id)
        else:
            logger.error(f"Error sending audio to ElevenLabs for session {session.session_id}: {e}")
        return False

def close_elevenlabs_connection(session_id: str = None):
    """Close ElevenLabs WebSocket connection for specific session"""
    if not session_id:
        logger.warning("Session ID is required for ElevenLabs connection closing")
        return
        
    with elevenlabs_sessions_lock:
        if session_id not in elevenlabs_sessions:
            logger.warning(f"ElevenLabs session {session_id} not found for closing")
            return
        session = elevenlabs_sessions[session_id]
    
    # Get WebSocket reference before any changes
    ws_to_close = session.ws
    
    # Wait briefly for VAD to commit any pending transcripts before closing
    # VAD (Voice Activity Detection) handles commits automatically when it detects silence
    # We just need to give it time to process the final audio
    if ws_to_close and session.connection_open:
        import time
        # Wait longer than vad_silence_threshold_secs (0.5s) to ensure VAD has time to commit
        logger.info(f"Waiting for ElevenLabs VAD to commit any pending transcripts for session {session.session_id}")
        time.sleep(1.0)  # 1000ms wait for VAD to detect silence and commit
    
    # NOW signal stop (after we've had a chance to receive committed transcript)
    session.stop_event.set()
    session.connection_open = False
    
    # Stop silence timer
    stop_elevenlabs_silence_timer(session)
    
    # Clear audio buffer
    with session.audio_buffer_lock:
        session.audio_buffer = bytearray()
    
    # Clear session WebSocket reference
    session.ws = None
    
    # Close WebSocket
    if ws_to_close:
        try:
            logger.info(f"Closing ElevenLabs WebSocket connection for session {session.session_id}")
            ws_to_close.close()
        except Exception as e:
            logger.debug(f"Error closing ElevenLabs connection for session {session.session_id} (may be already closed): {e}")
    
    # Reset transcript
    session.current_transcript = ""
    session.accumulated_transcript = ""
    
    logger.info(f"🔌 ElevenLabs disconnected for session {session.session_id}")