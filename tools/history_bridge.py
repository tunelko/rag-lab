"""Preserve available metrics during a long upgrade; stop when native history starts."""
import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
import urllib.request

from server_control import matching_processes
from statistics_store import StatisticsStore, RETENTION

ROOT=Path(__file__).resolve().parents[1]


def read(url):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url,timeout=8) as response:
        return json.load(response)


def capture(url, store, *, seed=False):
    data=read(url+'/api/telemetry')
    if data.get('retention_seconds')==RETENTION:
        return True
    if seed:
        for point in data.get('history',[]):
            store.save_sample(point,force=True)
    gpu=next(iter(data.get('gpu',{}).get('devices',[])),{})
    store.save_sample({'time':data['time'],'cpu_percent':data.get('cpu_percent'),
        'gpu_percent':gpu.get('utilization_percent'),'ram_used_bytes':data.get('ram_used_bytes'),
        'ram_total_bytes':data.get('ram_total_bytes'),'vram_used_bytes':gpu.get('memory_used_bytes'),
        'vram_total_bytes':gpu.get('memory_total_bytes')})
    for job in read(url+'/api/jobs').get('jobs',[]):
        store.save_job(job)
    return False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8765')
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    endpoint=urlsplit(args.url)
    if endpoint.scheme!='http' or endpoint.hostname not in ('127.0.0.1','localhost') or endpoint.path not in ('','/') or endpoint.username or endpoint.query or endpoint.fragment:
        parser.error('Use a plain loopback HTTP URL.')
    url=args.url.rstrip('/')
    store=StatisticsStore(ROOT/'data/statistics.sqlite3')
    seeded=False
    try:
        while matching_processes(ROOT,endpoint.port or 80):
            try:
                if capture(url,store,seed=not seeded):
                    print('Native history active; upgrade bridge finished.',flush=True)
                    return 0
                seeded=True
                print('Recorded available hardware/query/index metrics.',flush=True)
            except (OSError,ValueError) as error:
                print(f'Waiting for local server: {error}',flush=True)
            if args.once: return 0
            time.sleep(30)
        return 0
    finally:
        store.close()


if __name__=='__main__':
    raise SystemExit(main())
