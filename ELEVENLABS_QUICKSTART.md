# ⚡ ElevenLabs TTS - Quick Start Guide

## 🚨 IMPORTANT: Audio Routing Fix

**If you use VB-Cable or virtual audio devices, you MUST apply the PCM fix first!**

Without this fix, audio will go to speakers instead of Cable-A.

---

## 📋 Setup in 4 Steps

### 1️⃣ Install Dependencies
```bash
pip install elevenlabs pydub
```

### 2️⃣ Configure API Key

Add to `.env` file:
```env
USE_ELEVENLABS_TTS=1
ELEVENLABS_API_KEY=sk_your_api_key_here
ELEVENLABS_VOICE_ID=uScy1bXtKz8vPzfdFsFw
ELEVENLABS_MODEL=eleven_flash_v2_5
```

### 3️⃣ ⚠️ FIX AUDIO ROUTING (CRITICAL)

**If you use Cable-A/B**, apply this modification:

**File to modify**:
```
C:\Users\[USER]\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\LocalCache\local-packages\Python311\site-packages\RealtimeTTS\engines\elevenlabs_engine.py
```

**Quick modifications**:

1. **Add parameter** `output_format="pcm_24000"` to `__init__()`
2. **Save format**: `self.output_format = output_format`
3. **Parse sample rate**:
   ```python
   if output_format.startswith('pcm_'):
       self.sample_rate = int(output_format.split('_')[1])
   ```
4. **Modify `get_stream_info()`**:
   ```python
   if self.output_format.startswith('pcm_'):
       return pyaudio.paInt16, 1, self.sample_rate
   ```
5. **Pass format to API**:
   ```python
   audio_stream = self.client.text_to_speech.stream(
       text=text,
       voice_id=self.id,
       model_id=self.model,
       output_format=self.output_format,  # ← ADD THIS
   )
   ```

> **📖 Complete guide**: See `ELEVENLABS_PCM_FIX.md` for detailed instructions

### 4️⃣ Test

```bash
# Isolated test
python test_elevenlabs.py

# Complete system
python main_deepl.py
```

---

## ✅ Verify Operation

### Correct Logs
You should see:
```
[UP]-TTS Initializing ElevenlabsEngine with voice=Antonio Farina, model=eleven_flash_v2_5, output_format=pcm_24000
[UP]-TTS ElevenlabsEngine created successfully (PCM mode)  ← Important!
[UP]-TTS ✅ Patched engine.id: ... → uScy1bXtKz8vPzfdFsFw
```

### Audio Routing
- Audio comes out on **Cable-A Input** (not speakers/headphones)
- Voice is **Antonio Farina** (Italian, not Rachel)
- **Continuous** playback (no gaps between phrases)

---

## 🆘 Common Problems

### Audio goes to speakers (not Cable-A)
❌ **Cause**: PCM fix not applied  
✅ **Solution**: See Step 3, apply modifications to `elevenlabs_engine.py`

### Wrong voice (speaks English/Rachel)
❌ **Cause**: Voice ID not patched  
✅ **Solution**: Verify `.env` has `ELEVENLABS_VOICE_ID=uScy1bXtKz8vPzfdFsFw`

### Error "Missing ElevenLabs API key"
❌ **Cause**: API key not configured  
✅ **Solution**: Add `ELEVENLABS_API_KEY=sk_...` to `.env`

### Distorted audio
❌ **Cause**: Sample rate mismatch  
✅ **Solution**: Try `output_format="pcm_16000"` in `elevenlabs_tts.py`

---

## 📚 Complete Documentation

| File | Content |
|------|-----------|
| **`ELEVENLABS_PCM_FIX.md`** | 🔧 Detailed audio routing fix guide (START HERE!) |
| **`ELEVENLABS_SETUP.md`** | 📖 Complete setup, advanced configuration |
| **`README.md`** | 📋 System overview, Azure vs ElevenLabs comparison |
| **`test_elevenlabs.py`** | 🧪 Isolated test script |

---

## 🎯 Why is the PCM Fix Necessary?

```
❌ MP3 (DEFAULT):
ElevenLabs → MP3 → pydub decode → PCM → PyAudio → Default device ❌

✅ PCM (FIXED):
ElevenLabs → PCM → PyAudio(device=10) → Cable-A ✅
```

**Azure TTS works** because it already uses PCM, ElevenLabs uses MP3 by default.

The modification **forces ElevenLabs to return PCM**, avoiding the decoding that loses routing.

---

## 💡 Tips

- **Backup**: Always before modifying `elevenlabs_engine.py`
- **Incremental test**: First isolated test, then complete system
- **Recommended format**: `pcm_24000` (balance quality/latency)
- **Keep Azure**: As fallback if ElevenLabs has problems

---

**✅ With these 4 steps, ElevenLabs TTS will be operational with correct audio routing!**
