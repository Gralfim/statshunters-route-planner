from load import load_activities
from tiles import build_tile_database
from frontier import frontier_tiles

acts=load_activities('../data')
db=build_tile_database(acts)
print('Run tiles:', len(db))
print('Frontier tiles:', len(frontier_tiles(db)))
