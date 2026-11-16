"""Start the unmodified application with private paths and genuine local models."""
import json
import os
from pathlib import Path
import sys


def main():
    settings = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    root = Path(settings['instance']).resolve()
    os.environ.update({
        'CUDA_VISIBLE_DEVICES': '', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'RAG_WEB_HOST': '127.0.0.1', 'RAG_RERANKER': '', 'RAG_GENERATION_PROVIDER': 'ollama',
        'RAG_OLLAMA_URL': settings['ollama_url'], 'RAG_OLLAMA_MODEL': settings.get('model') or '',
        'RAG_EXTERNAL_MODEL': '', 'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2',
        'RAG_MAX_NEW_TOKENS': '512', 'RAG_REASONING_MAX_NEW_TOKENS': str(settings['max_output_tokens']),
    })
    import rag
    from web_app import RagService, create_app
    import uvicorn
    config = rag.Config(db=root/'data'/'index', collection='live_security',
                        embedding_model=settings['embedding_path'], embedding_device='cpu',
                        chunk_size=1200, chunk_overlap=120, context_chars=10000)
    service = RagService(config, root/'data'/'documents')
    # No subclass, patched transport, fake embedding, generated response or sensor.
    app = create_app(service)
    uvicorn.run(app, host='127.0.0.1', port=settings['port'], access_log=True)


if __name__ == '__main__':
    main()
