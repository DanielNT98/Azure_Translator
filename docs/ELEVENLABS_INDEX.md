# 📚 ElevenLabs TTS Documentation - Index

This folder contains all documentation for integrating and configuring ElevenLabs TTS.

---

## 🚀 Get Started

### 1. **Quick Start** (5 minutes)
📄 [`ELEVENLABS_QUICKSTART.md`](./ELEVENLABS_QUICKSTART.md)
- Setup in 4 steps
- Quick configuration
- Verify functionality

**Start here if**: You want to get running immediately without reading too much

---

## 🔧 Fix Audio Routing (CRITICAL)

### 2. **PCM Format Fix** (REQUIRED for VB-Cable)
📄 [`ELEVENLABS_PCM_FIX.md`](./ELEVENLABS_PCM_FIX.md)
- Detailed step-by-step guide
- Technical explanation of the problem
- Complete troubleshooting
- Verification checklist

**Read ABSOLUTELY if**: You use virtual audio cables (Cable-A/B) and audio is not routing correctly

### 3. **Code Diff** (For developers)
📄 [`ELEVENLABS_PCM_DIFF.md`](./ELEVENLABS_PCM_DIFF.md)
- Exact modification diff
- Before/After code
- Technical flow explanation
- Testing verification

**Read if**: You want to understand exactly what code to modify

---

## 📖 Complete Documentation

### 4. **Complete Setup**
📄 [`ELEVENLABS_SETUP.md`](./ELEVENLABS_SETUP.md)
- Dependencies installation
- API key configuration
- Voice selection
- Advanced configuration
- Azure vs ElevenLabs comparison
- Complete troubleshooting

**Read if**: You want to understand all integration details

### 5. **System README**
📄 [`../README.md`](../README.md)
- Complete system overview
- DeepL vs Azure modes
- Modular architecture
- Performance optimizations
- ElevenLabs quick reference

**Read if**: You want to understand the system as a whole

---

## 🧪 Testing

### 6. **Test Script**
📄 [`../test_elevenlabs.py`](../test_elevenlabs.py)
- Isolated integration test
- List available voices
- Verify configuration

**Use when**: You want to test only ElevenLabs without starting the full system

---

## 📊 Documentation Flowchart

```
START
  ↓
Want quick setup?
  ├─ YES → ELEVENLABS_QUICKSTART.md (5 min)
  │         ↓
  │    Audio NOT working on Cable-A?
  │         ├─ YES → ELEVENLABS_PCM_FIX.md (CRITICAL)
  │         └─ NO → ✅ COMPLETE!
  │
  └─ NO → Want to understand everything in detail?
            ├─ YES → ELEVENLABS_SETUP.md (complete)
            │         ↓
            │    Need to modify code?
            │         └─ YES → ELEVENLABS_PCM_DIFF.md (diff)
            │
            └─ NO → ../README.md (system overview)
```

---

## ❓ Quick FAQ

### Q: I installed ElevenLabs but audio goes to speakers, not to Cable-A
**A**: You need to apply the PCM format fix. Read `ELEVENLABS_PCM_FIX.md`

### Q: I want to test ElevenLabs without modifying the system
**A**: Use `test_elevenlabs.py`

### Q: How do I choose a different voice?
**A**: See "Advanced Configuration" section in `ELEVENLABS_SETUP.md`

### Q: Why does Azure work but ElevenLabs doesn't?
**A**: Azure uses PCM, ElevenLabs uses MP3. See explanation in `ELEVENLABS_PCM_FIX.md`

### Q: Do I need to modify the system code?
**A**: NO, only the `elevenlabs_engine.py` file in the RealtimeTTS package (installed package)

---

## 🎯 Common Paths

### Path 1: Standard Setup (VB-Cable)
1. `ELEVENLABS_QUICKSTART.md` → Base setup (5 min)
2. `ELEVENLABS_PCM_FIX.md` → Fix audio routing (10 min)
3. `test_elevenlabs.py` → Test (2 min)
4. ✅ Operational!

**Total time**: ~20 minutes

### Path 2: Simple Setup (Without VB-Cable)
1. `ELEVENLABS_QUICKSTART.md` → Base setup (5 min)
2. Skip PCM fix (audio goes to default device, ok for testing)
3. `test_elevenlabs.py` → Test (2 min)
4. ✅ Operational!

**Total time**: ~7 minutes

### Path 3: Complete Study
1. `../README.md` → Understand system (15 min)
2. `ELEVENLABS_SETUP.md` → Integration details (20 min)
3. `ELEVENLABS_PCM_FIX.md` → Understand audio problem (10 min)
4. `ELEVENLABS_PCM_DIFF.md` → Analyze modifications (10 min)
5. Implementation and testing (15 min)

**Total time**: ~70 minutes

---

## 📝 Files Modified in System

During setup, you will modify:

### External File (Installed Package)
- ✏️ `RealtimeTTS/engines/elevenlabs_engine.py` - PCM format fix

### System Files (Already present)
- `.env` - Add API key and configuration
- No other code files require modifications!

---

## 🔄 Versioning

| Version | Date | Changes |
|----------|------|-----------|
| v3.0 | 2024-10-20 | ElevenLabs integration + PCM fix |
| v2.0 | 2024-10-XX | Optimizations + RealtimeTTS |
| v1.0 | 2024-XX-XX | Dual-mode DeepL/Azure system |

---

## 🆘 Support

If you have problems:

1. **Check troubleshooting**: Each doc has dedicated section
2. **Verify logs**: System generates detailed logs in `logs/`
3. **Isolated test**: Use `test_elevenlabs.py` to isolate problem
4. **Consult diff**: `ELEVENLABS_PCM_DIFF.md` to verify modifications

---

## 📌 Useful Links

- [ElevenLabs API Docs](https://elevenlabs.io/docs/api-reference/text-to-speech)
- [RealtimeTTS GitHub](https://github.com/KoljaB/RealtimeTTS)
- [ElevenLabs Voices](https://elevenlabs.io/voice-library)

---

**✅ Follow the appropriate path and you'll be operational in minutes!**
