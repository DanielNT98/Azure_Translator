from typing import Tuple
import azure.cognitiveservices.speech as speechsdk


def create_upstream_clients(cfg) -> Tuple[speechsdk.translation.TranslationRecognizer, speechsdk.SpeechSynthesizer]:
    trans_config_up = speechsdk.translation.SpeechTranslationConfig(
        subscription=cfg.SPEECH_KEY, region=cfg.SPEECH_REGION
    )
    trans_config_up.speech_recognition_language = cfg.SRC_LANG
    trans_config_up.add_target_language(cfg.TGT_LANG)

    audio_input_up = speechsdk.audio.AudioConfig(use_default_microphone=True)
    recognizer_up = speechsdk.translation.TranslationRecognizer(
        translation_config=trans_config_up, audio_config=audio_input_up
    )

    speech_cfg_up = speechsdk.SpeechConfig(subscription=cfg.SPEECH_KEY, region=cfg.SPEECH_REGION)
    speech_cfg_up.speech_synthesis_voice_name = cfg.VOICE_NAME
    speech_cfg_up.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
    )
    synth_up = speechsdk.SpeechSynthesizer(speech_config=speech_cfg_up, audio_config=None)

    return recognizer_up, synth_up


def create_downstream_clients(cfg):
    format_16k = speechsdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
    push_stream_down = speechsdk.audio.PushAudioInputStream(format_16k)
    audio_input_down = speechsdk.audio.AudioConfig(stream=push_stream_down)

    trans_config_down = speechsdk.translation.SpeechTranslationConfig(
        subscription=cfg.SPEECH_KEY, region=cfg.SPEECH_REGION
    )
    trans_config_down.speech_recognition_language = cfg.DOWN_SRC_LANG
    trans_config_down.add_target_language(cfg.DOWN_TGT_LANG)

    recognizer_down = speechsdk.translation.TranslationRecognizer(
        translation_config=trans_config_down, audio_config=audio_input_down
    )

    speech_cfg_down = speechsdk.SpeechConfig(subscription=cfg.SPEECH_KEY, region=cfg.SPEECH_REGION)
    speech_cfg_down.speech_synthesis_voice_name = cfg.DOWN_VOICE_NAME
    speech_cfg_down.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
    )
    synth_down = speechsdk.SpeechSynthesizer(speech_config=speech_cfg_down, audio_config=None)

    return recognizer_down, synth_down, push_stream_down
