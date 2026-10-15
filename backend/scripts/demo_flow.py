"""Phase 1 checkpoint: run the whole money flow against your local server.

1. Start the server:   uvicorn app.main:app --reload
2. Run:                python -m scripts.demo_flow you-worker@personal.example.com
3. Open the approve link, log in with your sandbox PERSONAL (funder) account, approve.
4. Press Enter here; then upload proof via http://localhost:8000/docs (POST /milestones/{id}/proof).
"""
import sys

import httpx

API = "http://localhost:8000"
worker = sys.argv[1] if len(sys.argv) > 1 else "worker@example.com"

project = httpx.post(f"{API}/projects", json={
    "title": "Mangrove replanting - Kendrapara",
    "description": "Plant 500 mangrove saplings after the cyclone",
    "escrow_mode": "AUTH_CAPTURE",
    "site_lat": 20.5,   # TODO: set to where you'll take your test photo
    "site_lon": 86.4,
    "site_radius_m": 1000,
    "milestones": [
        {"title": "Buy saplings", "proof_description": "Saplings stacked at the nursery",
         "amount": 30, "worker_email": worker},
        {"title": "Plant saplings", "proof_description": "Saplings planted in mud along the shore",
         "amount": 40, "worker_email": worker},
        {"title": "Survival check", "proof_description": "Planted saplings alive with new leaves",
         "amount": 30, "worker_email": worker},
    ],
}).json()
print("Created project", project["id"])

fund = httpx.post(f"{API}/projects/{project['id']}/fund").json()
print("\nOpen this link and approve with your sandbox funder account:\n", fund["approve_url"])
input("\nPress Enter after approving... ")

print(httpx.get(f"{API}/projects/{project['id']}").json())
