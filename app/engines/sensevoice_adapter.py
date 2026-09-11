from sensevoice_processor import transcribe_audio, load_model


class SenseVoiceAdapter:
    def transcribe(self, audio_path: str, model_path: str, *, language: str = "auto", on_progress=None):
        return transcribe_audio(audio_path, model_path, language=language, on_progress=on_progress)

    def load_model(self, model_dir: str):
        return load_model(model_dir)
