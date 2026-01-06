import asyncio
import websockets
import json
import pyaudio
import os
import base64
import time
import numpy as np
from typing import Optional
from websockets.asyncio.client import ClientConnection
from dotenv import load_dotenv

class ElevenLabsRealtimeSTT:
    """Real-time Speech-to-Text using ElevenLabs Scribe v2 Realtime"""
    
    def __init__(self, api_key: str, model_id: str = "scribe_v2_realtime"):
        self.api_key = api_key
        self.model_id = model_id
        self.ws_url = f"wss://api.elevenlabs.io/v1/speech-to-text/realtime?model_id={model_id}"

        self.ws: Optional[ClientConnection] = None
        self.session_started = asyncio.Event()
        self.last_commit_time = 0.0
        self.min_commit_interval = 0.5  # Minimum 0.5 seconds between commits
        
        # Audio configuration
        self.sample_rate = 16000
        self.chunk_size = 4096
        self.channels = 1
        self.format = pyaudio.paInt16
        
        # Calculate commit interval: need at least 0.3s of audio
        # Time per chunk = chunk_size / sample_rate = 4096 / 16000 = 0.256s
        # To get 0.3s, we need at least 2 chunks. Use 4 chunks (~1 second) to be safe
        self.chunks_per_second = self.sample_rate / self.chunk_size  # ≈ 3.9 chunks/second
        self.commit_interval_chunks = max(4, int(1.0 * self.chunks_per_second))  # Commit every ~1 second
        
    async def connect(self):
        """Establish WebSocket connection with authentication"""
        headers = {
            "xi-api-key": self.api_key
        }
        self.ws = await websockets.connect(self.ws_url, additional_headers=headers)
        print("✅ Connected to ElevenLabs Scribe v2 Realtime")
    
    async def send_audio_chunk(self, audio_data: bytes, commit: bool = False):
        """Send audio chunk to the API"""
        if not self.ws:
            return
        
        # Enforce minimum commit interval
        if commit:
            current_time = time.time()
            time_since_last_commit = current_time - self.last_commit_time
            if time_since_last_commit < self.min_commit_interval:
                commit = False  # Skip commit if too soon
        
        try:
            # Encode audio as base64
            audio_base64 = base64.b64encode(audio_data).decode('utf-8')
            message = {
                "message_type": "input_audio_chunk",
                "audio_base_64": audio_base64,
                "commit": commit,
                "sample_rate": self.sample_rate
            }
            await self.ws.send(json.dumps(message))
            
            if commit:
                self.last_commit_time = time.time()
                print(f"\n💾 Committed audio chunk ({len(audio_data)} bytes)")
        except websockets.exceptions.ConnectionClosed:
            print("\n⚠️  Connection closed while sending audio")
            raise
        except Exception as e:
            print(f"\n⚠️  Error sending audio chunk: {e}")
            raise
    
    async def receive_transcriptions(self):
        """Listen for transcription results from the API"""
        if not self.ws:
            return
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    message_type = data.get("message_type")
                    
                    if message_type == "session_started":
                        session_id = data.get("session_id", "N/A")
                        print(f"\n✅ Session started: {session_id}")
                        print(f"   Config: {json.dumps(data.get('config', {}), indent=2)}")
                        self.session_started.set()  # Signal that session is ready
                    elif message_type == "partial_transcript":
                        text = data.get("text", "")
                        if text:
                            print(f"📝 Partial: {text}", end="\r", flush=True)
                        # Don't print empty partials to reduce noise
                    elif message_type == "final_transcript":
                        text = data.get("text", "")
                        if text:
                            print(f"\n✨ Final: {text}", flush=True)
                        else:
                            print(f"\n✨ Final (empty): {json.dumps(data)}")
                    elif message_type == "commit_throttled":
                        # This is a warning - we're committing too frequently
                        # Increase the minimum commit interval to back off
                        self.min_commit_interval = min(2.0, self.min_commit_interval * 1.5)
                        print(f"\n⚠️  Commit throttled, backing off to {self.min_commit_interval}s")
                    elif message_type == "error":
                        error = data.get("error", "Unknown error")
                        print(f"\n❌ Error: {error}")
                        print(f"   Full error data: {json.dumps(data, indent=2)}")
                    else:
                        # Debug: print ALL unknown message types with full data
                        print(f"\n🔍 Unknown message type: {message_type}")
                        print(f"   Full data: {json.dumps(data, indent=2)}")
                except json.JSONDecodeError:
                    print(f"\n⚠️  Received non-JSON message: {message[:200]}")
                    
        except websockets.exceptions.ConnectionClosed:
            print("\n🔌 Connection closed")
        except Exception as e:
            print(f"\n❌ Error receiving transcriptions: {e}")
            import traceback
            traceback.print_exc()
    
    async def stream_microphone_audio(self):
        """Capture and stream audio from microphone"""
        # Wait for session to start before sending audio
        print("⏳ Waiting for session to start...")
        await self.session_started.wait()
        print("✅ Session ready, starting audio stream...")
        
        audio = pyaudio.PyAudio()
        stream = None
        
        try:
            # List available audio input devices for debugging
            print("\n🔍 Available audio input devices:")
            input_devices = []
            for i in range(audio.get_device_count()):
                info = audio.get_device_info_by_index(i)
                max_input_channels = int(info.get('maxInputChannels', 0))
                if max_input_channels > 0:
                    input_devices.append(i)
                    print(f"   Device {i}: {info['name']} (inputs: {max_input_channels})")
            
            if not input_devices:
                print("❌ No audio input devices found!")
                return
            
            print(f"\n🎙️  Opening default input device...")
            stream = audio.open(
                format=self.format,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size
            )
            
            if not stream.is_active():
                print("❌ Audio stream is not active!")
                return
            
            print(f"\n🎤 Streaming audio... (Press Ctrl+C to stop)")
            print(f"   Commit interval: {self.commit_interval_chunks} chunks (~{self.commit_interval_chunks * (self.chunk_size / self.sample_rate):.2f}s)")
            print(f"   Sample rate: {self.sample_rate} Hz, Chunk size: {self.chunk_size} bytes\n")
            
            chunk_counter = 0
            bytes_sent = 0
            
            # Use executor to run blocking I/O operations
            loop = asyncio.get_event_loop()
            
            print("🔄 Starting audio capture loop...")
            
            def read_audio_chunk():
                """Helper function to read audio chunk (needed for executor)"""
                return stream.read(self.chunk_size, exception_on_overflow=False)
            
            while True:
                try:
                    # Run blocking stream.read() in executor to avoid blocking event loop
                    audio_chunk = await loop.run_in_executor(None, read_audio_chunk)
                    
                    if not audio_chunk or len(audio_chunk) == 0:
                        print("\n⚠️  No audio data received")
                        await asyncio.sleep(0.1)
                        continue
                    
                    bytes_sent += len(audio_chunk)
                    
                    # Check audio level to verify microphone is working
                    audio_array = np.frombuffer(audio_chunk, dtype=np.int16)
                    rms = np.sqrt(np.mean(np.square(audio_array.astype(np.float32))))
                    
                    # Commit at proper intervals to accumulate at least 0.3s of audio
                    commit = (chunk_counter > 0 and chunk_counter % self.commit_interval_chunks == 0)
                    
                    # Print first few chunks for debugging
                    if chunk_counter < 5:
                        print(f"📤 Chunk {chunk_counter}: {len(audio_chunk)} bytes, RMS: {rms:.0f}, commit: {commit}")
                    elif commit:
                        print(f"\n📤 Sending chunk {chunk_counter} with commit=True (RMS: {rms:.0f})")
                    
                    await self.send_audio_chunk(audio_chunk, commit=commit)
                    chunk_counter += 1
                    
                    # Print progress every 50 chunks with audio level
                    if chunk_counter % 50 == 0:
                        print(f"   Sent {chunk_counter} chunks ({bytes_sent / 1024:.1f} KB, RMS: {rms:.0f})", end="\r", flush=True)
                        
                except Exception as e:
                    print(f"\n⚠️  Error reading/sending audio chunk: {e}")
                    import traceback
                    traceback.print_exc()
                    await asyncio.sleep(0.1)
                
        except KeyboardInterrupt:
            print("\n⏹️ Stopping audio stream...")
        except Exception as e:
            print(f"\n❌ Error in audio streaming: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            audio.terminate()
            print("🔇 Audio stream closed")
    
    async def run(self):
        """Main execution method"""
        await self.connect()
        
        # Run audio streaming and transcription reception concurrently
        await asyncio.gather(
            self.stream_microphone_audio(),
            self.receive_transcriptions()
        )
    
    async def close(self):
        """Close WebSocket connection"""
        if self.ws:
            await self.ws.close()
            print("🔌 Disconnected")


async def main():
    
    load_dotenv() # Load environment variables from .env
    # Get API key from environment variable
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise ValueError("Please set ELEVENLABS_API_KEY environment variable")
    
    stt = ElevenLabsRealtimeSTT(api_key=api_key)
    
    try:
        await stt.run()
    finally:
        await stt.close()


if __name__ == "__main__":
    asyncio.run(main())
