import os
import json
import base64
import threading
import pyaudio
import logging
# Import WebSocketApp from websocket-client package
# The package name is 'websocket-client' but it's imported as 'websocket'
try:
    from websocket import WebSocketApp  # type: ignore
except ImportError:
    # Fallback for different installations
    import websocket  # type: ignore
    WebSocketApp = websocket.WebSocketApp  # type: ignore
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
    handlers=[
        logging.StreamHandler()  # Log to console
    ]
)

logger = logging.getLogger(__name__)

load_dotenv() # Load environment variables from .env

OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("❌ AZURE_OPENAI_API_KEY is missing!")

# WebSocket endpoint for OpenAI Realtime API (transcription model)
endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
if not endpoint:
    raise RuntimeError("❌ AZURE_OPENAI_ENDPOINT is missing!")

# Parse endpoint: remove https:// or http:// if present, remove trailing slashes
endpoint_host = endpoint.strip()
if '://' in endpoint_host:
    endpoint_host = endpoint_host.split('://')[1]
# Remove any trailing path or slashes
endpoint_host = endpoint_host.split('/')[0].rstrip('/')

# Construct WebSocket URL
url = f"wss://{endpoint_host}/openai/realtime?api-version=2025-04-01-preview&intent=transcription"
headers = { "api-key": OPENAI_API_KEY}

# Debug: log the URL
logger.info(f"Endpoint host: {endpoint_host}")
logger.info(f"Connecting to: {url}")
# Audio stream parameters (16-bit PCM, 16kHz mono)
RATE = 24000
CHANNELS = 1
FORMAT = pyaudio.paInt16
CHUNK = 1024

audio_interface = pyaudio.PyAudio()
stream = audio_interface.open(format=FORMAT,
                              channels=CHANNELS,
                              rate=RATE,
                              input=True,
                              frames_per_buffer=CHUNK)

def on_open(ws):
    logger.info("Connected! Start speaking...")
    session_config = {
        "type": "transcription_session.update",
        "session": {
            "input_audio_format": "pcm16",
            "input_audio_transcription": {
                "model": "gpt-4o-mini-transcribe"
                # No prompt - let the model auto-detect the language
            },
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 1000,  # 1 second of audio before speech detection
                "silence_duration_ms": 500   # 0.5 seconds of silence before ending turn
            }
        }
    }
    ws.send(json.dumps(session_config))

    def stream_microphone():
        try:
            while ws.keep_running:
                audio_data = stream.read(CHUNK, exception_on_overflow=False)
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": audio_base64
                }))
        except Exception as e:
            logger.error(f"Audio streaming error: {e}")
            ws.close()

    threading.Thread(target=stream_microphone, daemon=True).start()


def on_message(ws, message):
    try:
        data = json.loads(message)
        event_type = data.get("type", "")
        #print("Event type:", event_type)
        #print(data)   
        # Stream live incremental transcripts
        if event_type == "conversation.item.input_audio_transcription.delta":
            transcript_piece = data.get("delta", "")
            if transcript_piece:
                logger.info(f"Transcript delta: {transcript_piece}")
        if event_type == "conversation.item.input_audio_transcription.completed":
            logger.info(f"Final Data: {data['transcript']}")
        if event_type == "item":
            transcript = data.get("item", "")
            if transcript:
                logger.info(f"Final transcript: {transcript}")

    except Exception:
        pass  # Ignore unrelated events





def on_error(ws, error):
    logger.error(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    logger.info("Disconnected from server.")
    stream.stop_stream()
    stream.close()
    audio_interface.terminate()

logger.info("Connecting to OpenAI Realtime API...")
ws_app = WebSocketApp(
    url,
    header=headers,
    on_open=on_open,
    on_message=on_message,
    on_error=on_error,
    on_close=on_close
)

ws_app.run_forever()