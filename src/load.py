import json
from pathlib import Path
from datetime import datetime
from models import Activity, Tile

def load_activities(data_dir):
    out=[]
    for p in sorted(Path(data_dir).glob('*.json')):
        data=json.loads(p.read_text(encoding='utf-8'))
        for a in data.get('activities',[]):
            out.append(Activity(
                id=a['id'],
                date=datetime.fromisoformat(a['date'].replace(' ','T')),
                distance_km=a.get('distance',0)/1000,
                activity_type=a.get('type',''),
                tiles=[Tile(t['x'],t['y']) for t in a.get('tiles',[])]
            ))
    return out
