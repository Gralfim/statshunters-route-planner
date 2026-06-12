def build_tile_database(activities, start_date=None, end_date=None):
    db={}
    for act in activities:
        if act.activity_type!='Run':
            continue
        if start_date and act.date.date() < start_date:
            continue
        if end_date and act.date.date() > end_date:
            continue
        for tile in act.tiles:
            rec=db.setdefault(tile,{'first_visit':act.date,'last_visit':act.date,'visit_count':0})
            rec['visit_count']+=1
            rec['first_visit']=min(rec['first_visit'],act.date)
            rec['last_visit']=max(rec['last_visit'],act.date)
    return db
