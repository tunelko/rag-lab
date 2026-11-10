"""Audit selected PDFs and create a local labeling report. Never calls an LLM or edits an index."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import time

import pymupdf
from visual_selection import POLICY, analyze_page


def evaluate(dataset, labels):
    decisions={row['id']:row['candidate'] for row in dataset['pages']}
    valid={key:value for key,value in labels.items() if key in decisions and value in ('relevant','irrelevant')}
    tp=sum(value=='relevant' and decisions[key] for key,value in valid.items())
    fn=sum(value=='relevant' and not decisions[key] for key,value in valid.items())
    fp=sum(value=='irrelevant' and decisions[key] for key,value in valid.items())
    return {'reviewed':len(valid),'total_pages':len(decisions),'true_positive':tp,'false_negative':fn,'false_positive':fp,
        'recall_reviewed':tp/(tp+fn) if tp+fn else None,'precision_reviewed':tp/(tp+fp) if tp+fp else None,
        'scope':'Only manually labeled pages; unreviewed/uncertain pages are excluded.'}


def select_previews(pages,count):
    groups=defaultdict(list)
    for page in pages: groups[(page['document'],page['reason'])].append(page)
    selected=[]
    # Round-robin across documents and decision reasons avoids a positive-only sample.
    while groups and len(selected)<count:
        for key in sorted(list(groups)):
            values=groups[key]
            selected.append(values.pop(len(values)//2))
            if not values: del groups[key]
            if len(selected)>=count: break
    return {row['id'] for row in selected}


def audit(paths, output, *, previews=36):
    started=time.perf_counter()
    output.mkdir(parents=True,exist_ok=True)
    pages,documents=[],[]
    for path in paths:
        path=path.resolve()
        with path.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
        with pymupdf.open(path) as pdf:
            if pdf.is_encrypted: raise ValueError(f'Encrypted PDF: {path.name}')
            documents.append({'name':path.name,'sha256':digest,'pages':len(pdf)})
            for number,page in enumerate(pdf,1):
                pages.append({'id':f'{digest}:{number}','document':path.name,'sha256':digest,'page':number,
                    'pdf_url':path.as_uri()+f'#page={number}',**analyze_page(page)})
        print(f'Analyzed {path.name}: {documents[-1]["pages"]} pages',flush=True)
    selected=select_previews(pages,previews)
    preview_dir=output/'previews'
    preview_dir.mkdir(exist_ok=True)
    for path,document in zip(paths,documents):
        with pymupdf.open(path) as pdf:
            for row in pages:
                if row['sha256']!=document['sha256'] or row['id'] not in selected: continue
                page=pdf[row['page']-1]
                scale=min(2,1200/max(page.rect.width,page.rect.height))
                name=f'{row["sha256"][:16]}-{row["page"]}.png'
                page.get_pixmap(matrix=pymupdf.Matrix(scale,scale),alpha=False).save(preview_dir/name)
                row['preview']='previews/'+name
    candidate=sum(row['candidate'] for row in pages)
    legacy=sum(row['features']['legacy_visual'] for row in pages)
    dataset={'policy':POLICY,'pymupdf':pymupdf.VersionBind,'documents':documents,'pages':pages,
        'summary':{'pages':len(pages),'legacy_candidate_pages':legacy,'proposed_candidate_pages':candidate,
            'candidate_page_reduction':(legacy-candidate)/legacy if legacy else None,
            'preview_pages':len(selected),'reasons':dict(Counter(row['reason'] for row in pages)),
            'elapsed_seconds':round(time.perf_counter()-started,3),
            'model_calls':0,'applied_to_ingestion':False,'manual_labels':0,
            'note':'Candidate-page proxy, not measured inference calls/time saved. Recall requires manual labels.'}}
    dataset['id']=hashlib.sha256(json.dumps(sorted((item['sha256'],item['pages']) for item in documents)).encode()).hexdigest()
    (output/'features.json').write_text(json.dumps(dataset,ensure_ascii=False,indent=2),encoding='utf-8')
    template=Path(__file__).with_suffix('.html').read_text(encoding='utf-8')
    encoded=json.dumps(dataset,ensure_ascii=False).replace('<','\\u003c')
    (output/'index.html').write_text(template.replace('__DATASET_JSON__',encoded),encoding='utf-8')
    return dataset


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf',type=Path,action='append',help='Repeat for each PDF to audit.')
    parser.add_argument('--output',type=Path,default=Path('reports/visual-audit'))
    parser.add_argument('--previews',type=int,default=36)
    parser.add_argument('--evaluate',type=Path,help='Existing features.json to evaluate without parsing PDFs.')
    parser.add_argument('--labels',type=Path,help='Manual labels exported by the HTML report.')
    args=parser.parse_args()
    if args.evaluate:
        if not args.labels: parser.error('--evaluate requires --labels')
        dataset=json.loads(args.evaluate.read_text(encoding='utf-8'))
        labels=json.loads(args.labels.read_text(encoding='utf-8'))
        if labels.get('dataset_id')!=dataset['id']: parser.error('The labels belong to a different dataset.')
        print(json.dumps(evaluate(dataset,labels['labels']),ensure_ascii=False,indent=2))
    else:
        if not args.pdf: parser.error('Provide one or more --pdf files.')
        if not 1<=args.previews<=1000: parser.error('--previews must be between 1 and 1000.')
        result=audit(args.pdf,args.output,previews=args.previews)
        print(json.dumps(result['summary'],ensure_ascii=False,indent=2))
        print(str((args.output/'index.html').resolve()))


if __name__=='__main__': main()
