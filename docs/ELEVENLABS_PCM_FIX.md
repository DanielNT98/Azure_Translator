# 🔧 ElevenLabs PCM Format Fix - Detailed Guide

## 🎯 Problem

When using ElevenLabs TTS with **virtual audio cables** (VB-Cable), audio **is not routed correctly** to the specified device (e.g. Cable-A Input), but plays on the system's default device instead.

### Technical Cause

```
❌ MP3 FLOW (DEFAULT - DOESN'T WORK):
┌─────────────────┐     ┌──────────┐     ┌─────────────┐     ┌─────────────┐
│ ElevenLabs API  │ --> │ MP3 data │ --> │ pydub decode│ --> │ PyAudio     │
│                 │     │ (stream) │     │ (loses ctx) │     │ (default!)  │
└─────────────────┘     └──────────┘     └─────────────┘     └─────────────┘
                                                ↑
                                          Device routing
                                          LOST HERE! ❌

✅ PCM FLOW (FIX - WORKS):
┌─────────────────┐     ┌──────────┐     ┌─────────────┐
│ ElevenLabs API  │ --> │ PCM data │ --> │ PyAudio     │
│ output_format=  │     │ (stream) │     │ (device=10) │
│ "pcm_24000"     │     │          │     │ Cable-A ✅  │
└─────────────────┘     └──────────┘     └─────────────┘
```

**Explanation**:
- **Azure TTS**: Returns direct PCM/WAV (`riff-16khz-16bit-mono-pcm`) → Device routing preserved ✅
- **ElevenLabs (default)**: Returns MP3 → Requires decoding → Device routing lost during decode ❌
- **ElevenLabs (PCM)**: Returns PCM → No decoding → Device routing preserved ✅

---

## 🛠️ Solution: Modify RealtimeTTS

### Step 1: Locate the File

**Full path** (Windows, Python 3.11):
```
C:\Users\[USERNAME]\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\LocalCache\local-packages\Python311\site-packages\RealtimeTTS\engines\elevenlabs_engine.py
```

**Find your path**:
```bash
python -c "import RealtimeTTS; import os; print(os.path.dirname(RealtimeTTS.__file__))"
```

### Step 2: Backup

```bash
# PowerShell
Copy-Item "PATH\elevenlabs_engine.py" "PATH\elevenlabs_engine.py.backup"
```

### Step 3: Code Modifications

#### Modification 1: Add `output_format` parameter to `__init__()`

**FIND** (around line 24):
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
):
```

**REPLACE WITH**:
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
    output_format: str = "pcm_24000",  # ← ADD THIS
):
```

#### Modification 2: Add parameter documentation

**FIND** (inside the docstring):
```python
    model (str, optional): Model. Defaults to "eleven_multilingual_v2". Some models may not work with real time inference.
```

**ADD BELOW**:
```python
    output_format (str, optional): Audio format. Defaults to "pcm_24000". Options: pcm_8000, pcm_16000, pcm_22050, pcm_24000, pcm_32000, pcm_44100, pcm_48000, mp3_44100_128, etc.
```

#### Modification 3: Store and parse the format

**FIND** (around line 58):
```python
    self.model = model
    if not api_key:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
```

**REPLACE WITH**:
```python
    self.model = model
    self.output_format = output_format  # ← ADD THIS
    
    # Parse sample rate from format string
    if output_format.startswith('pcm_'):
        self.sample_rate = int(output_format.split('_')[1])
    else:
        self.sample_rate = 24000  # Default fallback
    
    if not api_key:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
```

#### Modification 4: Update `get_stream_info()`

**FIND** (around line 82):
```python
def get_stream_info(self):
    """
    Returns the audio stream configuration information suitable for PyAudio.

    Returns:
        tuple: A tuple containing the audio format, number of channels, and the sample rate.
              - Format (int): The format of the audio stream. pyaudio.paCustomFormat for mpeg.
              - Channels (int): The number of audio channels. -1 for mpeg.
              - Sample Rate (int): The sample rate of the audio in Hz. -1 for mpeg.
    """
    return pyaudio.paCustomFormat, -1, -1
```

**REPLACE WITH**:
```python
def get_stream_info(self):
    """
    Returns the audio stream configuration information suitable for PyAudio.

    Returns:
        tuple: A tuple containing the audio format, number of channels, and the sample rate.
              - Format (int): The format of the audio stream. pyaudio.paInt16 for PCM.
              - Channels (int): The number of audio channels. 1 for mono.
              - Sample Rate (int): The sample rate of the audio in Hz.
    """
    # Return PCM format info instead of paCustomFormat (MP3)
    if self.output_format.startswith('pcm_'):
        return pyaudio.paInt16, 1, self.sample_rate
    else:
        # Fallback to MP3 if not PCM
        return pyaudio.paCustomFormat, -1, -1
```

#### Modification 5: Pass format to API

**FIND** (around line 104):
```python
    audio_stream = self.client.text_to_speech.stream(
        text=text,
        voice_id=self.id,
        model_id=self.model,
    )
```

**REPLACE WITH**:
```python
    audio_stream = self.client.text_to_speech.stream(
        text=text,
        voice_id=self.id,
        model_id=self.model,
        output_format=self.output_format,  # ← ADD THIS
    )
```

---

## ✅ Verification

### Test 1: Check Logs

After starting `main_deepl.py`, you should see:

```
[UP]-TTS Initializing ElevenlabsEngine with voice=Antonio Farina, model=eleven_flash_v2_5, output_format=pcm_24000
[UP]-TTS ElevenlabsEngine created successfully (PCM mode)
```

**If you only see**:
```
[UP]-TTS ElevenlabsEngine created successfully  # ← Missing "(PCM mode)"
```
The modification **was not applied correctly**.

### Test 2: Audio Routing

1. Start the system: `python main_deepl.py`
2. Speak into the microphone
3. Verify that translated audio **comes out on Cable-A** (not from speakers/headphones)

### Test 3: Verify Device Index

In the logs, look for:
```
[UP]-TTS Creating TextToAudioStream (PyAudio device=10)
```

Device 10 should be Cable-A Input (or your configured virtual cable).

---

## 📋 Supported PCM Formats

ElevenLabs supports these PCM formats:

| Format | Sample Rate | Recommended Use |
|---------|-------------|------------------|
| `pcm_8000` | 8 kHz | Telephony (low quality) |
| `pcm_16000` | 16 kHz | Voice over IP, good latency |
| `pcm_22050` | 22.05 kHz | Standard quality |
| **`pcm_24000`** | **24 kHz** | **✅ RECOMMENDED** (perfect balance) |
| `pcm_32000` | 32 kHz | High quality |
| `pcm_44100` | 44.1 kHz | CD quality (higher latency) |
| `pcm_48000` | 48 kHz | Professional audio (high latency) |

**Recommendation**: Use `pcm_24000` for the best compromise between audio quality and latency.

---

## 🔄 Comparison: Before and After

### Before (MP3)
```python
# elevenlabs_engine.py (ORIGINAL)
def get_stream_info(self):
    return pyaudio.paCustomFormat, -1, -1  # ← Signals MP3 format

audio_stream = self.client.text_to_speech.stream(
    text=text,
    voice_id=self.id,
    model_id=self.model,
    # output_format not specified → default MP3
)
```

**Result**: Audio goes to default device ❌

### After (PCM)
```python
# elevenlabs_engine.py (MODIFIED)
def get_stream_info(self):
    if self.output_format.startswith('pcm_'):
        return pyaudio.paInt16, 1, self.sample_rate  # ← Signals PCM
    else:
        return pyaudio.paCustomFormat, -1, -1

audio_stream = self.client.text_to_speech.stream(
    text=text,
    voice_id=self.id,
    model_id=self.model,
    output_format=self.output_format,  # ← Explicitly requests PCM
)
```

**Result**: Audio goes to Cable-A (specified device) ✅

---

## 🆘 Troubleshooting

### Problem: Modification has no effect

**Cause**: Python might be using compiled `.pyc` files

**Solution**:
```bash
# Delete cache
Remove-Item -Recurse "C:\Users\[USER]\AppData\Local\Packages\...\RealtimeTTS\__pycache__"

# Restart system
python main_deepl.py
```

### Problem: Error `unexpected keyword argument 'output_format'`

**Cause**: Old version of `elevenlabs` SDK

**Solution**:
```bash
pip install --upgrade elevenlabs
```

Required version: `elevenlabs >= 1.0.0`

### Problem: Distorted audio with PCM

**Cause**: Sample rate mismatch

**Solution**: Try a different format:
```python
# In tts/elevenlabs_tts.py
self.engine = ElevenlabsEngine(
    voice=resolved_voice,
    model=model,
    output_format="pcm_16000"  # ← Try 16kHz
)
```

---

## 📚 References

- **ElevenLabs API Docs**: https://elevenlabs.io/docs/api-reference/text-to-speech
- **Supported Audio Formats**: https://elevenlabs.io/docs/api-reference/streaming
- **RealtimeTTS**: https://github.com/KoljaB/RealtimeTTS
- **PyAudio Device Selection**: https://people.csail.mit.edu/hubert/pyaudio/docs/

---

## 💡 Best Practices

1. **Always backup** before modifying installed packages
2. **Test incrementally**: Apply one modification at a time
3. **Verify logs** to confirm PCM mode is active
4. **Use `pcm_24000`** as default (best balance)
5. **Keep Azure TTS** configured as fallback
6. **Document modifications** for future installations

---

## 📝 Complete Checklist

- [ ] Backup of `elevenlabs_engine.py` created
- [ ] `output_format` parameter added to `__init__()`
- [ ] `self.output_format` and `self.sample_rate` stored
- [ ] `get_stream_info()` modified to return PCM
- [ ] `output_format` passed to `client.text_to_speech.stream()`
- [ ] Python cache deleted
- [ ] System restarted
- [ ] Logs verify `(PCM mode)`
- [ ] Audio routing tested on Cable-A
- [ ] Audio quality verified

---

**✅ If all points are checked, the PCM fix is successfully completed!**
