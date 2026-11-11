"""Local SQLite vectors and exact NumPy cosine search; no server or deserialization."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

import numpy as np
from langchain_core.documents import Document


class VectorStore:
    def __init__(self, path, embeddings=None):
        self.path=Path(path).resolve()
        self.embeddings=embeddings
        if not self.path.exists():
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.connect(write=True) as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.executescript('''
                    CREATE TABLE vectors (
                        id TEXT PRIMARY KEY, text TEXT NOT NULL,
                        metadata TEXT NOT NULL, file TEXT NOT NULL,
                        vector BLOB NOT NULL);
                    CREATE INDEX vector_files ON vectors(file);
                    CREATE TABLE info (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    PRAGMA user_version=1;
                ''')
        with self.connect() as db:
            if db.execute('PRAGMA user_version').fetchone()[0]!=1:
                raise ValueError('Unsupported vector database format.')

    @contextmanager
    def connect(self, *, write=False):
        db=sqlite3.connect(str(self.path) if write else self.path.as_uri()+'?mode=ro',
                           uri=not write,timeout=30)
        try:
            if write:db.execute('PRAGMA synchronous=FULL')
            with db:yield db
        finally:
            db.close()

    def upsert_vectors(self, ids, texts, metadatas, vectors):
        """Accept supplied vectors for indexing/migration; never load a model here."""
        if not ids:return
        if len(set(ids))!=len(ids) or any(not isinstance(item,str) or not item for item in ids):
            raise ValueError('Vector IDs must be unique nonempty strings.')
        matrix=np.asarray(vectors,dtype='<f4')
        if (matrix.ndim!=2 or matrix.shape[0]!=len(ids) or not matrix.shape[1]
                or len(texts)!=len(ids) or len(metadatas)!=len(ids)
                or not np.all(np.isfinite(matrix)) or np.any(np.linalg.norm(matrix,axis=1)==0)):
            raise ValueError('Invalid vector batch or dimensions.')
        rows=[]
        for identifier,text,metadata,vector in zip(ids,texts,metadatas,matrix):
            if not isinstance(text,str) or not isinstance(metadata,dict):
                raise ValueError('Invalid vector text or metadata.')
            rows.append((identifier,text,json.dumps(metadata,ensure_ascii=False,allow_nan=False),
                         str(metadata.get('file','')),vector.tobytes()))
        with self.connect(write=True) as db:
            db.execute('BEGIN IMMEDIATE')
            dimension=db.execute("SELECT value FROM info WHERE key='dimensions'").fetchone()
            if dimension is not None and int(dimension[0])!=matrix.shape[1]:
                raise ValueError('Embedding dimensions differ from this collection.')
            db.execute("INSERT OR IGNORE INTO info VALUES ('dimensions',?)",(str(matrix.shape[1]),))
            db.executemany('''INSERT INTO vectors VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                text=excluded.text,metadata=excluded.metadata,file=excluded.file,vector=excluded.vector''',rows)

    def add_documents(self, documents, ids):
        if not documents:return []
        if self.embeddings is None:raise ValueError('An embedding model is required for new text.')
        texts=[document.page_content for document in documents]
        self.upsert_vectors(ids,texts,[document.metadata for document in documents],
                            self.embeddings.embed_documents(texts))
        return ids

    def delete(self, ids):
        if not ids:return
        with self.connect(write=True) as db:
            db.executemany('DELETE FROM vectors WHERE id=?',[(identifier,) for identifier in ids])

    def get(self, ids=None, where=None, include=None, limit=None):
        include=['documents','metadatas'] if include is None else include
        if not set(include)<={'documents','metadatas','embeddings'}:
            raise ValueError('Unsupported vector fields.')
        conditions=[];parameters=[]
        if where is not None:
            if set(where)!={'file'}:raise ValueError('Only file filters are supported.')
            value=where['file']
            if isinstance(value,str):conditions.append('v.file=?');parameters.append(value)
            elif isinstance(value,dict) and set(value)=={'$in'} and isinstance(value['$in'],(list,tuple)):
                names=value['$in']
                if not names:return self.empty(include)
                if not all(isinstance(name,str) for name in names):raise ValueError('Invalid file filter.')
                conditions.append('v.file IN (SELECT name FROM selected_files)')
            else:raise ValueError('Invalid file filter.')
        if limit is not None and (not isinstance(limit,int) or limit<1):raise ValueError('Invalid limit.')
        if ids is not None and not ids:return self.empty(include)
        columns=['v.id']
        for field,column in [('documents','v.text'),('metadatas','v.metadata'),('embeddings','v.vector')]:
            if field in include:columns.append(column)
        with self.connect() as db:
            # Temporary tables avoid SQLite's parameter limit for large ID sets.
            # They belong to this connection, never to the persistent database.
            join=''
            if ids is not None:
                db.execute('CREATE TEMP TABLE selected_ids (id TEXT PRIMARY KEY)')
                db.executemany('INSERT OR IGNORE INTO selected_ids VALUES (?)',[(item,) for item in ids])
                join=' JOIN selected_ids s ON s.id=v.id'
            if where is not None and isinstance(where['file'],dict):
                db.execute('CREATE TEMP TABLE selected_files (name TEXT PRIMARY KEY)')
                db.executemany('INSERT OR IGNORE INTO selected_files VALUES (?)',[(name,) for name in names])
            sql='SELECT '+','.join(columns)+' FROM vectors v'+join
            if conditions:sql+=' WHERE '+' AND '.join(conditions)
            sql+=' ORDER BY v.id'
            if limit is not None:sql+=' LIMIT ?';parameters.append(limit)
            rows=db.execute(sql,parameters).fetchall()
        result=self.empty(include)
        result['ids']=[row[0] for row in rows]
        offset=1
        for field in ('documents','metadatas','embeddings'):
            if field not in include:continue
            values=[row[offset] for row in rows];offset+=1
            if field=='metadatas':values=[json.loads(value) for value in values]
            if field=='embeddings' and values:values=np.stack([np.frombuffer(value,dtype='<f4') for value in values])
            result[field]=values
        return result

    @staticmethod
    def empty(include):
        return {'ids':[],**{field:[] for field in include}}

    def similarity_search_with_score(self, question, k=4, *, ids=None, filter=None):
        if self.embeddings is None:raise ValueError('An embedding model is required for a query.')
        if k<1:raise ValueError('k must be positive.')
        rows=self.get(ids=ids,where=filter,include=['documents','metadatas','embeddings'])
        if not rows['ids']:return []
        vector=np.asarray(self.embeddings.embed_query(question),dtype=np.float32)
        matrix=rows['embeddings'];norm=np.linalg.norm(vector)
        if vector.ndim!=1 or matrix.shape[1]!=len(vector) or not np.isfinite(norm) or norm==0:
            raise ValueError('Invalid query embedding or dimensions.')
        distances=1-np.clip(matrix@vector/(np.linalg.norm(matrix,axis=1)*norm),-1,1)
        if not np.all(np.isfinite(distances)):raise ValueError('Invalid stored embeddings.')
        order=np.argsort(distances,kind='stable')[:k]
        return [(Document(page_content=rows['documents'][i],metadata=rows['metadatas'][i]),float(distances[i])) for i in order]
