"""Run with python -m tools.recipes; operations default to a private data directory."""
import argparse
import json
from pathlib import Path
import time

from .sources import SOURCES, client, discover, download, write_json
from .pipeline import stage_sources, deduplication, export, stage_for_ingestion, log
from .reports import reports
from .integration import private_index, verify
from .catalog import select


def main(argv=None):
    parser = argparse.ArgumentParser(description='Musa: reproducible recipe corpus preparation')
    parser.add_argument('phase', choices=['prepare','discover','download','extract','normalize','validate','deduplicate','export','stage_for_ingestion','index','verify','report','query'])
    parser.add_argument('--source', action='append', choices=[item.id for item in SOURCES])
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parents[2] / 'data' / 'recipe-corpus')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--index', action='store_true',help='Opt in to indexing a private verification pilot, never the active library.')
    parser.add_argument('--semantic', action='store_true',help='Run the ten retrieval smoke cases on CPU using the cached embedding model.')
    parser.add_argument('--country');parser.add_argument('--region')
    parser.add_argument('--servings',type=int);parser.add_argument('--max-ingredients',type=int)
    parser.add_argument('--ingredient',action='append',default=[])
    parser.add_argument('--historical',action='store_true',default=None)
    parser.add_argument('--title-threshold',type=float,default=98)
    parser.add_argument('--ingredient-threshold',type=float,default=.95)
    parser.add_argument('--steps-threshold',type=float,default=98)
    args = parser.parse_args(argv)
    selected = [item for item in SOURCES if not args.source or item.id in args.source]
    if args.dry_run:
        print(json.dumps({'phase': args.phase, 'sources': [item.id for item in selected], 'output': str(args.output_dir)}));return
    if args.limit is not None and args.limit < 1:parser.error('--limit must be positive')
    if not 0<=args.ingredient_threshold<=1 or not 0<=args.title_threshold<=100 or not 0<=args.steps_threshold<=100:
        parser.error('Invalid deduplication thresholds.')
    output=args.output_dir.resolve();started=time.perf_counter()
    if args.phase=='query':
        result=select(output,country=args.country,region=args.region,servings=args.servings,max_ingredients=args.max_ingredients,
                      ingredients=args.ingredient,historical=args.historical,limit=args.limit or 100)
        result['records']=[{name:row.get(name) for name in ('recipe_id','titulo','pais_origen','region','raciones','ingredient_count','source_url')} for row in result['records']]
        print(json.dumps(result,ensure_ascii=True));return 0
    phases=['download','extract','normalize','validate','deduplicate','export','stage_for_ingestion','verify','report'] if args.phase=='prepare' else [args.phase]
    run=[]
    for phase in phases:
        log(phase,status='starting')
        if phase in {'discover','download'}:
            result=[]
            with client() as session:
                for source in selected:
                    log(phase,source=source.id,status='starting')
                    info=discover(source,output,session,args.force)
                    item=download(source,info,output,session) if phase=='download' else info
                    result.append(item);log(phase,source=source.id,status=item.get('download_status',item['status']),reused=item.get('reused',False),error=item.get('error'))
            write_json(output/'cache'/f'{phase}.json',result)
        elif phase in {'extract','normalize','validate'}:
            result=stage_sources(output,phase,selected,args.limit,args.force)
        elif phase=='deduplicate':
            result=deduplication(output,selected,args.force,title_threshold=args.title_threshold,
                                 ingredient_threshold=args.ingredient_threshold,steps_threshold=args.steps_threshold)
        elif phase=='export':result=export(output,args.force)
        elif phase=='stage_for_ingestion':result=stage_for_ingestion(output,args.limit)
        elif phase=='index':result=private_index(output,args.limit or 160)[-1]
        elif phase=='verify':
            result=verify(output,semantic=args.semantic or args.index,limit=args.limit or 160)
        else:
            reports(output);result={'reports':str(output/'reports')}
        run.append({'phase':phase,'result':result})
        log(phase,status='done',**({name:value for name,value in result.items() if name in {'documents','ready','written','reused','unique_recipes','duplicate_records','review_records','integrity_ok'}} if isinstance(result,dict) else {}))
    write_json(output/'cache'/'last-run.json',{'phase':args.phase,'seconds':round(time.perf_counter()-started,3),'stages':run})
    if phases[-1] not in {'discover','download','extract','normalize','validate','query','report'} or args.phase=='prepare':
        reports(output)
    if any(isinstance(item['result'],dict) and item['result'].get('integrity_ok') is False for item in run):return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
