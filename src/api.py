from fastapi import FastAPI
app = FastAPI(title="StatsHunters Route Planner")

@app.get("/api/health")
def health():
    return {"status":"ok"}
