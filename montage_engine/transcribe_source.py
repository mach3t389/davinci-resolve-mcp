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


def complete_words(aligned, seg_start, seg_end):
    """Words of one ALIGNED segment as [{'w','s','e'}]. The aligner leaves some words without times (numbers such as
    "2026", symbols): they are kept, timed between their neighbours, instead of being dropped."""
    out = []
    for k, w in enumerate(aligned):
        text = (w.get('word') or '').strip()
        if not text:
            continue
        out.append(dict(w=text, s=w.get('start'), e=w.get('end')))
    for k, w in enumerate(out):
        if w['s'] is None or w['e'] is None:
            prev_end = next((out[j]['e'] for j in range(k - 1, -1, -1) if out[j]['e'] is not None), seg_start)
            nxt_start = next((out[j]['s'] for j in range(k + 1, len(out)) if out[j]['s'] is not None), seg_end)
            w['s'] = prev_end if w['s'] is None else w['s']
            w['e'] = max(nxt_start, w['s'] + 0.05) if w['e'] is None else w['e']
    return out


def _key(text):
    import re
    return re.sub(r'[^\w]', '', text.lower())


def join_apostrophes(words):
    """Whisper splits "J'ai" into "J" and "'ai" (and "m'est" into "m", "'est"); the aligner keeps them together.
    Glue a token that starts with an apostrophe to the previous one so both sides speak the same words."""
    out = []
    for w in words:
        if out and w['w'][:1] in ("'", '\u2019'):
            out[-1] = dict(out[-1], w=out[-1]['w'] + w['w'], e=max(out[-1]['e'], w['e']))
        else:
            out.append(dict(w))
    return out


def reconcile(whisper_words, aligned_words):
    """Whisper's words, ALWAYS all of them, with the aligner's times where it placed the same word and Whisper's own
    times where it did not (hyphenated words, numbers, symbols are often dropped or changed by the aligner). The two
    lists are matched over the whole file, not segment by segment: the aligner cuts long segments into sentences, so
    segment counts differ. -> (words, number of words kept with Whisper's own times)."""
    import difflib
    sm = difflib.SequenceMatcher(a=[_key(w['w']) for w in whisper_words], b=[_key(w['w']) for w in aligned_words],
                                 autojunk=False)
    out = [dict(w) for w in whisper_words]
    matched = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal' or (tag == 'replace' and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                out[i1 + k]['s'], out[i1 + k]['e'] = aligned_words[j1 + k]['s'], aligned_words[j1 + k]['e']
                matched += 1
    return out, len(out) - matched


def merge_aligned(whisper_segments, aligned_segments):
    """Words of the whole file, never fewer than Whisper heard: Whisper's text, the aligner's times wherever it placed
    the word, Whisper's own times elsewhere. `whisper_segments` are faster-whisper segments, `aligned_segments` the
    aligner's output (any segmentation). -> (words, {'words_whisper_timing': n})."""
    whisper = join_apostrophes([dict(w=w.word.strip(), s=w.start, e=w.end)
                                for ws in whisper_segments for w in (ws.words or []) if w.word.strip()])
    aligned = []
    for seg in aligned_segments:
        aligned += complete_words(seg.get('words') or [], seg.get('start', 0.0), seg.get('end', 0.0))
    if not whisper:
        return aligned, dict(words_whisper_timing=0)
    if not aligned:
        return whisper, dict(words_whisper_timing=len(whisper))
    words, own = reconcile(whisper, aligned)
    return words, dict(words_whisper_timing=own)


def transcribe(media, language='fr', model_name='large-v3-turbo', align=True, hint=None):
    import whisperx
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / 'a.wav'
        extract_audio(media, wav)
        audio = whisperx.load_audio(str(wav))
        model = _model(model_name)
        # condition_on_previous_text=False and a fixed temperature: with the defaults Whisper skipped passages after
        # a repetition loop and gave a different text on every run (909, 1056 and 1158 words for the same rush).
        segs, _ = model.transcribe(audio, language=language, word_timestamps=True, initial_prompt=hint,
                                   vad_filter=True, condition_on_previous_text=False, temperature=0.0)
        segs = list(segs)
        words = [dict(w=w.word.strip(), s=w.start, e=w.end) for x in segs for w in (x.words or []) if w.word.strip()]
        aligned = False
        if align and segs:
            try:
                am, meta = _align_model(language)
                res = whisperx.align([dict(text=x.text, start=x.start, end=x.end) for x in segs], am, meta, audio,
                                     'cpu', return_char_alignments=False)
                aw, stats = merge_aligned(segs, res['segments'])
                if aw:
                    words, aligned = aw, True
                if stats['words_whisper_timing']:
                    print(f'{stats["words_whisper_timing"]} of {len(aw)} words kept with Whisper timing '
                          f'(unplaced by the aligner)', file=sys.stderr)
            except Exception as exc:   # alignment is an improvement, never a blocker
                print('alignment skipped:', exc, file=sys.stderr)
        return words, aligned


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('store')
    ap.add_argument('media', nargs='*')
    ap.add_argument('--clean', action='store_true', help='remove known hallucinated phrases from stored transcripts')
    ap.add_argument('--language', default='fr')
    ap.add_argument('--model', default='large-v3-turbo')
    ap.add_argument('--no-align', action='store_true')
    ap.add_argument('--hint')
    ap.add_argument('--device', choices=['auto', 'cuda', 'cpu'], default='auto')
    a = ap.parse_args(argv)
    global DEVICE
    DEVICE = a.device
    store = TranscriptStore(a.store)
    if a.clean:
        for name, phrases in store.clean().items():
            print('cleaned', name, phrases)
        return
    if not a.media:
        ap.error('give at least one media file, or --clean')
    for m in a.media:
        if store.load(m):
            print('skip (already transcribed):', os.path.basename(m))
            continue
        words, aligned = transcribe(m, a.language, a.model, not a.no_align, a.hint)
        store.save(m, words, language=a.language, model=a.model, aligned=aligned)
        print('transcribed', os.path.basename(m), len(words), 'words', 'aligned' if aligned else 'whisper timing')


if __name__ == '__main__':
    main()
