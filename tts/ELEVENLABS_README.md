# ElevenLabs TTS Integration (EXPERIMENTAL)

Experimental module for ElevenLabs TTS integration into the real-time translation system.

## 📋 Requirements

```bash
pip install elevenlabs pydub
```

**Note**: `pydub` is required to convert ElevenLabs MP3 audio to PCM16 for PyAudio.

## 🔑 Configuration

### 1. Get API Key

1. Register on [ElevenLabs](https://elevenlabs.io)
2. Go to [API Keys](https://elevenlabs.io/app/settings/api-keys)
3. Copy your API key

### 2. Configure .env File

Add to your `.env`:

```bash
# Enable ElevenLabs TTS
USE_ELEVENLABS_TTS=1

# Insert your API key
ELEVENLABS_API_KEY=sk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Voice ID (optional - default: Rachel)
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM

# Model (optional - default: eleven_turbo_v2_5)
ELEVENLABS_MODEL=eleven_turbo_v2_5
```

## 🎙️ Available Voices

### Popular Default Voices

| Voice ID | Name | Gender | Characteristics |
|----------|------|--------|-----------------|
| `21m00Tcm4TlvDq8ikWAM` | **Rachel** | F | Clear, professional, neutral |
| `EXAVITQu4vr4xnSDxMaL` | **Bella** | F | Sweet, friendly |
| `IKne3meq5aSn9XLyUdCD` | **Charlie** | M | Casual, young |
| `pNInz6obpgDQGcFmaJgB` | **Adam** | M | Deep, narrative |
| `ErXwobaYiN019PkySvjV` | **Antoni** | M | British, sophisticated |
| `VR6AewLTigWG4xSOukaG` | **Arnold** | M | Energetic, intense |

### How to Get Other Voice IDs

1. Go to [ElevenLabs Voice Library](https://elevenlabs.io/app/voice-lab)
2. Listen to available voices
3. Copy the Voice ID from the voice page

## 🚀 Usage

### Standard Mode (Azure TTS)

```bash
# In .env
USE_ELEVENLABS_TTS=0
USE_REALTIME_TTS=1
```

### Experimental Mode (ElevenLabs TTS)

```bash
# In .env
USE_ELEVENLABS_TTS=1
```

The system will automatically use ElevenLabs instead of Azure.

## ⚙️ Available Models

### `eleven_turbo_v2_5` (RECOMMENDED for real-time)
- ✅ Ultra-low latency (~300ms)
- ✅ Optimized for streaming
- ✅ Supports multilingual
- ⚡ **Best for real-time translation**

### `eleven_multilingual_v2`
- ✅ Superior audio quality
- ✅ Excellent multilingual support
- ⚠️ Slightly higher latency

### `eleven_monolingual_v1`
- ✅ English only
- ✅ Very high quality
- ⚠️ Not suitable for other languages

## 📊 Azure vs ElevenLabs Comparison

| Feature | Azure Neural TTS | ElevenLabs Turbo |
|----------------|------------------|------------------|
| **Latency** | ~500-800ms | ~300-500ms |
| **Voice quality** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ |
| **Naturalness** | Good | Excellent |
| **Languages** | 75+ | 29 |
| **Cost** | Pay-per-use | Subscription |
| **Streaming** | Yes | Yes |

## 🔧 Customizable Parameters

You can modify parameters in `tts/elevenlabs_tts.py`:

```python
# RealtimeTTS Tuning (lines 192-198)
self.buffer_threshold = 0.1  # Buffer before starting (seconds)
self.context_size = 20       # Context words
self.force_first_fragment_after_words = 8  # Words before flush
self.pre_buffer_ms = 20      # Pre-buffering (milliseconds)
self.lookahead_cycles = 5    # Lookahead cycles
```

## 💰 Costs

ElevenLabs pricing (as of 2024):

- **Free Tier**: 10,000 characters/month
- **Starter**: $5/month - 30,000 characters/month
- **Creator**: $22/month - 100,000 characters/month
- **Pro**: $99/month - 500,000 characters/month

**Usage estimate**: ~1000 words of conversation = ~5000 characters

## ⚠️ Limitations

1. **Rate Limits**: Free tier has requests per second limits
2. **Multilingual**: Not all voices support all languages
3. **Experimental**: This integration is in testing phase
4. **Variable latency**: Depends on ElevenLabs server load

## 🐛 Troubleshooting

### Error: "Missing ElevenLabs API key"

```bash
# Verify it's set in .env
ELEVENLABS_API_KEY=sk_your_actual_key_here
```

### Error: "Rate limit exceeded"

- Wait a few seconds and retry
- Consider upgrading to a paid plan

### Voice not working

- Verify the Voice ID is correct
- Check that the voice supports the target language
- Try with Rachel (default): `21m00Tcm4TlvDq8ikWAM`

### Distorted or choppy audio

```python
# Increase buffer in elevenlabs_tts.py
self.buffer_threshold = 0.2  # From 0.1 to 0.2
self.pre_buffer_ms = 50      # From 20 to 50
```

## 📝 Development Notes

### Architecture

```
DeepLTranslator
    ├─> ElevenLabsTTS (if USE_ELEVENLABS_TTS=1)
    │   ├─> ElevenLabsEngine (custom engine)
    │   │   └─> ElevenLabs API (streaming)
    │   └─> RealtimeTTS (framework)
    │       └─> TextToAudioStream
    │
    └─> RealtimeAzureTTS (if USE_ELEVENLABS_TTS=0)
        └─> Azure Neural TTS
```

### Implementation Differences

**ElevenLabs**:
- MP3 stream (compressed format)
- Native streaming API
- Variable chunks
- Superior audio quality

**Azure**:
- PCM16 stream (raw format)
- Streaming via SSML
- Fixed chunks
- Native integration with Azure ecosystem

## 🎯 Best Practices

1. **Testing**: Always test with free tier before subscribing
2. **Monitoring**: Check character usage in ElevenLabs dashboard
3. **Fallback**: Keep Azure configured as backup
4. **Voice Selection**: Test different voices to find the best for your use case
5. **Model Selection**: Use `eleven_turbo_v2_5` for real-time, `eleven_multilingual_v2` for quality

## 📚 Resources

- [ElevenLabs Documentation](https://elevenlabs.io/docs)
- [ElevenLabs API Reference](https://elevenlabs.io/docs/api-reference)
- [Voice Library](https://elevenlabs.io/voice-library)
- [Pricing](https://elevenlabs.io/pricing)

## 🔄 Return to Azure

To disable ElevenLabs and return to Azure:

```bash
# In .env
USE_ELEVENLABS_TTS=0
USE_REALTIME_TTS=1
```

Restart the application.
