# VoiceSearch - Real-time Speech Transcription Application

A Flask-based web application that provides real-time speech-to-text transcription using Deepgram's Live Transcription API. The application features a modern web interface, multi-language support, automatic silence detection, and comprehensive performance logging.

## 🚀 Features

- **Real-time Speech Transcription**: Live audio transcription using Deepgram's advanced speech recognition
- **Multi-language Support**: Supports 16 languages including English, Spanish, French, German, Hindi, Japanese, and more
- **Automatic Silence Detection**: Automatically stops recording after a configurable period of silence
- **Performance Logging**: Detailed performance metrics for each transcription session
- **WebSocket Communication**: Real-time bidirectional communication between client and server
- **Public URL Support**: Optional ngrok integration for public access
- **Modern UI**: Clean and intuitive user interface with real-time transcription display

## 📋 Requirements

- Python 3.8 or higher
- Deepgram API key ([Get one here](https://console.deepgram.com/signup))
- (Optional) ngrok for public URL access ([Download here](https://ngrok.com/download))

## 🛠️ Installation

1. **Clone the repository** (if applicable) or navigate to the project directory:
   ```bash
   cd flask-live-transcription
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
   Create a `.env` file in the root directory:
   ```bash
   DEEPGRAM_API_KEY=your_deepgram_api_key_here
   SILENCE_TIMEOUT=5000
   ```
   
   - `DEEPGRAM_API_KEY`: Your Deepgram API key (required)
   - `SILENCE_TIMEOUT`: Silence timeout in milliseconds (optional, default: 5000ms)

## 🚀 Quick Start

### Option 1: Using the Startup Script (Recommended)

**Start locally:**
```bash
./start.sh
```

**Start with ngrok (public URL):**
```bash
./start.sh --ngrok
# or
./start.sh -n
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
- Start the Flask application
- Display connection URLs

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
2. **Select a language** from the dropdown menu
3. **Click the microphone button** to start recording
4. **Speak** - your speech will be transcribed in real-time
5. **Click the microphone button again** to stop recording
6. The transcription will automatically stop after the silence timeout period

### Supported Languages

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

## 📊 Logging

The application generates two types of logs:

### 1. Application Log (`voicesearch_app.log`)
Contains general application events:
- Server startup/shutdown
- Client connections/disconnections
- Deepgram connection events
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
# Required
DEEPGRAM_API_KEY=your_api_key_here

# Optional
SILENCE_TIMEOUT=5000  # Milliseconds (default: 5000)
```

### Silence Timeout

The `SILENCE_TIMEOUT` setting controls how long the application waits for silence before automatically stopping the recording. Value is in milliseconds:
- `5000` = 5 seconds (default)
- `10000` = 10 seconds
- `3000` = 3 seconds

## 📁 Project Structure

```
flask-live-transcription/
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

### Issue: "Failed to start ngrok"

**Solution**: 
- Check if ngrok is installed: `which ngrok`
- Check if ngrok is authenticated: `ngrok config add-authtoken YOUR_TOKEN`
- Stop existing ngrok sessions: `pkill ngrok`
- Check ngrok logs: `cat /tmp/ngrok.log`

### Issue: "DEEPGRAM_API_KEY is not set"

**Solution**:
- Create a `.env` file in the root directory
- Add your API key: `DEEPGRAM_API_KEY=your_key_here`
- Make sure the file is named exactly `.env` (not `env` or `.env.txt`)

### Issue: "Port 8000 is already in use"

**Solution**:
- Find the process using port 8000: `lsof -i :8000`
- Kill the process: `kill -9 <PID>`
- Or change the port in `voicesearch_app.py` (line 321)

### Issue: "Module not found" errors

**Solution**:
- Make sure virtual environment is activated: `source venv/bin/activate`
- Install requirements: `pip install -r requirements.txt`

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
- `toggle_transcription`: Start/stop transcription
  ```javascript
  { action: "start", language: "English" }
  { action: "stop" }
  ```
- `audio_stream`: Send audio data chunks
- `restart_deepgram`: Restart Deepgram connection with new language

**Server → Client:**
- `transcription_update`: Receive transcription text
  ```javascript
  { transcription: "Hello world" }
  ```
- `transcription_status`: Connection status updates
- `silence_timeout`: Notification when silence timeout occurs

## 🔒 Security Notes

- Never commit your `.env` file to version control
- Keep your Deepgram API key secure
- Use HTTPS in production
- Consider implementing authentication for production use

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
- **Application Issues**: Check the logs (`voicesearch_app.log`) for error details

## 🎯 Features in Detail

### Real-time Transcription
- Uses Deepgram's Live Transcription API
- Low latency streaming transcription
- Supports interim results for better UX

### Multi-language Support
- 16 languages supported
- Easy language switching
- Uses Deepgram's latest models (nova-3 for most languages)

### Performance Tracking
- Tracks response time for each transcription
- Logs session duration and transcription count
- Helps identify performance bottlenecks

### Automatic Silence Detection
- Configurable timeout period
- Automatically stops recording after silence
- Prevents unnecessary API calls

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

**Made with ❤️ using Flask, SocketIO, and Deepgram**
