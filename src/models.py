from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class Tile:
    x:int
    y:int

@dataclass
class Activity:
    id:int
    date:datetime
    distance_km:float
    activity_type:str
    tiles:list
