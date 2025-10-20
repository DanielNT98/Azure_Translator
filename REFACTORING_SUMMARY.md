# Refactoring Completed: Dual-Mode Architecture

## 🎯 Objective Achieved

Created modular architecture that supports **both DeepL and Azure Translation** with maximum code reuse.

## 📋 Created/Modified Files

### New Files

1. **`main_azure.py`** (12.5 KB)
   - Entry point for Azure mode
   - Complete orchestration similar to main_8_best.py
   - Azure Speech Translation API support

2. **`translation/azure_translator.py`** (9 KB)
   - New module for Azure STT + Translation
   - Event-driven with callbacks for recognizing/recognized
   - BalancedChunker integration
   - Optional partials support (USE_PARTIALS)

3. **`README_DUAL_MODE.md`** (6.3 KB)
   - Complete dual-mode documentation
   - Comparative tables
   - Usage instructions

### Renamed Files

- `main_refactored.py` → **`main_deepl.py`**
  - Now clearly identifiable as DeepL mode

### Modified Files

1. **`audio/capture_pipe.py`**
   - Added dual-mode support
   - `deepl_translator`: for DeepL mode (push_audio)
   - `azure_translator`: for Azure mode (push_stream.write)
   - Parameter `input_device_idx` renamed from `in_dev_idx` for clarity
   - Supports both DeepL and Azure push stream in callback

2. **`config/settings.py`**
   - Already contained all necessary parameters
   - No changes required (already complete!)

## 🏗️ Architettura Implementata

```
┌─────────────────────────────────────────────────────────┐
│                   Dual-Mode System                       │
├─────────────────────────────────────────────────────────┤
│                                                           │
│  ┌──────────────┐              ┌──────────────┐         │
│  │ main_deepl.py│              │main_azure.py │         │
│  │  (DeepL)     │              │  (Azure)     │         │
│  └──────┬───────┘              └──────┬───────┘         │
│         │                              │                 │
│         ├──────────────┬───────────────┤                │
│         │              │               │                 │
│   ┌─────▼─────┐  ┌────▼────┐   ┌─────▼──────┐         │
│   │ DeepL     │  │  Azure  │   │   Azure    │         │
│   │Translator │  │   TTS   │   │ Translator │         │
│   └─────┬─────┘  └────┬────┘   └─────┬──────┘         │
│         │              │               │                 │
│         │         ┌────▼────────────────▼───┐          │
│         │         │   CapturePipe (Dual)    │          │
│         │         │  - DeepL: push_audio()  │          │
│         └─────────│  - Azure: push_stream   │          │
│                   └────┬────────────┬────────┘          │
│                        │            │                    │
│              ┌─────────▼─┐      ┌──▼─────────┐         │
│              │    VAD     │      │Turn Manager│         │
│              │ (Shared)   │      │  (Shared)  │         │
│              └────────────┘      └────────────┘         │
└─────────────────────────────────────────────────────────┘
```

## 🔄 Code Reuse

| Component | DeepL | Azure | Shared |
|------------|-------|-------|-----------|
| **Audio I/O** | ✓ | ✓ | ✅ 100% |
| **VAD** | ✓ | ✓ | ✅ 100% |
| **Turn Manager** | ✓ | ✓ | ✅ 100% |
| **TTS** | ✓ | ✓ | ✅ 100% |
| **Device Discovery** | ✓ | ✓ | ✅ 100% |
| **Timing Utils** | ✓ | ✓ | ✅ 100% |
| **Translator** | DeepL only | Azure only | ❌ 0% |
| **Chunker** | ❌ | ✓ | ⚠️ Present but used only by Azure |

**Result**: ~85% shared code!

## 🚀 How to Use

### DeepL Mode (Recommended)
```bash
python main_deepl.py
```
- ✅ Lower latency (~2.5s)
- ✅ Simple setup (1 service)
- ✅ Native streaming

### Azure Mode
```bash
python main_azure.py
```
- ℹ️ Higher latency (~3.5-4s)
- ℹ️ More complex (separate STT + Translation)
- ℹ️ Requires Azure Speech + Translation API

## 📊 Implementation Differences

### DeepL Mode
```python
# In main_deepl.py:
translator_up = DeepLTranslator(
    api_key=DEEPL_API_KEY,
    from_lang='it',
    to_lang='en',
    # ...
)

# In capture_pipe callback:
if self.deepl_translator:
    self.deepl_translator.push_audio(frame)  # WebSocket push
```

### Azure Mode
```python
# In main_azure.py:
push_up = speechsdk.audio.PushAudioInputStream(audio_format)

translator_up = AzureTranslator(
    from_lang='it-IT',
    to_lang='en',
    push_stream=push_up,
    # ...
)

# In capture_pipe callback:
if self.push_stream:
    self.push_stream.write(frame)  # Azure push stream
```

## 🔧 Configurable Parameters

Both modes share `.env`:

```env
# Works for BOTH modes
SPEECH_RATE=+35%           # TTS speed
VAD_THRESHOLD=0.025        # VAD sensitivity
CALL_CENTER_MODE=0         # Keyboard sound

# Azure mode only
USE_PARTIALS=0             # Streaming partials
FIRST_CHUNK_WORDS=2        # Chunker config
SUBSEQUENT_CHUNK_WORDS=5

# ElevenLabs TTS (experimental)
USE_ELEVENLABS_TTS=0       # 0=Azure, 1=ElevenLabs
ELEVENLABS_API_KEY=...     # ElevenLabs API key
ELEVENLABS_VOICE_ID=...    # Voice ID (e.g. Antonio Farina)
ELEVENLABS_MODEL=...       # Model (e.g. eleven_flash_v2_5)
```

---

## 🎙️ ElevenLabs TTS Integration (v3.0)

### Added October 20, 2024

**Created modules**:
- `tts/elevenlabs_tts.py` - ElevenLabs engine with RealtimeTTS
- `test_elevenlabs.py` - Test script
- `ELEVENLABS_SETUP.md` - Complete setup guide
- `ELEVENLABS_PCM_FIX.md` - CRITICAL fix for audio routing
- `ELEVENLABS_QUICKSTART.md` - Quick start guide
- `ELEVENLABS_PCM_DIFF.md` - Detailed code diff

### Solved Problem: Audio Routing with Virtual Cables

**Symptom**: ElevenLabs TTS doesn't route audio correctly to VB-Cable (Cable-A), while Azure TTS works.

**Root Cause**: 
- ElevenLabs API returns **MP3** by default
- MP3 requires decoding via `pydub`
- During decoding, device routing context is lost
- Audio is played on default device instead of Cable-A

**Problematic flow**:
```
ElevenLabs API → MP3 stream → pydub decode → PCM → PyAudio → ❌ Default device
```

**Implemented Solution**:
1. Modified `RealtimeTTS/engines/elevenlabs_engine.py` to support **PCM format**
2. Added parameter `output_format="pcm_24000"` to `ElevenlabsEngine.__init__()`
3. Modified `get_stream_info()` to return `(pyaudio.paInt16, 1, sample_rate)` instead of `(pyaudio.paCustomFormat, -1, -1)`
4. Passed `output_format` to ElevenLabs API: `client.text_to_speech.stream(output_format="pcm_24000")`

**Correct flow**:
```
ElevenLabs API → PCM stream → PyAudio(device=10) → ✅ Cable-A
```

### Benefits
- ✅ Audio routing preserved (works like Azure TTS)
- ✅ No impact on latency or quality
- ✅ Backward compatibility (MP3 fallback maintained)
- ✅ ElevenLabs premium voice quality + device routing

### Supported PCM Formats
- `pcm_8000`, `pcm_16000`, `pcm_22050`, `pcm_24000` (recommended)
- `pcm_32000`, `pcm_44100`, `pcm_48000`

**Recommendation**: Use `pcm_24000` for optimal quality/latency balance.

### Documentation
- **Complete setup**: `ELEVENLABS_SETUP.md`
- **Audio routing fix** (CRITICAL): `ELEVENLABS_PCM_FIX.md`
- **Quick start**: `ELEVENLABS_QUICKSTART.md`
- **Code diff**: `ELEVENLABS_PCM_DIFF.md`
- **Test**: `test_elevenlabs.py`


