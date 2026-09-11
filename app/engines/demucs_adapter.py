from vocal_processor import separate_vocals


class DemucsAdapter:
    """Vocal/instrumental separation using ONNX Runtime (UVR MDX-NET model)."""

    def separate(self, audio_path: str, output_dir: str, on_progress=None):
        return separate_vocals(audio_path, output_dir, on_progress=on_progress)
