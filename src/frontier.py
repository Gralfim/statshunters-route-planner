def frontier_tiles(tile_db):
    occupied={(t.x,t.y) for t in tile_db}
    frontier=set()
    for t in tile_db:
        for n in ((t.x+1,t.y),(t.x-1,t.y),(t.x,t.y+1),(t.x,t.y-1)):
            if n not in occupied:
                frontier.add(n)
    return frontier
