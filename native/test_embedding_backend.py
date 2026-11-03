"""Local ONNX embedding: tokenizer, fallback and optional model smoke."""
from pathlib import Path
import tempfile
import unittest

from embedding_backend import BertTokenizer, EmbeddingBackend
from memory_engine import MemoryEngine


class EmbeddingBackendTests(unittest.TestCase):
    def test_minimal_bert_tokenizer_uses_cls_sep_and_wordpiece(self):
        with tempfile.TemporaryDirectory() as directory:
            vocab = Path(directory) / 'vocab.txt'
            vocab.write_text('[PAD]\n[UNK]\n[CLS]\n[SEP]\n你\n好\nplay\n##ing\n', encoding='utf-8')
            tokenizer = BertTokenizer(vocab, max_length=32)
            ids = tokenizer.encode('你playing好')
            self.assertEqual(ids[0], tokenizer.cls)
            self.assertEqual(ids[-1], tokenizer.sep)
            self.assertIn(tokenizer.vocab['你'], ids)
            self.assertIn(tokenizer.vocab['play'], ids)
            self.assertIn(tokenizer.vocab['##ing'], ids)
            self.assertIn(tokenizer.vocab['好'], ids)

    def test_missing_model_fails_closed_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = EmbeddingBackend(directory)
            self.assertFalse(backend.files_present())
            self.assertEqual(backend.encode(['任何文本']), [])

    def test_local_model_embeds_and_ranks_when_downloaded(self):
        backend = EmbeddingBackend()
        if not backend.files_present():
            self.skipTest('optional BGE model not downloaded')
        vectors = backend.encode(['晚上喜欢做什么', '喜欢在夜里散步', '法国的首都是巴黎'])
        self.assertEqual(len(vectors), 3)
        self.assertEqual(len(vectors[0]), 512)
        near = sum(a * b for a, b in zip(vectors[0], vectors[1]))
        far = sum(a * b for a, b in zip(vectors[0], vectors[2]))
        self.assertGreater(near, far + .15)

    def test_memory_snapshot_reports_embedding_availability(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = MemoryEngine(Path(directory) / 'memory.json', clock=lambda: 1_800_000_000)
            engine.ingest_state({'diary': [{'text': '今天完成了一次本地回顾'}]})
            snapshot = engine.snapshot()
            self.assertIn(snapshot['embedding'], {'ready', 'unavailable'})


if __name__ == '__main__':
    unittest.main()
