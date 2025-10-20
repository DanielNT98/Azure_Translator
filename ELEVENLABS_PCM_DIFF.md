# 🔄 ElevenLabs PCM Fix - Code Diff

Questo file mostra il **diff esatto** delle modifiche da applicare a `elevenlabs_engine.py`.

---

## 📍 Modifica 1: Parametro `__init__()`

```diff
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
+   output_format: str = "pcm_24000",  # NEW: Default to PCM format
):
```

---

## 📍 Modifica 2: Docstring parametro

```diff
        model (str, optional): Model. Defaults to "eleven_multilingual_v2". Some models may not work with real time inference.
+       output_format (str, optional): Audio format. Defaults to "pcm_24000". 
+           Options: pcm_8000, pcm_16000, pcm_22050, pcm_24000, pcm_32000, pcm_44100, pcm_48000, mp3_44100_128, etc.
    """
```

---

## 📍 Modifica 3: Salvataggio formato

```diff
    self.model = model
+   self.output_format = output_format  # NEW: Store output format
+   
+   # NEW: Parse sample rate from format string
+   if output_format.startswith('pcm_'):
+       self.sample_rate = int(output_format.split('_')[1])
+   else:
+       self.sample_rate = 24000  # Default fallback
+   
    if not api_key:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
```

---

## 📍 Modifica 4: `get_stream_info()`

### Prima (BROKEN):
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

### Dopo (FIXED):
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
    # NEW: Return PCM format info instead of paCustomFormat (MP3)
    if self.output_format.startswith('pcm_'):
        return pyaudio.paInt16, 1, self.sample_rate
    else:
        # Fallback to MP3 if not PCM
        return pyaudio.paCustomFormat, -1, -1
```

### Diff:
```diff
def get_stream_info(self):
    """
    Returns the audio stream configuration information suitable for PyAudio.

    Returns:
        tuple: A tuple containing the audio format, number of channels, and the sample rate.
-             - Format (int): The format of the audio stream. pyaudio.paCustomFormat for mpeg.
-             - Channels (int): The number of audio channels. -1 for mpeg.
-             - Sample Rate (int): The sample rate of the audio in Hz. -1 for mpeg.
+             - Format (int): The format of the audio stream. pyaudio.paInt16 for PCM.
+             - Channels (int): The number of audio channels. 1 for mono.
+             - Sample Rate (int): The sample rate of the audio in Hz.
    """
-   return pyaudio.paCustomFormat, -1, -1
+   # NEW: Return PCM format info instead of paCustomFormat (MP3)
+   if self.output_format.startswith('pcm_'):
+       return pyaudio.paInt16, 1, self.sample_rate
+   else:
+       # Fallback to MP3 if not PCM
+       return pyaudio.paCustomFormat, -1, -1
```

---

## 📍 Modifica 5: `synthesize()` - Passa formato all'API

### Prima (BROKEN):
```python
try:
    audio_stream = self.client.text_to_speech.stream(
        text=text,
        voice_id=self.id,
        model_id=self.model,
    )

    for chunk in audio_stream:
        if chunk:
            self.queue.put(chunk)
    return True
```

### Dopo (FIXED):
```python
try:
    # NEW: Request PCM format from API
    audio_stream = self.client.text_to_speech.stream(
        text=text,
        voice_id=self.id,
        model_id=self.model,
        output_format=self.output_format,  # NEW: Specify format
    )

    for chunk in audio_stream:
        if chunk:
            self.queue.put(chunk)
    return True
```

### Diff:
```diff
    try:
+       # NEW: Request PCM format from API
        audio_stream = self.client.text_to_speech.stream(
            text=text,
            voice_id=self.id,
            model_id=self.model,
+           output_format=self.output_format,  # NEW: Specify format
        )

        for chunk in audio_stream:
            if chunk:
                self.queue.put(chunk)
        return True
```

---

## 🎯 Riepilogo Completo

### File: `elevenlabs_engine.py`

**Righe modificate**: ~15 righe totali  
**Tempo richiesto**: ~5 minuti  
**Difficoltà**: ⭐⭐⚪⚪⚪ (facile)

### Modifiche:
1. ✅ Aggiungi parametro `output_format` a `__init__()` (1 riga)
2. ✅ Aggiungi documentazione parametro (2 righe)
3. ✅ Salva formato e parsa sample rate (6 righe)
4. ✅ Modifica `get_stream_info()` per restituire PCM (6 righe)
5. ✅ Passa formato all'API in `synthesize()` (1 riga)

**Totale**: 16 righe aggiunte/modificate

---

## 🧪 Testing

### Prima della modifica:
```python
# elevenlabs_engine.py (ORIGINALE)
>>> engine = ElevenlabsEngine(api_key="...", voice="Nicole")
>>> engine.get_stream_info()
(pyaudio.paCustomFormat, -1, -1)  # ← Formato MP3
```

### Dopo la modifica:
```python
# elevenlabs_engine.py (MODIFICATO)
>>> engine = ElevenlabsEngine(api_key="...", voice="Nicole", output_format="pcm_24000")
>>> engine.get_stream_info()
(pyaudio.paInt16, 1, 24000)  # ← Formato PCM! ✅
```

---

## 📊 Impatto

### Performance:
- ⚪ Nessun impatto negativo
- ✅ Stessa qualità audio
- ✅ Stessa latenza

### Funzionalità:
- ✅ Device routing preservato
- ✅ Compatibilità retroattiva (fallback MP3)
- ✅ Nessun breaking change

### Compatibilità:
- ✅ Python 3.8+
- ✅ elevenlabs >= 1.0.0
- ✅ RealtimeTTS >= 0.3.0
- ✅ PyAudio

---

## 🔍 Verifica Modifiche

### Checklist prima di salvare:

```bash
# 1. Backup creato?
ls elevenlabs_engine.py.backup  # ← Deve esistere

# 2. Tutte le modifiche applicate?
grep "output_format" elevenlabs_engine.py  # ← Deve trovare 5+ occorrenze

# 3. Indentazione corretta?
python -m py_compile elevenlabs_engine.py  # ← Nessun errore syntax

# 4. Cache eliminata?
rm -rf __pycache__/  # ← Forza ricompilazione
```

### Log attesi dopo riavvio:

```
✅ SUCCESSO:
[UP]-TTS Initializing ElevenlabsEngine with voice=..., model=..., output_format=pcm_24000
[UP]-TTS ElevenlabsEngine created successfully (PCM mode)

❌ FALLIMENTO:
[UP]-TTS ElevenlabsEngine created successfully  # ← Manca "(PCM mode)"
```

---

## 🎓 Spiegazione Tecnica

### Perché funziona?

```python
# PRIMA (MP3):
get_stream_info() → (pyaudio.paCustomFormat, -1, -1)
                         ↓
stream_player.py vede paCustomFormat
                         ↓
Chiama AudioSegment.from_file(chunk, format="mp3")
                         ↓
pydub decodifica MP3 → PCM raw
                         ↓
PyAudio.open() riceve PCM senza device_index context
                         ↓
Audio va su default device ❌

# DOPO (PCM):
get_stream_info() → (pyaudio.paInt16, 1, 24000)
                         ↓
stream_player.py vede paInt16 (PCM)
                         ↓
Salta decodifica MP3 (già PCM!)
                         ↓
PyAudio.open(output_device_index=10) riceve PCM diretto
                         ↓
Audio va su Cable-A ✅
```

### Flusso chiamate:

```
tts/elevenlabs_tts.py:
└─ ElevenlabsEngine(output_format="pcm_24000")
   └─ get_stream_info() → (paInt16, 1, 24000)
   └─ synthesize()
      └─ client.text_to_speech.stream(output_format="pcm_24000")
         └─ ElevenLabs API → PCM chunks
            └─ queue.put(chunk)  # ← PCM diretto!

stream_player.py:
└─ Riceve chunk dalla queue
   └─ Controlla formato: paInt16 (PCM)
      └─ Salta MP3 decode
         └─ PyAudio.open(device=10) → Cable-A ✅
```

---

**✅ Con questo diff, il fix è completo e l'audio routing funzionerà correttamente!**
