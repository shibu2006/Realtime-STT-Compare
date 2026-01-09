# VoiceTranscribe - Real-time Speech Transcription Application

VoiceTranscribe is a Flask-based web application that provides real-time **S**peech-**T**o-**T**ext transcription using multiple AI-powered transcription APIs. Choose between **Deepgram API**, **Azure OpenAI**, or **ElevenLabs ScribeV2** for your transcription needs. The application features a modern web interface, multi-language support, automatic silence detection, and comprehensive performance logging.

<img src="https://github.com/shibu2006/Realtime-STT-Compare/blob/main/VoiceTranscribeAppScreenshotV1.png" width="720" alt="Realtime-STT-Compare is a Voice Transcribe Screenshot">

## 🚀 Features

- **Multiple Transcription APIs**: Choose between three powerful transcription services:
  - **Deepgram API**: Advanced speech recognition with live transcription
  - **Azure OpenAI**: GPT-4o-mini-transcribe model with automatic language detection
  - **ElevenLabs ScribeV2**: Real-time transcription with voice activity detection
- **Multi-language Support**: Supports many languages including English, Spanish, French, German, Hindi, Japanese, Indic and more
- **Automatic Silence Detection**: Automatically stops recording after a configurable period of silence
- **Performance Logging**: Detailed performance metrics for each transcription session
- **WebSocket Communication**: Real-time bidirectional communication between client and server
- **Public URL Support**: Optional ngrok integration for public access
- **Modern UI**: Clean and intuitive user interface with real-time transcription display and API selection

## 📋 Requirements

- Python 3.8 or higher
- **At least one API key** from the supported providers:
  - **Deepgram API key** ([Get one here](https://console.deepgram.com/signup))
  - **Azure OpenAI API key and endpoint** ([Get one here](https://portal.azure.com/))
  - **ElevenLabs API key** ([Get one here](https://elevenlabs.io/))
- (Optional) ngrok for public URL access ([Download here](https://ngrok.com/download))

## 🛠️ Installation

1. **Clone the repository** (if applicable) or navigate to the project directory:
   ```bash
   cd realtime-stt-compare
   ```

2. **Create a virtual environment**:
   ```bash
   python3 -m venv venv
   ```

3. **Activate the virtual environment**:
   - On macOS/Linux:
     ```bash
     source venv/bin/activate
     ```
   - On Windows:
     ```bash
     venv\Scripts\activate
     ```

4. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

5. **Set up environment variables**:
   Create a `.env` file in the root directory with your API keys:
   ```bash
   # At least one API key is required
   
   # Deepgram API (recommended for general use)
   DEEPGRAM_API_KEY=your_deepgram_api_key_here
   
   # Azure OpenAI (for GPT-4o-mini-transcribe model)
   AZURE_OPENAI_API_KEY=your_azure_openai_api_key_here
   AZURE_OPENAI_ENDPOINT=https://your-resource-name.cognitiveservices.azure.com
   
   # ElevenLabs ScribeV2 (for voice activity detection)
   ELEVENLABS_API_KEY=sk_your_elevenlabs_api_key_here
   
   # Optional settings
   SILENCE_TIMEOUT=5000
   
   # Server configuration (optional)
   HOST=0.0.0.0          # 0.0.0.0 for all interfaces, 127.0.0.1 for localhost only
   PORT=8000             # Port number to listen on
   ```
   
   **API Key Requirements:**
   - **DEEPGRAM_API_KEY**: Your Deepgram API key (optional if using other APIs)
   - **AZURE_OPENAI_API_KEY** & **AZURE_OPENAI_ENDPOINT**: Required for Azure OpenAI transcription
   - **ELEVENLABS_API_KEY**: Required for ElevenLabs ScribeV2 transcription
   - **SILENCE_TIMEOUT**: Silence timeout in milliseconds (optional, default: 5000ms)
   - **HOST**: IP address to bind to (optional, default: 0.0.0.0)
   - **PORT**: Port number to listen on (optional, default: 8000)

## 🚀 Quick Start

### Option 1: Using the Startup Script (Recommended)

**Start in background (daemon mode):**
```bash
./start.sh start
```

**Start in background with ngrok (public URL):**
```bash
./start.sh start --ngrok
# or
./start.sh start -n
```

**Check status:**
```bash
./start.sh status
```

**Stop the application:**
```bash
./start.sh stop
```

**Legacy foreground mode (deprecated):**
```bash
./start.sh              # Start locally in foreground
./start.sh --ngrok       # Start with ngrok in foreground
```

**Show help:**
```bash
./start.sh --help
```

The startup script will:
- Check for Python and dependencies
- Create virtual environment if missing
- Install requirements if needed
- Validate `.env` file
- Start ngrok tunnel (if requested)
- Start the Flask application in background
- Display connection URLs
- Save process IDs for management

**Background Mode Features:**
- Runs as a daemon process
- Logs output to `voicesearch_app.log`
- Process management with PID files
- Start/stop/status commands
- Automatic cleanup on stop

### Option 2: Manual Start

1. **Activate virtual environment**:
   ```bash
   source venv/bin/activate
   ```

2. **Run the application**:
   ```bash
   python voicesearch_app.py
   ```

3. **Access the application**:
   Open your browser and navigate to `http://localhost:8000`

## 🌐 Using ngrok for Public Access

To make your application accessible from the internet:

1. **Install ngrok** (if not already installed):
   - Download from [ngrok.com](https://ngrok.com/download)
   - Or install via Homebrew: `brew install ngrok`

2. **Authenticate ngrok** (first time only):
   ```bash
   ngrok config add-authtoken YOUR_NGROK_AUTH_TOKEN
   ```
   Get your auth token from [ngrok dashboard](https://dashboard.ngrok.com/get-started/your-authtoken)

3. **Start with ngrok**:
   ```bash
   ./start.sh --ngrok
   ```

4. **Access your app**:
   The script will display the public URL (e.g., `https://abc123.ngrok.io`)

**Note**: Free ngrok accounts are limited to 1 simultaneous session. If you get an error about existing sessions:
```bash
pkill ngrok
```

## 📖 Usage

1. **Open the application** in your web browser
2. **Select a transcription API** from the dropdown menu:
   - **Deepgram API**: Fast and accurate, supports 16+ languages
   - **Azure OpenAI**: GPT-4o-mini-transcribe with automatic language detection
   - **ElevenLabs ScribeV2**: Advanced voice activity detection
3. **Select a language** from the dropdown menu (or "Auto" for automatic detection)
4. **Click the microphone button** to start recording
5. **Speak** - your speech will be transcribed in real-time
6. **Click the microphone button again** to stop recording
7. The transcription will automatically stop after the silence timeout period

### API-Specific Features

#### Deepgram API
- **Best for**: General-purpose transcription, production use
- **Languages**: 16 supported languages with explicit selection
- **Model**: Nova-3 (latest Deepgram model)
- **Features**: Fast response times, high accuracy

#### Azure OpenAI
- **Best for**: Automatic language detection, GPT-powered transcription
- **Languages**: Most languages with auto-detection option (no manual selection needed)
- **Model**: gpt-4o-mini-transcribe
- **Features**: Automatic language detection, GPT-4 powered accuracy

#### ElevenLabs ScribeV2
- **Best for**: Voice activity detection, real-time streaming
- **Languages**: 99 supported languages with auto-detection option (no manual selection needed)
- **Model**: scribe_v2_realtime
- **Features**: Advanced VAD, real-time streaming, commit strategies

### Supported Languages

**Languages supported :**
- Supported with Deepgram
   - English (default)
   - Chinese
   - Danish
   - Dutch
   - Finnish
   - French
   - German
   - Hindi
   - Italian
   - Japanese
   - Korean
   - Norwegian
   - Portuguese
   - Russian
   - Spanish
   - Swedish
- Supported with Azure OpenAI
   - All languages with Indic Languages (Tamil, Malayalam, Kannada, Telugu, Odia, Bengali, Urdu)
- Supported with ElevenLabs Scribe v2
   - 99 languages along with Indic

**Note**: Azure OpenAI, ElevenLabs automatically detects the language, while Deepgram allows manual selection.

## 📊 Logging

The application generates two types of logs:

### 1. Application Log (`voicesearch_app.log`)
Contains general application events:
- Server startup/shutdown
- Client connections/disconnections
- Deepgram / Azure OpenAI / ElevenLabs connection events
- Errors and warnings

### 2. Performance Log (`voicesearch_performance.log`)
Contains detailed performance metrics for each transcription session:
- Session start/end times
- Individual transcription response times
- Time since session start
- Time between transcriptions
- Transcription text
- Session duration and transcription count

**Example Performance Log Entry:**
```
2026-01-05 16:50:00,123 - SESSION_START | Language: English | Model: nova-3 | Timestamp: 1704477000.123
2026-01-05 16:50:01,456 - TRANSCRIPTION | Count: 1 | ResponseTime: 1234.56ms | TimeSinceStart: 1333.33ms | TimeSinceLast: 0.00ms | Text: "Hello world"
2026-01-05 16:50:05,012 - SESSION_END | TotalDuration: 4889.00ms | TotalTranscriptions: 2
```

## ⚙️ Configuration

### Environment Variables

Create a `.env` file in the root directory:

```env
# API Keys (at least one required)
DEEPGRAM_API_KEY=your_deepgram_api_key_here
AZURE_OPENAI_API_KEY=your_azure_openai_api_key_here
AZURE_OPENAI_ENDPOINT=https://your-resource-name.cognitiveservices.azure.com
ELEVENLABS_API_KEY=sk_your_elevenlabs_api_key_here

# Optional Settings
SILENCE_TIMEOUT=5000  # Milliseconds (default: 5000)

# Server Configuration (optional)
HOST=0.0.0.0          # IP to bind to (default: 0.0.0.0)
PORT=8000             # Port to listen on (default: 8000)
```

### API Configuration Details

#### Deepgram API
- **Required**: `DEEPGRAM_API_KEY`
- **Get API Key**: [Deepgram Console](https://console.deepgram.com/signup)
- **Format**: Standard API key string

#### Azure OpenAI
- **Required**: `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_ENDPOINT`
- **Get API Key**: [Azure Portal](https://portal.azure.com/)
- **Endpoint Format**: `https://your-resource-name.cognitiveservices.azure.com`
- **Model**: Uses gpt-4o-mini-transcribe automatically

#### ElevenLabs ScribeV2
- **Required**: `ELEVENLABS_API_KEY`
- **Get API Key**: [ElevenLabs Dashboard](https://elevenlabs.io/)
- **Format**: Starts with `sk_`
- **Additional Dependency**: Requires `websockets` library (`pip install websockets`)

### Silence Timeout

The `SILENCE_TIMEOUT` setting controls how long the application waits for silence before automatically stopping the recording. Value is in milliseconds:
- `5000` = 5 seconds (default)
- `10000` = 10 seconds
- `3000` = 3 seconds

### Server Configuration

The `HOST` and `PORT` settings control where the application listens for connections:

**HOST Options:**
- `0.0.0.0` = Listen on all network interfaces (default) - allows external access
- `127.0.0.1` = Listen only on localhost - local access only
- Specific IP = Listen only on that IP address

**PORT Options:**
- `8000` = Default port
- Any available port number (1024-65535 recommended for non-root users)

**Examples:**
```env
# For local development only
HOST=127.0.0.1
PORT=8000

# For EC2/server deployment (external access)
HOST=0.0.0.0
PORT=8000

# Custom port
HOST=0.0.0.0
PORT=3000
```

## 📁 Project Structure

```
realtime-stt-compare/
├── voicesearch_app.py          # Main application file
├── start.sh                    # Startup script
├── requirements.txt            # Python dependencies
├── .env                        # Environment variables (create this)
├── templates/
│   └── index.html             # Web interface
├── static/
│   ├── script.js              # Client-side JavaScript
│   └── style.css              # Stylesheet
├── archive/                    # Archived unused files
├── voicesearch_app.log         # Application log
└── voicesearch_performance.log # Performance log
```

## 🔧 Troubleshooting

### API Key Issues

**Issue: "DEEPGRAM_API_KEY is not set"**
**Solution**:
- Create a `.env` file in the root directory
- Add your API key: `DEEPGRAM_API_KEY=your_key_here`
- Make sure the file is named exactly `.env` (not `env` or `.env.txt`)

**Issue: "AZURE_OPENAI_API_KEY is not set"**
**Solution**:
- Add both `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_ENDPOINT` to your `.env` file
- Ensure the endpoint format is correct: `https://your-resource-name.cognitiveservices.azure.com`

**Issue: "ELEVENLABS_API_KEY is not set"**
**Solution**:
- Add `ELEVENLABS_API_KEY=sk_your_key_here` to your `.env` file
- Install websockets: `pip install websockets`

### Connection Issues

**Issue: "Failed to start [API] connection"**
**Solution**:
- Check if ngrok is installed: `which ngrok`
- Check if ngrok is authenticated: `ngrok config add-authtoken YOUR_TOKEN`
- Stop existing ngrok sessions: `pkill ngrok`
- Check ngrok logs: `cat /tmp/ngrok.log`

**Issue: "API handler not available"**
**Solution**:
- For ElevenLabs: Install websockets with `pip install websockets`
- For Azure OpenAI: Install websocket-client with `pip install websocket-client`
- Restart the application after installing dependencies

### Issue: "Port 8000 is already in use"

**Solution**:
- Find the process using port 8000: `lsof -i :8000`
- Kill the process: `kill -9 <PID>`
- Or change the port in `voicesearch_app.py` (line 602)

### Issue: Cannot access from external IP (EC2/Server)

**Solution**:
- The app now binds to `0.0.0.0:8000` for external access
- **Security Group**: Make sure port 8000 is open in your EC2 security group:
  - Type: Custom TCP
  - Port: 8000
  - Source: 0.0.0.0/0 (or your specific IP range)
- **Firewall**: On Ubuntu, check if ufw is blocking:
  ```bash
  sudo ufw status
  sudo ufw allow 8000
  ```
- **Access URL**: Use your EC2 public IP: `http://YOUR_EC2_PUBLIC_IP:8000`
- **HTTPS**: For microphone access, you may need HTTPS or use ngrok

### Issue: "Module not found" errors

**Solution**:
- Make sure virtual environment is activated: `source venv/bin/activate`
- Install requirements: `pip install -r requirements.txt`
- For ElevenLabs: `pip install websockets`
- For Azure OpenAI: `pip install websocket-client`

### Issue: Microphone not working

**Solution**:
- Check browser permissions for microphone access
- Use HTTPS or localhost (browsers require secure context for microphone)
- Check browser console for errors (F12 → Console)

## 🧪 Testing

Run the test suite:
```bash
pytest tests/
```

## 📝 API Endpoints

### WebSocket Events

**Client → Server:**
- `toggle_transcription`: Start/stop transcription with API selection
  ```javascript
  { 
    action: "start", 
    api: "Deepgram API", // or "Azure OpenAI" or "ElvenLabs ScribeV2"
    language: "English" 
  }
  { action: "stop", api: "Deepgram API" }
  ```
- `audio_stream`: Send audio data chunks
- `restart_deepgram`: Restart Deepgram connection with new language (Deepgram only)

**Server → Client:**
- `transcription_update`: Receive transcription text
  ```javascript
  { transcription: "Hello world" }
  ```
- `transcription_status`: Connection status updates with API info
  ```javascript
  { status: "started", api: "Azure OpenAI" }
  ```
- `silence_timeout`: Notification when silence timeout occurs

## 🔒 Security Notes

- Never commit your `.env` file to version control
- Keep all API keys secure (Deepgram, Azure OpenAI, ElevenLabs)
- Use HTTPS in production
- Consider implementing authentication for production use
- Each API provider has different rate limits and usage policies

## 📄 License

See [LICENSE](LICENSE) file for details.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## 📞 Support

For issues related to:
- **Deepgram API**: [Deepgram Documentation](https://developers.deepgram.com)
- **Azure OpenAI**: [Azure OpenAI Documentation](https://docs.microsoft.com/en-us/azure/cognitive-services/openai/)
- **ElevenLabs API**: [ElevenLabs Documentation](https://elevenlabs.io/docs)
- **Application Issues**: Check the logs (`voicesearch_app.log`) for error details

## 🎯 Features in Detail

### API Comparison

| Feature | Deepgram API | Azure OpenAI | ElevenLabs ScribeV2 |
|---------|-------------|--------------|-------------------|
| **Best For** | General purpose, production | Auto language detection | Voice activity detection |
| **Model** | Nova-3 | gpt-4o-mini-transcribe | scribe_v2_realtime |
| **Language Selection** | Manual | Automatic | Manual or Auto |
| **Response Time** | Very Fast | Fast | Fast |
| **Accuracy** | High | Very High | High |
| **Special Features** | Live transcription | GPT-powered | Advanced VAD |
| **Dependencies** | None | websocket-client | websockets |

### Multiple Transcription APIs
- **Deepgram API**: Uses Nova-3 model for fast, accurate transcription
- **Azure OpenAI**: Leverages GPT-4o-mini-transcribe for intelligent language detection
- **ElevenLabs ScribeV2**: Advanced voice activity detection with real-time streaming

### Real-time Transcription
- Low latency streaming transcription across all APIs
- Supports interim results for better UX
- API-specific optimizations for each provider

### Multi-language Support
- Many languages supported across all APIs
- Automatic language detection (Azure OpenAI, ElevenLabs)
- Manual language selection (Deepgram)

### Performance Tracking
- Tracks response time for each transcription
- Logs session duration and transcription count
- API-specific performance metrics
- Helps identify performance bottlenecks

### Automatic Silence Detection
- Configurable timeout period (applies to all APIs)
- Automatically stops recording after silence
- Prevents unnecessary API calls and costs

## 🚀 Deployment

For production deployment:

1. **Use a production WSGI server** (not Flask's development server):
   ```bash
   pip install gunicorn
   gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 voicesearch_app:app
   ```

2. **Set up proper environment variables** on your server

3. **Use HTTPS** for microphone access (browsers require secure context)

4. **Configure reverse proxy** (nginx/Apache) if needed

5. **Set up proper logging** and log rotation

6. **Monitor performance logs** regularly

---

**Made with ❤️ using Flask, SocketIO, Deepgram, Azure OpenAI, and ElevenLabs**
