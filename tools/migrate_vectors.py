"""One-time, verified Chroma-to-SQLite migration. Run before removing legacy packages."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

import numpy as np
import rag
from index_lock import index_writer
from vector_store import VectorStore


def legacy_collection(directory, name):
    # Only this explicit migration tool imports the retired dependency. The
    # application, installers and tests do not require it after migration.
    try:
        import chromadb
        from chromadb.config import Settings
    except ImportError as error:
        raise RuntimeError('Run migration using the previous environment, before removing Chroma.') from error
    client=chromadb.PersistentClient(path=str(directory),settings=Settings(
        anonymized_telemetry=False,chroma_api_impl='chromadb.api.rust.RustBindingsAPI',
        chroma_server_host=None,chroma_server_http_port=None,allow_reset=False))
    return client.get_collection(name,embedding_function=None)


def migrate(config, *, reader=legacy_collection):
    target=config.db/f'{config.collection}-vectors.sqlite3'
    with index_writer(config):
        if target.exists():raise FileExistsError('SQLite vector store already exists; it was not overwritten.')
        state=rag.load_state(config)
        ids=sorted(rag.published_ids(state))
        if not ids:raise ValueError('No published vectors to migrate.')
        manifest=config.db/f'{config.collection}-manifest.json'
        before=manifest.read_bytes()
        backup=config.db.parent/'vector-backups'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
        # Never open the original Chroma database. Even its compatibility client
        # may checkpoint SQLite files; all legacy reads operate on this copy.
        shutil.copytree(config.db,backup,ignore=shutil.ignore_patterns('*-writer.lock'))
        source=reader(backup,config.collection)
        pending=config.db/f'.{config.collection}-{uuid4().hex}.migrating.sqlite3'
        store=VectorStore(pending)
        digest=hashlib.sha256();dimensions=None
        try:
            for start in range(0,len(ids),256):
                selected=ids[start:start+256]
                rows=source.get(ids=selected,include=['documents','metadatas','embeddings'])
                if set(rows['ids'])!=set(selected):raise ValueError('Legacy index is missing published vectors; migration stopped.')
                store.upsert_vectors(rows['ids'],rows['documents'],rows['metadatas'],rows['embeddings'])
                copied=store.get(ids=selected,include=['documents','metadatas','embeddings'])
                source_positions={identifier:i for i,identifier in enumerate(rows['ids'])}
                for i,identifier in enumerate(copied['ids']):
                    old=source_positions[identifier]
                    vector=np.asarray(rows['embeddings'][old],dtype='<f4')
                    if (copied['documents'][i]!=rows['documents'][old]
                            or copied['metadatas'][i]!=rows['metadatas'][old]
                            or not np.array_equal(copied['embeddings'][i],vector)):
                        raise ValueError('Vector/text/metadata verification failed; migration stopped.')
                    dimensions=len(vector)
                    payload=json.dumps([identifier,copied['documents'][i],copied['metadatas'][i]],
                                       ensure_ascii=False,sort_keys=True).encode('utf-8')
                    digest.update(len(payload).to_bytes(8,'big'));digest.update(payload);digest.update(vector.tobytes())
            if len(store.get(include=[])['ids'])!=len(ids):raise ValueError('Migrated vector count differs.')
            if manifest.read_bytes()!=before:raise ValueError('Manifest changed during migration; result not published.')
            with store.connect(write=True) as db:db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            pending.replace(target)
        except BaseException:
            for suffix in ('','-wal','-shm'):
                Path(str(pending)+suffix).unlink(missing_ok=True)
            raise
        report={'vectors':len(ids),'dimensions':dimensions,'content_sha256':digest.hexdigest(),
                'manifest_sha256':hashlib.sha256(before).hexdigest(),'backup':str(backup),
                'target':str(target),'embedding_calls':0,'llm_calls':0}
        (backup/'migration-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path)
    parser.add_argument('--collection')
    args=parser.parse_args()
    from dataclasses import replace
    config=rag.Config()
    if args.db:config=replace(config,db=args.db.resolve())
    if args.collection:config=replace(config,collection=args.collection)
    if not config.collection or Path(config.collection).name!=config.collection or any(char in config.collection for char in '/\\:'):
        parser.error('Invalid collection name.')
    from server_control import matching_processes,default_port
    if matching_processes(rag.ROOT,default_port(rag.ROOT)):
        parser.error('Stop the RAG web server before migrating. Ollama can keep running.')
    print(json.dumps(migrate(config),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
