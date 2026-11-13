"""Conservative visual-page triage for offline evaluation; not an ingestion filter."""
import re
import time

POLICY='visual-triage-v1'
TECHNICAL=re.compile(r'ATX[ _-]?12V|ATX[ _-]?POWER|CPU[ _-]?FAN|SYS[ _-]?FAN|F[ _-]?PANEL|F[ _-]?USB|'
    r'JFP\d|JUSB\d|JAUD\d|F[ _-]?AUDIO|F[ _-]?COM|CLR[ _-]?CMOS|pinout|pin\s+(?:assignment|definition|header)|pinbelegung|'
    r'connector\s+(?:diagram|layout)|board\s+layout|esquema\s+electrico|diagrama\s+de\s+conectores|'
    r'針腳|针脚|接腳|接脚|ピン配置|커넥터',re.IGNORECASE)


def area(box):
    return max(0,box[2]-box[0])*max(0,box[3]-box[1])


def union_area(boxes):
    xs=sorted({box[index] for box in boxes for index in (0,2)})
    result=0
    for left,right in zip(xs,xs[1:]):
        intervals=sorted((box[1],box[3]) for box in boxes if box[0]<right and box[2]>left)
        covered=0
        end=None
        for low,high in intervals:
            covered+=max(0,high-max(low,end if end is not None else low))
            end=max(high,end if end is not None else high)
        result+=(right-left)*covered
    return result


def inside(box,table):
    return box[0]>=table[0]-2 and box[1]>=table[1]-2 and box[2]<=table[2]+2 and box[3]<=table[3]+2


def axis_only(drawing):
    for item in drawing.get('items',[]):
        if item[0]=='re': continue
        if item[0]!='l': return False
        first,second=item[1:3]
        if abs(first.x-second.x)>1 and abs(first.y-second.y)>1: return False
    return True


def classify(features):
    """Scores express deterministic rules, not calibrated probabilities or recall."""
    if features['technical_cues'] and (features['image_count'] or features['vector_count']):
        return True,.95,'technical-diagram-cue'
    if features['text_chars']<40 and (features['image_fraction']>.05 or features['vector_outside_count']>=4):
        return True,1.0,'scan-or-low-text-graphics'
    if features['largest_image_fraction']>=.04 or features['image_fraction']>=.10:
        return True,.90,'substantial-raster-image'
    if features['table_error'] and features['vector_count']:
        return True,.60,'review-table-detection-error'
    if features['vector_complex_outside_count']>=2:
        return True,.80,'complex-vector-figure'
    if features['vector_outside_count']>=6:
        return True,.75,'vector-structure-outside-tables'
    if features['vector_outside_count'] or features['central_small_images']:
        return True,.55,'review-ambiguous-graphics'
    if features['table_count'] and features['text_chars']>=80:
        return False,.15,'native-table-and-text'
    return False,.05,'text-or-small-edge-decoration' if features['text_chars'] else 'blank-page'


def analyze_page(page):
    started=time.perf_counter()
    bounds=tuple(page.rect)
    surface=max(area(bounds),1)
    text=page.get_text().strip()
    images=page.get_image_info()
    drawings=page.get_drawings()
    table_error=None
    try:
        tables=[tuple(table.bbox) for table in page.find_tables().tables]
    except Exception as error:
        tables=[]
        table_error=type(error).__name__
    boxes=[tuple(item['bbox']) for item in images if area(item['bbox'])>0]
    meaningful,complex_count,decoration=0,0,0
    for drawing in drawings:
        box=tuple(drawing['rect'])
        axis=axis_only(drawing)
        fraction=area(box)/surface
        center_y=(box[1]+box[3])/2
        edge=center_y<bounds[1]+.12*(bounds[3]-bounds[1]) or center_y>bounds[1]+.88*(bounds[3]-bounds[1])
        frame=axis and fraction>.90 and len(drawing.get('items',[]))<=4
        if frame or (edge and (fraction<.02 or min(box[2]-box[0],box[3]-box[1])<2)):
            decoration+=1
        elif not (axis and any(inside(box,table) for table in tables)):
            meaningful+=1
            complex_count+=not axis
    central=sum(.005<=area(box)/surface<.04 and bounds[1]+.15*(bounds[3]-bounds[1])<(box[1]+box[3])/2<bounds[1]+.85*(bounds[3]-bounds[1]) for box in boxes)
    features={'text_chars':len(text),'image_count':len(images),
        'image_fraction':round(min(1,union_area(boxes)/surface),5),
        'largest_image_fraction':round(max((area(box)/surface for box in boxes),default=0),5),
        'central_small_images':central,'vector_count':len(drawings),'vector_outside_count':meaningful,
        'vector_complex_outside_count':complex_count,'edge_decoration_count':decoration,
        'table_count':len(tables),'table_fraction':round(min(1,union_area(tables)/surface),5),
        'table_error':table_error,'technical_cues':sorted({match.group(0) for match in TECHNICAL.finditer(text)}),
        'legacy_visual':bool(page.get_images() or drawings or not text)}
    candidate,score,reason=classify(features)
    return {'features':features,'candidate':candidate,'score':score,'reason':reason,
            'elapsed_seconds':round(time.perf_counter()-started,6)}
