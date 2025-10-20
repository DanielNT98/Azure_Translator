# 🔧 ElevenLabs PCM Format Fix - Guida Dettagliata

## 🎯 Problema

Quando si usa ElevenLabs TTS con **virtual audio cables** (VB-Cable), l'audio **non viene instradato correttamente** al dispositivo specificato (es. Cable-A Input), ma viene riprodotto sul dispositivo di default del sistema.

### Causa Tecnica

```
❌ FLUSSO MP3 (DEFAULT - NON FUNZIONA):
┌─────────────────┐     ┌──────────┐     ┌─────────────┐     ┌─────────────┐
│ ElevenLabs API  │ --> │ MP3 data │ --> │ pydub decode│ --> │ PyAudio     │
│                 │     │ (stream) │     │ (perde ctx) │     │ (default!)  │
└─────────────────┘     └──────────┘     └─────────────┘     └─────────────┘
                                                ↑
                                          Device routing
                                          PERSO QUI! ❌

✅ FLUSSO PCM (FIX - FUNZIONA):
┌─────────────────┐     ┌──────────┐     ┌─────────────┐
│ ElevenLabs API  │ --> │ PCM data │ --> │ PyAudio     │
│ output_format=  │     │ (stream) │     │ (device=10) │
│ "pcm_24000"     │     │          │     │ Cable-A ✅  │
└─────────────────┘     └──────────┘     └─────────────┘
```

**Spiegazione**:
- **Azure TTS**: Restituisce direttamente PCM/WAV (`riff-16khz-16bit-mono-pcm`) → Device routing preservato ✅
- **ElevenLabs (default)**: Restituisce MP3 → Necessita decodifica → Device routing perso durante decode ❌
- **ElevenLabs (PCM)**: Restituisce PCM → Nessuna decodifica → Device routing preservato ✅

---

## 🛠️ Soluzione: Modifica RealtimeTTS

### Passo 1: Localizza il File

**Path completo** (Windows, Python 3.11):
```
C:\Users\[NOME_UTENTE]\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\LocalCache\local-packages\Python311\site-packages\RealtimeTTS\engines\elevenlabs_engine.py
```

**Trova il tuo path**:
```bash
python -c "import RealtimeTTS; import os; print(os.path.dirname(RealtimeTTS.__file__))"
```

### Passo 2: Backup

```bash
# PowerShell
Copy-Item "PATH\elevenlabs_engine.py" "PATH\elevenlabs_engine.py.backup"
```

### Passo 3: Modifiche al Codice

#### Modifica 1: Aggiungi parametro `output_format` a `__init__()`

**CERCA** (circa riga 24):
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

**SOSTITUISCI CON**:
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
    output_format: str = "pcm_24000",  # ← AGGIUNGI QUESTO
):
```

#### Modifica 2: Aggiungi documentazione parametro

**CERCA** (dentro la docstring):
```python
    model (str, optional): Model. Defaults to "eleven_multilingual_v2". Some models may not work with real time inference.
```

**AGGIUNGI SOTTO**:
```python
    output_format (str, optional): Audio format. Defaults to "pcm_24000". Options: pcm_8000, pcm_16000, pcm_22050, pcm_24000, pcm_32000, pcm_44100, pcm_48000, mp3_44100_128, etc.
```

#### Modifica 3: Salva e parsa il formato

**CERCA** (circa riga 58):
```python
    self.model = model
    if not api_key:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
```

**SOSTITUISCI CON**:
```python
    self.model = model
    self.output_format = output_format  # ← AGGIUNGI
    
    # Parse sample rate from format string
    if output_format.startswith('pcm_'):
        self.sample_rate = int(output_format.split('_')[1])
    else:
        self.sample_rate = 24000  # Default fallback
    
    if not api_key:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
```

#### Modifica 4: Aggiorna `get_stream_info()`

**CERCA** (circa riga 82):
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

**SOSTITUISCI CON**:
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

#### Modifica 5: Passa formato all'API

**CERCA** (circa riga 104):
```python
    audio_stream = self.client.text_to_speech.stream(
        text=text,
        voice_id=self.id,
        model_id=self.model,
    )
```

**SOSTITUISCI CON**:
```python
    audio_stream = self.client.text_to_speech.stream(
        text=text,
        voice_id=self.id,
        model_id=self.model,
        output_format=self.output_format,  # ← AGGIUNGI QUESTO
    )
```

---

## ✅ Verifica

### Test 1: Controlla i Logs

Dopo aver avviato `main_deepl.py`, dovresti vedere:

```
[UP]-TTS Initializing ElevenlabsEngine with voice=Antonio Farina, model=eleven_flash_v2_5, output_format=pcm_24000
[UP]-TTS ElevenlabsEngine created successfully (PCM mode)
```

**Se vedi solo**:
```
[UP]-TTS ElevenlabsEngine created successfully  # ← Manca "(PCM mode)"
```
La modifica **non è stata applicata correttamente**.

### Test 2: Audio Routing

1. Avvia il sistema: `python main_deepl.py`
2. Parla nel microfono
3. Verifica che l'audio tradotto **esca su Cable-A** (non dagli speaker/cuffie)

### Test 3: Verifica Device Index

Nei logs, cerca:
```
[UP]-TTS Creating TextToAudioStream (PyAudio device=10)
```

Device 10 dovrebbe essere Cable-A Input (o il tuo virtual cable configurato).

---

## 📋 Formati PCM Supportati

ElevenLabs supporta questi formati PCM:

| Formato | Sample Rate | Uso Consigliato |
|---------|-------------|------------------|
| `pcm_8000` | 8 kHz | Telefonia (bassa qualità) |
| `pcm_16000` | 16 kHz | Voice over IP, buona latenza |
| `pcm_22050` | 22.05 kHz | Qualità standard |
| **`pcm_24000`** | **24 kHz** | **✅ RACCOMANDATO** (balance perfetto) |
| `pcm_32000` | 32 kHz | Alta qualità |
| `pcm_44100` | 44.1 kHz | CD quality (latenza più alta) |
| `pcm_48000` | 48 kHz | Professional audio (latenza alta) |

**Raccomandazione**: Usa `pcm_24000` per il miglior compromesso tra qualità audio e latenza.

---

## 🔄 Confronto: Prima e Dopo

### Prima (MP3)
```python
# elevenlabs_engine.py (ORIGINALE)
def get_stream_info(self):
    return pyaudio.paCustomFormat, -1, -1  # ← Segnala formato MP3

audio_stream = self.client.text_to_speech.stream(
    text=text,
    voice_id=self.id,
    model_id=self.model,
    # output_format non specificato → default MP3
)
```

**Risultato**: Audio va su default device ❌

### Dopo (PCM)
```python
# elevenlabs_engine.py (MODIFICATO)
def get_stream_info(self):
    if self.output_format.startswith('pcm_'):
        return pyaudio.paInt16, 1, self.sample_rate  # ← Segnala PCM
    else:
        return pyaudio.paCustomFormat, -1, -1

audio_stream = self.client.text_to_speech.stream(
    text=text,
    voice_id=self.id,
    model_id=self.model,
    output_format=self.output_format,  # ← Richiede PCM esplicitamente
)
```

**Risultato**: Audio va su Cable-A (device specificato) ✅

---

## 🆘 Troubleshooting

### Problema: Modifica non ha effetto

**Causa**: Python potrebbe usare file `.pyc` compilati

**Soluzione**:
```bash
# Elimina cache
Remove-Item -Recurse "C:\Users\[USER]\AppData\Local\Packages\...\RealtimeTTS\__pycache__"

# Riavvia sistema
python main_deepl.py
```

### Problema: Errore `unexpected keyword argument 'output_format'`

**Causa**: Versione vecchia di `elevenlabs` SDK

**Soluzione**:
```bash
pip install --upgrade elevenlabs
```

Versione richiesta: `elevenlabs >= 1.0.0`

### Problema: Audio distorto con PCM

**Causa**: Sample rate mismatch

**Soluzione**: Prova un formato diverso:
```python
# In tts/elevenlabs_tts.py
self.engine = ElevenlabsEngine(
    voice=resolved_voice,
    model=model,
    output_format="pcm_16000"  # ← Prova 16kHz
)
```

---

## 📚 Riferimenti

- **ElevenLabs API Docs**: https://elevenlabs.io/docs/api-reference/text-to-speech
- **Formati Audio Supportati**: https://elevenlabs.io/docs/api-reference/streaming
- **RealtimeTTS**: https://github.com/KoljaB/RealtimeTTS
- **PyAudio Device Selection**: https://people.csail.mit.edu/hubert/pyaudio/docs/

---

## 💡 Best Practices

1. **Backup sempre** prima di modificare pacchetti installati
2. **Testa incrementalmente**: Applica una modifica alla volta
3. **Verifica logs** per confermare che PCM mode sia attivo
4. **Usa `pcm_24000`** come default (best balance)
5. **Mantieni Azure TTS** configurato come fallback
6. **Documenta modifiche** per future installazioni

---

## 📝 Checklist Completa

- [ ] Backup di `elevenlabs_engine.py` creato
- [ ] Parametro `output_format` aggiunto a `__init__()`
- [ ] `self.output_format` e `self.sample_rate` salvati
- [ ] `get_stream_info()` modificato per restituire PCM
- [ ] `output_format` passato a `client.text_to_speech.stream()`
- [ ] Cache Python eliminata
- [ ] Sistema riavviato
- [ ] Logs verificano `(PCM mode)`
- [ ] Audio routing testato su Cable-A
- [ ] Qualità audio verificata

---

**✅ Se tutti i punti sono spuntati, il fix PCM è completato con successo!**
