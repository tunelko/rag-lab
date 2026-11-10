"""Small local SQLite history: seven days, real samples and per-query metrics."""
from datetime import datetime
import json
import math
import sqlite3
import threading
import time


RETENTION = 7 * 24 * 3600


def epoch(value):
    return datetime.fromisoformat(value).timestamp()


class StatisticsStore:
    def __init__(self, path, retention=RETENTION, sample_interval=30):
        self.path, self.retention, self.sample_interval = path, retention, sample_interval
        self.lock = threading.RLock()
        self.connection = None
        self.last_sample = None
        self.last_prune = 0
        self.history_cache = {}

    def _open(self):
        if self.connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
            self.connection.execute('PRAGMA journal_mode=WAL')
            self.connection.executescript('''
                CREATE TABLE IF NOT EXISTS samples (time REAL PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS queries (
                    id TEXT PRIMARY KEY, time REAL NOT NULL, status TEXT NOT NULL,
                    tokens INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS queries_time ON queries(time);
                CREATE TABLE IF NOT EXISTS ingestions (
                    id TEXT PRIMARY KEY, time REAL NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS ingestions_time ON ingestions(time);
            ''')
            self.last_sample = self.connection.execute('SELECT MAX(time) FROM samples').fetchone()[0]
        return self.connection

    def _prune(self, connection, now):
        if now - self.last_prune >= 1800:
            connection.execute('DELETE FROM samples WHERE time < ?', (now-self.retention,))
            connection.execute('DELETE FROM queries WHERE time < ?', (now-self.retention,))
            connection.execute('DELETE FROM ingestions WHERE time < ?', (now-self.retention,))
            self.last_prune = now

    def save_sample(self, point, *, force=False):
        timestamp = epoch(point['time'])
        with self.lock:
            connection = self._open()
            if timestamp < time.time()-self.retention:
                return
            if not force and self.last_sample is not None and timestamp-self.last_sample < self.sample_interval:
                return
            connection.execute('INSERT OR IGNORE INTO samples VALUES (?,?)',
                               (timestamp,json.dumps(point,ensure_ascii=False)))
            self.last_sample = max(timestamp,self.last_sample or timestamp)
            self._prune(connection,time.time())
            connection.commit()
            self.history_cache.clear()

    def history(self, seconds, *, end=None, limit=1200):
        end = time.time() if end is None else end
        with self.lock:
            connection = self._open()
            key = (seconds, int(end//30), self.last_sample)
            if key in self.history_cache:
                return self.history_cache[key]
            rows = connection.execute('SELECT time,payload FROM samples WHERE time>=? AND time<=? ORDER BY time',
                                      (end-seconds,end)).fetchall()
            points = [json.loads(row[1]) for row in rows]
            # Keep measured points, propagate missing intervals/sensors across thinning.
            indices = (list(range(len(points))) if len(points)<=limit else
                       sorted({round(i*(len(points)-1)/(limit-1)) for i in range(limit)}))
            compact, previous = [], -1
            fields = {'cpu':'cpu_percent','gpu':'gpu_percent','ram':'ram_used_bytes','vram':'vram_used_bytes'}
            for index in indices:
                point = dict(points[index])
                between = range(max(1,previous+1),index+1)
                point['break_before'] = any(rows[item][0]-rows[item-1][0]>90 for item in between)
                point['break_before_keys'] = [key for key,field in fields.items()
                    if any(points[item].get(field) is None for item in range(previous+1,index+1))]
                compact.append(point)
                previous=index
            stride = max((epoch(compact[i]['time'])-epoch(compact[i-1]['time']) for i in range(1,len(compact))),default=30)
            result={'history':compact,'history_seconds':seconds,'history_sample_count':len(rows),
                    'history_gap_seconds':max(90,stride*2),'archive_interval_seconds':self.sample_interval,
                    'retention_seconds':self.retention}
            summaries={}
            for name,field in fields.items():
                values=[point[field] for point in points if isinstance(point.get(field),(int,float))]
                scale=1024**3 if name in ('ram','vram') else 1
                summaries[name]={'min':min(values)/scale if values else None,'max':max(values)/scale if values else None,
                    'mean':sum(values)/len(values)/scale if values else None}
            result['history_summary']=summaries
            result['history_memory_totals']={name:max((point.get(field) or 0 for point in points),default=0)
                for name,field in (('ram','ram_total_bytes'),('vram','vram_total_bytes'))}
            self.history_cache = {key:result}
            return result

    def save_job(self, job):
        if job.get('kind') not in ('query','index'):
            return
        record = {key:job.get(key) for key in ('id','question','status','created_at','elapsed_seconds','queue_seconds','metrics',
            'document_name','document_names','chunks','documents')}
        metrics = record['metrics'] or {}
        with self.lock:
            connection = self._open()
            if job['kind']=='query':
                connection.execute('INSERT OR REPLACE INTO queries VALUES (?,?,?,?,?)',
                    (job['id'],epoch(job['created_at']),job['status'],int(metrics.get('output_tokens') or 0),
                     json.dumps(record,ensure_ascii=False)))
            else:
                connection.execute('INSERT OR REPLACE INTO ingestions VALUES (?,?,?,?)',
                    (job['id'],epoch(job['created_at']),job['status'],json.dumps(record,ensure_ascii=False)))
            self._prune(connection,time.time())
            connection.commit()

    def mark_interrupted(self):
        with self.lock:
            connection = self._open()
            connection.execute("UPDATE queries SET status='interrupted' WHERE status IN ('queued','running')")
            connection.execute("UPDATE ingestions SET status='interrupted' WHERE status IN ('queued','running')")
            self._prune(connection,time.time())
            connection.commit()

    def queries(self, seconds=RETENTION, *, page=1, per_page=50):
        with self.lock:
            connection = self._open()
            cutoff=time.time()-seconds
            total,completed,errors,tokens=connection.execute('''
                SELECT COUNT(*),COALESCE(SUM(status='done'),0),COALESCE(SUM(status='error'),0),
                       COALESCE(SUM(CASE WHEN status='done' THEN tokens ELSE 0 END),0)
                FROM queries WHERE time>=?''',(cutoff,)).fetchone()
            pages=max(1,math.ceil(total/per_page))
            page=min(page,pages)
            rows=connection.execute('SELECT payload,status FROM queries WHERE time>=? ORDER BY time DESC,id DESC LIMIT ? OFFSET ?',
                (cutoff,per_page,(page-1)*per_page)).fetchall()
            return {'queries':total,'completed':completed,'errors':errors,'output_tokens':tokens,
                    'recent':[{**json.loads(payload),'status':status} for payload,status in rows],
                    'page':page,'pages':pages,'per_page':per_page,'history_seconds':seconds,'retention_seconds':self.retention}

    def ingestions(self, seconds=RETENTION, *, page=1, per_page=50):
        with self.lock:
            connection=self._open()
            cutoff=time.time()-seconds
            total=connection.execute('SELECT COUNT(*) FROM ingestions WHERE time>=?',(cutoff,)).fetchone()[0]
            pages=max(1,math.ceil(total/per_page))
            page=min(page,pages)
            rows=connection.execute('SELECT payload,status FROM ingestions WHERE time>=? ORDER BY time DESC LIMIT ? OFFSET ?',
                (cutoff,per_page,(page-1)*per_page)).fetchall()
            return {'total':total,'page':page,'pages':pages,'recent':[{**json.loads(payload),'status':status} for payload,status in rows]}

    def close(self):
        with self.lock:
            if self.connection is not None:
                self.connection.close()
                self.connection=None
