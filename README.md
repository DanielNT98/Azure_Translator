# Azure Translator - Dual Mode Architecture

This project supports **two translation modes** with a shared modular architecture:

## 🎯 Translation Modes

### 1. DeepL Mode (`main_deepl.py`)
- **Translation Engine**: DeepL Voice API (WebSocket streaming)
- **Features**: 
  - Combined STT + Translation in single service
  - Continuous audio streaming
  - Real-time WebSocket communication

### 2. Azure Mode (`main_azure.py`)
- **Translation Engine**: Azure Speech Translation API
- **Features**:
  - Separate STT + Text Translation
  - Balanced chunking for partial results
  - Event-driven architecture

## 📁 Project Structure

```
Azure__Transaltor/
├── main_deepl.py          # 🟢 Entry point for DeepL mode
├── main_azure.py           # 🔵 Entry point for Azure mode
├── config/
│   └── settings.py         # Configuration (shared by both modes)
├── core/
│   ├── audio_devices.py    # Device discovery (shared)
│   ├── vad.py              # Voice Activity Detection (shared)
│   └── turn_manager.py     # Turn-taking manager (shared)
├── translation/
│   ├── deepl_translator.py # DeepL WebSocket client
│   ├── azure_translator.py # Azure STT + Translation
│   └── chunker.py          # Balanced text chunker (Azure only)
├── tts/
│   ├── azure_tts.py        # Standard Azure TTS (shared)
│   └── realtime_azure_tts.py  # RealtimeTTS (3x faster, optional)
├── audio/
│   └── capture_pipe.py     # Audio capture + VAD (shared)
└── utils/
    ├── timing.py           # Timing utilities (shared)
    ├── helpers.py          # Audio conversion (shared)
    └── analyze_timing.py   # Log analysis (shared)
```

## 🚀 Running the System

### DeepL Mode
```bash
python main_deepl.py
```

### Azure Mode
```bash
python main_azure.py
```

## 🔧 Configuration

All settings in `config/settings.py` and `.env` file:

```env
# Azure Services
SPEECH__SERVICE__KEY=your_azure_key
SPEECH__SERVICE__REGION=westeurope

# DeepL API
DEEPL_API_KEY=your_deepl_key

# DeepL Experimental Mode (Lower latency, may cause hallucinations)
DEEPL_EXPERIMENTAL_MODE=1  # 1=enabled, 0=disabled

# Languages
SRC_LANG=it
TGT_LANG_UP=en
DOWN_SRC_LANG=en
TGT_LANG_DOWN=it

# Audio Devices
CABLE_A_PLAYBACK=CABLE-A Input
CABLE_B_CAPTURE=CABLE-B Output
MY_HEADPHONES=Headphones
MIC_DEVICE_NAME=Jack Mic

# VAD Settings
VAD_THRESHOLD=0.025
VAD_HANGOVER_MS=200

# TTS Settings
SPEECH_RATE=+35%

# Azure-specific (for main_azure.py)
USE_PARTIALS=0           # 0=finals only, 1=partials+finals
FINAL_SEG_MS=100
FIRST_CHUNK_WORDS=2
SUBSEQUENT_CHUNK_WORDS=5

# Call Center Mode
CALL_CENTER_MODE=0       # 0=disabled, 1=enabled
KEYBOARD_SOUND_FILE=computer-keyboard-typing-290582.wav
```

## 🆚 Mode Comparison

| Feature | DeepL Mode | Azure Mode |
|---------|------------|------------|
| **Translation Engine** | DeepL Voice API | Azure Speech Translation |
| **Architecture** | WebSocket streaming | Event-driven callbacks |
| **Latency** | ~2.5s (standard)<br>**~1.5-2s** (experimental) | ~3.5-4s |
| **Chunking** | Not needed | BalancedChunker |
| **Partial Results** | Built-in | Optional (USE_PARTIALS) |
| **Cost** | DeepL API | Azure Speech + Translation |

## 📊 Shared Components

Both modes share these core components:

1. **Audio Capture** (`audio/capture_pipe.py`)
   - Captures from microphone and virtual cables
   - Voice Activity Detection
   - Pass-through mixing
   - Call center keyboard mode
   - **Dual support**: Pushes to DeepL translator OR Azure push stream

2. **Voice Activity Detection** (`core/vad.py`)
   - Energy-based VAD
   - Adaptive threshold
   - 500ms hangover

3. **Turn Manager** (`core/turn_manager.py`)
   - Manages A/B channel priority
   - Hysteresis and soft-locking
   - Barge-in detection

4. **TTS Engine** (2 options)
   - **Standard** (`tts/azure_tts.py`): Azure Neural Voices, SSML speed control
   - **RealtimeTTS** (`tts/realtime_azure_tts.py`): Ultra-low latency (3x faster)
     - Enable with `USE_REALTIME_TTS=1` in `.env`
     - See `REALTIME_TTS_GUIDE.md` for details
   - Both use thread-pooled synthesis and queue-based playback

5. **Device Management** (`core/audio_devices.py`)
   - Auto-discovery
   - Fuzzy matching
   - Fallback logic

## 🎓 Architecture Decisions

### Why Two Modes?

1. **DeepL Mode**: Production-ready, best performance
2. **Azure Mode**: For Azure-only environments or when more control needed

### Code Reuse Strategy

- **Shared**: VAD, audio I/O, TTS, device management, turn control
- **Mode-specific**: Translation client only
- **Benefit**: ~80% code reuse, easy to maintain

### Migration Between Modes

To switch modes, just run the other main file. Configuration is shared, so no changes needed to `.env`.

## ⚡ DeepL Experimental Mode (Lower Latency)

DeepL has released an **experimental feature** that significantly reduces translation latency:

```env
DEEPL_EXPERIMENTAL_MODE=1  # Enable lower latency mode
```

**Benefits:**
- ✅ Much lower latency (faster translations)
- ✅ Better user experience for real-time conversations

**Risks:**
- ⚠️ May cause occasional **hallucinations** (incorrect translations)
- ⚠️ Still in early access, behavior may change

**Recommendation:**
- Enable for **testing and demos** to experience the speed improvement
- Monitor for translation accuracy issues
- Disable if hallucinations become problematic: `DEEPL_EXPERIMENTAL_MODE=0`

This feature uses DeepL's `enable_early_translation_conclusion` parameter to conclude translations earlier in the pipeline, reducing end-to-end latency at the potential cost of accuracy.

## 🐛 Debugging

Both modes generate detailed logs in `logs/` directory:

```
logs/
├── azure_translator_session_YYYYMMDD_HHMMSS.log  # Azure mode
├── deepl_translator_session_YYYYMMDD_HHMMSS.log  # DeepL mode
└── *_timing_report.txt                            # Auto-generated reports
```

Analyze timing with:
```bash
python utils/analyze_timing.py logs/[log_file].log
```

## 💡 Tips

1. **Azure requirements**: Only use Azure mode if mandated by infrastructure
2. **Performance tuning**: Adjust `SPEECH_RATE`, `VAD_THRESHOLD` in `.env`
3. **Call center mode**: Enable `CALL_CENTER_MODE=1` to replace voice with keyboard sound
4. **Partial results**: In Azure mode, set `USE_PARTIALS=1` for streaming (higher CPU usage)

## 🔗 Related Files

- `test/main_8_best.py` - Original monolithic Azure implementation
- `test/main_10_deepl.py` - Original monolithic DeepL implementation
- `COMPARISON_ANALYSIS.md` - Detailed refactoring analysis

## 📝 License

Internal project - Teleperformance Italy
