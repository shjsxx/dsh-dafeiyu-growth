"""Isolated smoke settings; never inherit owner's keys or explicit data paths."""
from pathlib import Path

FILES = {
    'GROWTH': 'profile.json', 'BEHAVIOR': 'behavior.json', 'NEURAL':'neural.json',
    'CLOUD':'cloud.json', 'REALTIME':'realtime.json', 'EMOTION':'emotion.json',
    'LEARNING':'learning.json', 'ONLINE':'online.json', 'TRAINING':'training.json',
    'KNOWLEDGE':'knowledge.json', 'COMPANION':'companion.json', 'MEMORY':'memory.json',
    'PERSONALITY':'personality.json', 'CONTINUITY':'continuity.json',
    'ENGINE':'growth.json', 'REVIEW':'retrieval_reviews.json', 'RERANK':'embedding_rerank.json',
}


def build_environment(environ, directory, native_root, asset_root):
    out, root = Path(directory).resolve(), Path(native_root).resolve()
    env = {key:value for key,value in environ.items() if not key.upper().startswith('DSH_DAFEIYU_')}
    env.update({f'DSH_DAFEIYU_{name}_FILE':str(out / filename) for name,filename in FILES.items()})
    env.update({
        'QT_QPA_PLATFORM':'offscreen',
        'QT_QPA_FONTDIR': str(Path(environ.get('SystemRoot', 'C:/Windows')) / 'Fonts'),
        'PYTHONIOENCODING':'utf-8', 'PYTHONPATH':str(root),
        'DSH_DAFEIYU_EMBEDDING_DIR':str(out / 'empty-embedding-model'),
        'DSH_DAFEIYU_LAYOUT_PATH':str(out / 'layout.json'),
        'DSH_DAFEIYU_GROWTH_SMOKE_DIR':str(out),
        'DSH_DAFEIYU_ASSET_ROOT':str(asset_root),
    })
    return env
