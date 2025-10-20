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
├── main_deepl.py                    # 🟢 Entry point for DeepL mode
├── main_azure.py                    # 🔵 Entry point for Azure mode
├── test_elevenlabs.py               # 🧪 ElevenLabs TTS test script
├── requirements.txt                 # Python dependencies
├── .env.example                     # Environment variables template
│
├── config/
│   └── settings.py                  # Configuration (shared by both modes)
│
├── core/
│   ├── __init__.py
│   ├── audio_devices.py             # Device discovery (shared)
│   ├── vad.py                       # Voice Activity Detection (shared)
│   └── turn_manager.py              # Turn-taking manager (shared)
│
├── translation/
│   ├── __init__.py
│   ├── deepl_translator.py          # DeepL WebSocket client
│   ├── azure_translator.py          # Azure STT + Translation
│   └── chunker.py                   # Balanced text chunker (Azure only)
│
├── tts/
│   ├── __init__.py
│   ├── azure_tts.py                 # Standard Azure TTS (shared)
│   ├── realtime_azure_tts.py        # RealtimeTTS (3x faster, optional)
│   ├── elevenlabs_tts.py            # ElevenLabs TTS (experimental)
│   └── ELEVENLABS_README.md         # ElevenLabs documentation
│
├── audio/
│   ├── __init__.py
│   └── capture_pipe.py              # Audio capture + VAD (shared)
│
├── utils/
│   ├── __init__.py
│   ├── timing.py                    # Timing utilities (shared)
│   ├── helpers.py                   # Audio conversion (shared)
│   └── analyze_timing.py            # Log analysis (shared)
│
├── logs/                            # Auto-generated session logs
│
├── test/                            # Legacy test files
│   ├── main_8_best.py              # Original Azure implementation
│   └── main_10_deepl.py            # Original DeepL implementation
│
└── docs/                            # Documentation
    ├── README.md                    # This file (main documentation)
    ├── OPTIMIZATION_SUMMARY.md      # Performance optimizations v2.0
    ├── REALTIME_TTS_GUIDE.md        # RealtimeTTS integration guide
    ├── REFACTORING_SUMMARY.md       # Architecture refactoring details
    ├── DEEPL_EXPERIMENTAL_FEATURE.md # DeepL low-latency mode
    ├── DOCSTRINGS_COMPLETE.md       # Docstring enhancement report
    ├── ELEVENLABS_INDEX.md          # ElevenLabs navigation guide
    ├── ELEVENLABS_QUICKSTART.md     # ElevenLabs quick start (4 steps)
    ├── ELEVENLABS_SETUP.md          # ElevenLabs complete setup
    ├── ELEVENLABS_PCM_FIX.md        # Audio routing fix (CRITICAL)
    ├── ELEVENLABS_PCM_DIFF.md       # Code diff for developers
    └── ELEVENLABS_COMMIT_SUMMARY.md # ElevenLabs integration summary
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
USE_ELEVENLABS_TTS=0     # 0=Azure TTS, 1=ElevenLabs TTS (experimental)

# ElevenLabs (if USE_ELEVENLABS_TTS=1)
ELEVENLABS_API_KEY=your_elevenlabs_key
ELEVENLABS_VOICE_ID=uScy1bXtKz8vPzfdFsFw  # Antonio Farina (Italian)
ELEVENLABS_MODEL=eleven_flash_v2_5

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
| **TTS Options** | Azure / ElevenLabs | Azure / ElevenLabs |
| **Cost** | DeepL API | Azure Speech + Translation |

> **Note**: ElevenLabs TTS requires PCM format modification (see section below) for proper audio routing to virtual cables.

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

4. **TTS Engine** (3 options)
   - **Standard Azure** (`tts/azure_tts.py`): Azure Neural Voices, SSML speed control
   - **RealtimeTTS Azure** (`tts/realtime_azure_tts.py`): Ultra-low latency (3x faster)
     - Enable with `USE_REALTIME_TTS=1` in `.env`
     - See `REALTIME_TTS_GUIDE.md` for details
   - **ElevenLabs TTS** (`tts/elevenlabs_tts.py`): Premium voice quality (EXPERIMENTAL)
     - Enable with `USE_ELEVENLABS_TTS=1` in `.env`
     - See `ELEVENLABS_SETUP.md` for configuration
     - **⚠️ IMPORTANT**: Requires PCM format for audio routing (see below)
   - All use thread-pooled synthesis and queue-based playback

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

## 🎙️ ElevenLabs TTS - Audio Routing Fix (CRITICAL)

When using ElevenLabs TTS with virtual audio devices (e.g., VB-Cable), **you must configure PCM format** to ensure proper audio routing.

### ⚠️ The Problem

By default, ElevenLabs API returns **MP3 audio**. During MP3 decoding, the device routing information is lost, causing audio to play on the default device instead of your specified virtual cable.

```
❌ MP3 Flow (BROKEN):
ElevenLabs API → MP3 stream → pydub decode → PCM → PyAudio → ❌ Default device

✅ PCM Flow (FIXED):
ElevenLabs API → PCM stream → PyAudio(device=10) → ✅ Cable-A Input
```

### 🔧 Required Modification

> **📖 Complete Guide**: See `ELEVENLABS_PCM_FIX.md` for detailed step-by-step instructions with troubleshooting.

**File**: `C:\Users\[USER]\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\LocalCache\local-packages\Python311\site-packages\RealtimeTTS\engines\elevenlabs_engine.py`

**Changes** (summary - see ELEVENLABS_PCM_FIX.md for full details):

1. **Add `output_format` parameter** to `__init__()`:
```python
def __init__(
    self,
    api_key: str = "",
    voice: str = "Nicole",
    id: str = "piTKgcLEGmPE4e6mEKli",
    category: str = "premade",
    clarity: float = 75.0,
    stability: float = 50.0,
    style_exxageration: float = 0.0,
    model: str = "eleven_multilingual_v2",
    output_format: str = "pcm_24000",  # ← ADD THIS LINE
):
```

2. **Store format and parse sample rate**:
```python
    self.output_format = output_format  # ← ADD THIS LINE
    
    # Parse sample rate from format string
    if output_format.startswith('pcm_'):
        self.sample_rate = int(output_format.split('_')[1])
    else:
        self.sample_rate = 24000  # Default fallback
```

3. **Update `get_stream_info()` method**:
```python
def get_stream_info(self):
    # Return PCM format info instead of paCustomFormat (MP3)
    if self.output_format.startswith('pcm_'):
        return pyaudio.paInt16, 1, self.sample_rate
    else:
        # Fallback to MP3 if not PCM
        return pyaudio.paCustomFormat, -1, -1
```

4. **Pass `output_format` to API** in `synthesize()`:
```python
audio_stream = self.client.text_to_speech.stream(
    text=text,
    voice_id=self.id,
    model_id=self.model,
    output_format=self.output_format,  # ← ADD THIS LINE
)
```

### 📋 Available PCM Formats

ElevenLabs supports these PCM formats:
- `pcm_8000` (8 kHz)
- `pcm_16000` (16 kHz)
- `pcm_22050` (22.05 kHz)
- `pcm_24000` (24 kHz) ← **Recommended**
- `pcm_32000` (32 kHz)
- `pcm_44100` (44.1 kHz)
- `pcm_48000` (48 kHz)

**Recommendation**: Use `pcm_24000` for optimal balance between quality and latency.

### ✅ Verification

After modification, check logs for:
```
[UP]-TTS Initializing ElevenlabsEngine with voice=..., model=..., output_format=pcm_24000
[UP]-TTS ElevenlabsEngine created successfully (PCM mode)
```

Audio should now route correctly to your specified virtual cable device!

### 📝 Why This Works

- **Azure TTS**: Returns PCM/WAV directly (`riff-16khz-16bit-mono-pcm`) → Device routing preserved ✅
- **ElevenLabs (default)**: Returns MP3 → Decode loses device context → Routes to default ❌
- **ElevenLabs (PCM)**: Returns PCM directly → No decode → Device routing preserved ✅

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

## ⚡ Performance Optimizations (v2.0)

The system includes advanced optimizations for ultra-low latency and smooth audio:

### 🚀 RealtimeTTS Integration
- **Continuous Generator Pattern**: Single audio stream with no gaps between chunks
- **Adaptive Buffering**: Fast first chunk (~1.0s), smooth subsequent chunks
- **Smart Batching**: Intelligent chunk aggregation for optimal audio quality
- **Performance**: 60% faster on first chunk, 0ms gaps between chunks

### 🎤 Premium Italian Voice
- Voice: `it-IT-IsabellaNeural` (replaces ElsaNeural)
- Natural Italian accents and prosody
- Premium expressive intonation

### 🔌 WebSocket Stability
- Automatic reconnection with exponential backoff
- Built-in ping/pong (10s interval)
- Unlimited uptime, <1% lost translations

**Technical Documents:**
- `OPTIMIZATION_SUMMARY.md` - Complete optimization details
- `REALTIME_TTS_GUIDE.md` - RealtimeTTS guide
- `DEEPL_EXPERIMENTAL_FEATURE.md` - DeepL low-latency feature

### Performance Metrics

| Metric | Before | After v2.0 | Improvement |
|---------|-------|-----------|---------------|
| First chunk | ~2.5s | **~1.0s** | **-60%** ✅ |
| Gap between chunks | 100-200ms | **0ms** | **-100%** ✅ |
| TTS synthesis | ~150ms | **~75ms** | **-50%** ✅ |

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

### Core Documentation
- [README.md](README.md) - This file (system overview)
- [OPTIMIZATION_SUMMARY.md](OPTIMIZATION_SUMMARY.md) - Performance optimizations v2.0
- [REALTIME_TTS_GUIDE.md](REALTIME_TTS_GUIDE.md) - RealtimeTTS integration guide
- [DEEPL_EXPERIMENTAL_FEATURE.md](DEEPL_EXPERIMENTAL_FEATURE.md) - DeepL low-latency mode
- [REFACTORING_SUMMARY.md](REFACTORING_SUMMARY.md) - Detailed refactoring analysis
- [DOCSTRINGS_COMPLETE.md](DOCSTRINGS_COMPLETE.md) - Docstring enhancement report

### ElevenLabs TTS Documentation
- **[ELEVENLABS_INDEX.md](ELEVENLABS_INDEX.md)** - 📚 Start here! Navigation guide
- **[ELEVENLABS_QUICKSTART.md](ELEVENLABS_QUICKSTART.md)** - ⚡ Quick start (4 steps)
- **[ELEVENLABS_PCM_FIX.md](ELEVENLABS_PCM_FIX.md)** - 🔧 Fix audio routing (CRITICAL)
- **[ELEVENLABS_PCM_DIFF.md](ELEVENLABS_PCM_DIFF.md)** - 📝 Code diff (for developers)
- **[ELEVENLABS_SETUP.md](ELEVENLABS_SETUP.md)** - 📖 Complete setup guide
- **[ELEVENLABS_COMMIT_SUMMARY.md](ELEVENLABS_COMMIT_SUMMARY.md)** - Integration summary
- [tts/ELEVENLABS_README.md](tts/ELEVENLABS_README.md) - 🧪 TTS module documentation
- [test_elevenlabs.py](test_elevenlabs.py) - 🧪 Test script

### Legacy/Test Files
- [test/main_8_best.py](test/main_8_best.py) - Original monolithic Azure implementation
- [test/main_10_deepl.py](test/main_10_deepl.py) - Original monolithic DeepL implementation

## 🎙️ ElevenLabs TTS - Quick Reference

### Quick Setup
```bash
# 1. Install
pip install elevenlabs pydub

# 2. Configure .env
USE_ELEVENLABS_TTS=1
ELEVENLABS_API_KEY=sk_your_key
ELEVENLABS_VOICE_ID=uScy1bXtKz8vPzfdFsFw  # Antonio Farina
ELEVENLABS_MODEL=eleven_flash_v2_5

# 3. ⚠️ CRITICAL: Modify RealtimeTTS for audio routing
# See ELEVENLABS_PCM_FIX.md for complete instructions

# 4. Launch
python main_deepl.py
```

### Useful Documents
- **`ELEVENLABS_SETUP.md`**: Complete setup, configuration, troubleshooting
- **`ELEVENLABS_PCM_FIX.md`**: Detailed audio routing fix guide (CRITICAL)
- **`test_elevenlabs.py`**: Isolated test script

### When to Use ElevenLabs
✅ **Advantages**:
- Premium voice quality (more natural)
- Low latency with turbo models
- Voice cloning available

❌ **Limitations**:
- Requires RealtimeTTS modification for audio routing
- Cost (after free tier)
- Limited native Italian voices vs Azure

## 📝 License

Internal project - Teleperformance Italy
