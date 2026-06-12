def build_tile_database(activities):
    db={}
    for act in activities:
        if act.activity_type!='Run':
            continue
        for tile in act.tiles:
            rec=db.setdefault(tile,{'first_visit':act.date,'last_visit':act.date,'visit_count':0})
            rec['visit_count']+=1
            rec['first_visit']=min(rec['first_visit'],act.date)
            rec['last_visit']=max(rec['last_visit'],act.date)
    return db
