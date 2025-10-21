# 🎙️ ElevenLabs TTS Integration - Complete Setup

## 📦 Created Files

### 1. **tts/elevenlabs_tts.py** (Main Module)
- `ElevenLabsEngine` Class: Custom engine for RealtimeTTS
- `ElevenLabsTTS` Class: Complete wrapper with queue and threading management
- Real-time audio streaming support
- Text deduplication handling
- Complete timing logging

### 2. **config/settings.py** (Added Configuration)
```python
USE_ELEVENLABS_TTS = get_env("USE_ELEVENLABS_TTS", "0") == "1"
ELEVENLABS_API_KEY = get_env("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = get_env("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVENLABS_MODEL = get_env("ELEVENLABS_MODEL", "eleven_turbo_v2_5")
```

### 3. **translation/deepl_translator.py** (Modified)
- Added ElevenLabs engine support
- Priority: ElevenLabs > RealtimeTTS > Azure Standard
- Conditional initialization based on flags

### 4. **.env.example** (Updated)
- ElevenLabs variables documentation
- Popular voices list
- Models explanation

### 5. **tts/ELEVENLABS_README.md** (Documentation)
- Complete setup guide
- Azure vs ElevenLabs comparison
- Troubleshooting
- Best practices

### 6. **test_elevenlabs.py** (Test Script)
- Isolated integration test
- Available voices list
- Configuration verification

---

## 🚀 Quick Start

### Step 1: Install Dependencies

```bash
pip install elevenlabs pydub
```

**Important**: `pydub` is required to decode ElevenLabs MP3 audio to PCM16.

### ⚠️ **Step 1.5: CRITICAL - Modify RealtimeTTS for Audio Routing**

**If you use virtual audio cables (VB-Cable, etc.), you MUST modify the `elevenlabs_engine.py` file to support PCM format!**

Without this modification, audio will go to the default device instead of Cable-A/B.

**File to modify**:
```
C:\Users\[USER]\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\LocalCache\local-packages\Python311\site-packages\RealtimeTTS\engines\elevenlabs_engine.py
```

**Required modifications** (see README.md section "ElevenLabs TTS - Audio Routing Fix"):

1. Add parameter `output_format="pcm_24000"` to `__init__()`
2. Add `self.output_format` and sample rate parsing
3. Modify `get_stream_info()` to return `pyaudio.paInt16` instead of `pyaudio.paCustomFormat`
4. Pass `output_format=self.output_format` to `client.text_to_speech.stream()`

**Why is it necessary?**
- **MP3**: API → MP3 → Decode → Loses device routing → ❌ Default device
- **PCM**: API → PCM → No decode → ✅ Correct Cable-A

**Available PCM formats**: `pcm_8000`, `pcm_16000`, `pcm_22050`, `pcm_24000`, `pcm_32000`, `pcm_44100`, `pcm_48000`

**Recommended**: `pcm_24000` (balance quality/latency)

### Step 2: Configure API Key

1. Go to [ElevenLabs](https://elevenlabs.io)
2. Sign up and get API key
3. Add to `.env` file:

```bash
USE_ELEVENLABS_TTS=1
ELEVENLABS_API_KEY=sk_your_api_key_here
```

### Step 3: Isolated Test

```bash
# Basic test
python test_elevenlabs.py

# List available voices
python test_elevenlabs.py --list-voices
```

### Step 4: Use in System

```bash
python main_deepl.py
```

The system will now use ElevenLabs instead of Azure!

---

## 🔧 Advanced Configuration

### Change Voice

```bash
# In .env
ELEVENLABS_VOICE_ID=EXAVITQu4vr4xnSDxMaL  # Bella (female, soft)
```

### Change Model

```bash
# In .env
ELEVENLABS_MODEL=eleven_multilingual_v2  # Higher quality
```

### Return to Azure

```bash
# In .env
USE_ELEVENLABS_TTS=0
USE_REALTIME_TTS=1
```

---

## 📊 Architecture

```
main_deepl.py
    │
    └─> DeepLTranslator
            │
            ├─> [IF USE_ELEVENLABS_TTS=1]
            │   └─> ElevenLabsTTS
            │       ├─> ElevenLabsEngine
            │       │   └─> ElevenLabs API
            │       └─> RealtimeTTS Framework
            │
            └─> [IF USE_ELEVENLABS_TTS=0]
                └─> RealtimeAzureTTS
                    └─> Azure Neural TTS
```

---

## ⚙️ Parameter Tuning

In `tts/elevenlabs_tts.py`, you can modify:

```python
# Lines 192-198
self.buffer_threshold = 0.1  # Initial buffer (seconds)
self.context_size = 20       # Context words
self.force_first_fragment_after_words = 8  # Early flush
self.pre_buffer_ms = 20      # Pre-buffering (ms)
self.lookahead_cycles = 5    # Lookahead cycles
```

**For maximum reactivity** (similar to optimizations made):
```python
self.buffer_threshold = 0.0
self.context_size = 15
self.force_first_fragment_after_words = 4
self.pre_buffer_ms = 0
self.lookahead_cycles = 3
```

---

## 🎯 Recommended Voices for Italian

| Voice Name | Voice ID | Characteristics |
|------------|----------|-----------------|
| **Rachel** | `21m00Tcm4TlvDq8ikWAM` | Clear English, neutral |
| **Bella** | `EXAVITQu4vr4xnSDxMaL` | Sweet, friendly |
| **Giovanni** | Custom | Native Italian (requires Voice Cloning) |

**Note**: Default voices are optimized for English. For high-quality native Italian, consider:
1. Using Voice Cloning (Pro plan)
2. Creating a custom Italian voice
3. Testing `eleven_multilingual_v2` with existing voices

---

## 💡 ElevenLabs Advantages

✅ **Superior Audio Quality**: More natural and expressive voice
✅ **Low Latency**: ~300-500ms with `eleven_turbo_v2_5`
✅ **Flexibility**: Wide choice of voices and styles
✅ **Voice Cloning**: Ability to clone voices (advanced plans)

## ⚠️ Disadvantages

❌ **Costs**: Requires subscription after free tier
❌ **Rate Limits**: Limits on requests/second
❌ **External Dependency**: Requires connection to ElevenLabs
❌ **Italian**: Limited native Italian voices (vs Azure)

---

## 🧪 Testing

### Test 1: Verify Setup
```bash
python test_elevenlabs.py
```
Expected output: "Hello! This is a test..." played

### Test 2: List Voices
```bash
python test_elevenlabs.py --list-voices
```
Expected output: Complete list of available voices

### Test 3: Full Integration
```bash
python main_deepl.py
```
Expected log: `"🎙️ Using ElevenLabs TTS (EXPERIMENTAL)"`

---

## 📝 Important Notes

1. **Free Tier**: 10,000 characters/month (about 2000 words)
2. **Streaming**: Works in streaming mode for low latency
3. **MP3 Format**: ElevenLabs uses MP3, Azure uses PCM16
4. **Compatibility**: Fully compatible with existing DeepLTranslator
5. **Fallback**: Always keep Azure configured as backup

---

## 🆘 Troubleshooting

### Error: "Import elevenlabs could not be resolved"
```bash
pip install elevenlabs
```

### Error: "Missing ElevenLabs API key"
Verify `.env`:
```bash
ELEVENLABS_API_KEY=sk_xxxxxxxxxxxxx
```

### No audio
1. Verify output device
2. Check system volume
3. Verify log: look for `"Stream started"`

### ⚠️ **Audio goes to wrong device (doesn't go to Cable-A/B)**

**Symptom**: Audio comes out of speakers/headphones instead of VB-Cable

**Cause**: ElevenLabs uses MP3 by default, which loses routing during decoding

**Solution**: Modify `elevenlabs_engine.py` to use PCM format (see Step 1.5 above)

**Verify if this is the problem**:
1. Azure TTS works correctly on Cable-A ✅
2. ElevenLabs goes to default device ❌
3. Logs show: `[UP]-TTS ElevenlabsEngine created successfully` but not `(PCM mode)`

**After modification, you should see**:
```
[UP]-TTS Initializing ElevenlabsEngine with voice=..., model=..., output_format=pcm_24000
[UP]-TTS ElevenlabsEngine created successfully (PCM mode)
```

### High latency
Change model:
```bash
ELEVENLABS_MODEL=eleven_turbo_v2_5
```

### Wrong voice (speaks Rachel instead of Antonio)
Verify that voice_id patching works in logs:
```
[UP]-TTS ✅ Patched engine.id: piTKgcLEGmPE4e6mEKli → uScy1bXtKz8vPzfdFsFw
```

If you don't see this, patching doesn't work. Voice_id must be set correctly in `.env`.

---

## 📚 Next Steps

1. ✅ **Isolated Test**: Verify basic functionality
2. ✅ **Integration Test**: Use in complete system
3. 🔄 **Comparison**: Compare with Azure for quality/latency
4. ⚙️ **Tuning**: Optimize parameters for your use case
5. 💰 **Evaluation**: Decide if subscription is worth it

---

## 🎓 Resources

- [ElevenLabs Docs](https://elevenlabs.io/docs)
- [API Reference](https://elevenlabs.io/docs/api-reference)
- [Voice Library](https://elevenlabs.io/voice-library)
- [Pricing](https://elevenlabs.io/pricing)

---

**Happy testing! 🚀**

For questions or issues, check system logs or the [tts/ELEVENLABS_README.md](../tts/ELEVENLABS_README.md) file for detailed troubleshooting.
