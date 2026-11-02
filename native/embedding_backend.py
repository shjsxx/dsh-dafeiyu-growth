"""Optional local neural embedding via BGE-small-zh ONNX.

Weights are NOT committed. Download once with:
    python native/embedding_backend.py --download

After that, encoding is fully offline and memory text never leaves the machine.
If onnxruntime/model files are missing, callers must fall back to hashed recall.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.request

# Import before the Qt/COM event loop starts. Loading the ONNX DLL lazily from
# inside a Qt slot can deadlock on Windows; preloading keeps recall responsive.
try:
    import onnxruntime as _ort
except Exception:  # optional dependency: hashed recall remains available
    _ort = None

REVISION = "313386ad7ffad360a4870fd1910f8ecaf1a05151"
BASE = f"https://www.modelscope.cn/models/Xenova/bge-small-zh-v1.5/resolve/{REVISION}/"
FILES = {
    "model_quantized.onnx": ("onnx/model_quantized.onnx", 1_000_000),
    "vocab.txt": ("vocab.txt", 10_000),
    "tokenizer_config.json": ("tokenizer_config.json", 100),
}
MODEL_NAME = "bge-small-zh-v1.5"


def default_dir():
    explicit = os.environ.get("DSH_DAFEIYU_EMBEDDING_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "dsh-dafeiyu" / "models" / MODEL_NAME


class BertTokenizer:
    """Minimal BERT Chinese WordPiece tokenizer, no transformers dependency."""

    def __init__(self, vocab_path, max_length=128):
        self.vocab = {}
        with Path(vocab_path).open("r", encoding="utf-8") as stream:
            for index, line in enumerate(stream):
                token = line.rstrip("\n")
                if token:
                    self.vocab[token] = index
        self.max_length = max(8, min(256, int(max_length)))
        self.unk = self.vocab.get("[UNK]", 100)
        self.cls = self.vocab.get("[CLS]", 101)
        self.sep = self.vocab.get("[SEP]", 102)

    @staticmethod
    def _basic(text):
        tokens, word = [], []
        def flush():
            if word:
                tokens.append("".join(word).lower())
                word.clear()
        for char in str(text or ""):
            if char.isspace():
                flush()
            elif "\u4e00" <= char <= "\u9fff" or "\u3400" <= char <= "\u4dbf":
                flush()
                tokens.append(char)
            elif char.isalnum():
                word.append(char)
            else:
                flush()
                tokens.append(char)
        flush()
        return tokens

    def _wordpiece(self, word):
        if word in self.vocab:
            return [word]
        pieces, start = [], 0
        while start < len(word):
            end, piece = len(word), None
            while end > start:
                candidate = word[start:end]
                if start:
                    candidate = "##" + candidate
                if candidate in self.vocab:
                    piece = candidate
                    break
                end -= 1
            if piece is None:
                return []
            pieces.append(piece)
            start = end
        return pieces

    def encode(self, text, max_length=None):
        limit = max_length or self.max_length
        tokens = []
        for word in self._basic(text)[:limit - 2]:
            pieces = self._wordpiece(word)
            tokens.extend(pieces or [self.unk])
        ids = [self.cls] + [self.vocab.get(token, self.unk) for token in tokens[:limit - 2]] + [self.sep]
        return ids


class EmbeddingBackend:
    def __init__(self, directory=None, max_length=128):
        self.directory = Path(directory).expanduser() if directory else default_dir()
        self.max_length = max_length
        self.status = "unavailable"
        self.error = ""
        self._session = None
        self._tokenizer = None

    def files_present(self):
        return all((self.directory / name).is_file() and (self.directory / name).stat().st_size >= minimum
                   for name, (_, minimum) in FILES.items())

    def load(self):
        if self._session is not None:
            return True
        if not self.files_present():
            self.status, self.error = "unavailable", "embedding model not downloaded"
            return False
        try:
            if _ort is None:
                raise ImportError("onnxruntime unavailable")
            self._tokenizer = BertTokenizer(self.directory / "vocab.txt", self.max_length)
            options = _ort.SessionOptions()
            options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
            self._session = _ort.InferenceSession(
                str(self.directory / "model_quantized.onnx"), sess_options=options,
                providers=["CPUExecutionProvider"])
            self.status, self.error = "ready", ""
            return True
        except (ImportError, OSError, ValueError, RuntimeError) as error:
            self._session = None
            self.status, self.error = "error", type(error).__name__
            return False

    def encode(self, texts):
        """Return normalized CLS embeddings as list[list[float]]."""
        if not self.load():
            return []
        import numpy as np
        texts = [str(text or "")[:300] for text in texts]
        if not texts:
            return []
        token_ids = [self._tokenizer.encode(text) for text in texts]
        width = max(len(row) for row in token_ids)
        ids = np.zeros((len(texts), width), dtype=np.int64)
        mask = np.zeros((len(texts), width), dtype=np.int64)
        for row, values in enumerate(token_ids):
            ids[row, :len(values)] = values
            mask[row, :len(values)] = 1
        output = self._session.run(None, {
            "input_ids": ids, "attention_mask": mask,
            "token_type_ids": np.zeros_like(ids),
        })[0]
        vectors = output[:, 0, :].astype("float32")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors = vectors / np.maximum(norms, 1e-9)
        return vectors.tolist()

    def download(self, force=False):
        self.directory.mkdir(parents=True, exist_ok=True)
        print(f"target: {self.directory}")
        for name, (relative, minimum) in FILES.items():
            target = self.directory / name
            if target.is_file() and target.stat().st_size >= minimum and not force:
                print(f"exists {name} {target.stat().st_size}")
                continue
            temp = target.with_suffix(target.suffix + ".part")
            request = urllib.request.Request(BASE + relative, headers={"User-Agent": "Yunlan/0.24 local-embedding"})
            with urllib.request.urlopen(request, timeout=120) as response, temp.open("wb") as stream:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
            if temp.stat().st_size < minimum:
                temp.unlink(missing_ok=True)
                raise RuntimeError(f"download too small: {name}")
            os.replace(temp, target)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            print(f"downloaded {name} {target.stat().st_size} sha256={digest}")
        self.status, self.error = "unavailable", ""
        return self.files_present()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--encode", default="")
    args = parser.parse_args()
    backend = EmbeddingBackend()
    if args.download:
        ok = backend.download()
        print(json.dumps({"ok": ok, "dir": str(backend.directory)}, ensure_ascii=False))
        return 0 if ok else 1
    if args.encode:
        vectors = backend.encode([args.encode, "喜欢在夜里散步"])
        print(json.dumps({"status": backend.status, "dim": len(vectors[0]) if vectors else 0,
                          "similarity": round(sum(a * b for a, b in zip(vectors[0], vectors[1])), 4) if len(vectors) == 2 else None},
                         ensure_ascii=False))
        return 0 if vectors else 1
    print(json.dumps({"status": "ready" if backend.files_present() else "unavailable",
                      "dir": str(backend.directory), "files": FILES}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
