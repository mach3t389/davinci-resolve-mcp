r"""Transcribe SOURCE files once and store the words next to the rushes.

Run with the WhisperX environment (CPU by default), not the Resolve one:
  "D:\Vibe coding\whisperx\.venv\Scripts\python.exe" -m montage_engine.transcribe_source <store folder> <media> [<media> ...]
Options: --language fr  --model large-v3-turbo  --no-align  --device auto|cuda|cpu  --hint "mots difficiles, noms propres"
Sources that already have a matching transcript are skipped (a source is transcribed ONCE, never after an edit).
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from montage_engine.anchors import TranscriptStore  # noqa: E402


def extract_audio(media, wav):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(media), '-vn', '-ac', '1', '-ar', '16000',
                    '-c:a', 'pcm_s16le', str(wav)], check=True)


_MODELS = {}
DEVICE = 'auto'   # 'auto' (GPU when it works, else CPU) | 'cuda' | 'cpu'; set from --device


def add_cuda_libraries():
    """Make the NVIDIA wheels (nvidia-cublas-cu12, nvidia-cudnn-cu12 in this environment) loadable by CTranslate2 on
    Windows. Returns the folders added; nothing happens when the wheels are not installed."""
    import site
    added = []
    for base in site.getsitepackages():
        for lib in ('cublas', 'cudnn', 'cuda_nvrtc'):
            d = Path(base) / 'nvidia' / lib / 'bin'
            if d.is_dir() and hasattr(os, 'add_dll_directory'):
                os.add_dll_directory(str(d))
                os.environ['PATH'] = str(d) + os.pathsep + os.environ.get('PATH', '')
                added.append(str(d))
    return added


def _load_whisper(name, device):
    from faster_whisper import WhisperModel
    if device == 'cpu':
        return WhisperModel(name, device='cpu', compute_type='int8'), 'cpu'
    add_cuda_libraries()
    try:
        # int8_float16 keeps large-v3-turbo within 4 GB of video memory
        model = WhisperModel(name, device='cuda', compute_type='int8_float16')
        # The missing-library errors only show up at the first real inference: force it here, once.
        import numpy as np
        list(model.transcribe(np.zeros(16000, dtype='float32'), language='fr')[0])
        return model, 'cuda'
    except Exception as exc:
        if device == 'cuda':
            raise
        print(f'GPU unavailable ({str(exc)[:120]}); using the CPU', file=sys.stderr)
        return WhisperModel(name, device='cpu', compute_type='int8'), 'cpu'


def _model(name):
    """Loaded once per run: reloading the model for every rush would cost more than short files take."""
    if name not in _MODELS:
        _MODELS[name], used = _load_whisper(name, DEVICE)
        print('speech model on', used, file=sys.stderr)
    return _MODELS[name]


def _align_model(language):
    key = ('align', language)
    if key not in _MODELS:
        import whisperx
        _MODELS[key] = whisperx.load_align_model(language_code=language, device='cpu')
    return _MODELS[key]


def transcribe(media, language='fr', model_name='large-v3-turbo', align=True, hint=None):
    import whisperx
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / 'a.wav'
        extract_audio(media, wav)
        audio = whisperx.load_audio(str(wav))
        model = _model(model_name)
        segs, _ = model.transcribe(audio, language=language, word_timestamps=True, initial_prompt=hint,
                                   vad_filter=True)
        segs = list(segs)
        words = [dict(w=w.word.strip(), s=w.start, e=w.end) for x in segs for w in (x.words or []) if w.word.strip()]
        aligned = False
        if align and segs:
            try:
                am, meta = _align_model(language)
                res = whisperx.align([dict(text=x.text, start=x.start, end=x.end) for x in segs], am, meta, audio,
                                     'cpu', return_char_alignments=False)
                aw = [dict(w=w['word'], s=w['start'], e=w['end']) for s in res['segments'] for w in s.get('words', [])
                      if 'start' in w and 'end' in w]
                if aw:
                    words, aligned = aw, True
            except Exception as exc:   # alignment is an improvement, never a blocker
                print('alignment skipped:', exc, file=sys.stderr)
        return words, aligned


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('store')
    ap.add_argument('media', nargs='+')
    ap.add_argument('--language', default='fr')
    ap.add_argument('--model', default='large-v3-turbo')
    ap.add_argument('--no-align', action='store_true')
    ap.add_argument('--hint')
    ap.add_argument('--device', choices=['auto', 'cuda', 'cpu'], default='auto')
    a = ap.parse_args(argv)
    global DEVICE
    DEVICE = a.device
    store = TranscriptStore(a.store)
    for m in a.media:
        if store.load(m):
            print('skip (already transcribed):', os.path.basename(m))
            continue
        words, aligned = transcribe(m, a.language, a.model, not a.no_align, a.hint)
        store.save(m, words, language=a.language, model=a.model, aligned=aligned)
        print('transcribed', os.path.basename(m), len(words), 'words', 'aligned' if aligned else 'whisper timing')


if __name__ == '__main__':
    main()
