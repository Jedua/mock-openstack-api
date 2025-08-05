from fastapi import FastAPI, HTTPException, Request, Header, status, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import uuid, os, json

app = FastAPI(title="Mock OpenStack API (Persistent)")

DATA_DIR = "./mock_data"
os.makedirs(DATA_DIR, exist_ok=True)

def load_data(name, default):
    file = os.path.join(DATA_DIR, f"{name}.json")
    if os.path.exists(file):
        try:
            with open(file, "r") as f:
                return json.load(f)
        except Exception:
            return default
    return default

def save_data(name, data):
    file = os.path.join(DATA_DIR, f"{name}.json")
    with open(file, "w") as f:
        json.dump(data, f, indent=2)

# --- Mock Data (Persistent) ---
USERS = load_data("users", {
    "admin": {"password": "secret", "id": "user-1", "role": "admin", "domain": "default"},
    "demo":  {"password": "test",   "id": "user-2", "role": "user",  "domain": "default"},
})
TOKENS: Dict[str, str] = load_data("tokens", {})  # token: user_id
IMAGES = load_data("images", [
    {"id": str(uuid.uuid4()), "name": "Cirros", "status": "active", "size": 13287936,
     "visibility": "public", "container_format": "bare", "disk_format": "qcow2",
     "created_at": "2024-08-01T00:00:00Z"}
])
VOLUMES = load_data("volumes", [
    {"id": str(uuid.uuid4()), "name": "vol-1", "size": 1, "status": "available"}
])
SERVERS = load_data("servers", [
    {"id": str(uuid.uuid4()), "name": "server-1", "status": "ACTIVE"}
])

def persist():
    save_data("users", USERS)
    save_data("tokens", TOKENS)
    save_data("images", IMAGES)
    save_data("volumes", VOLUMES)
    save_data("servers", SERVERS)

# --- Schemas para tipado ---

class AuthRequest(BaseModel):
    auth: Dict[str, Any]

class ImageIn(BaseModel):
    name: str
    size: Optional[int] = 0
    visibility: Optional[str] = "private"
    container_format: Optional[str] = "bare"
    disk_format: Optional[str] = "qcow2"

class VolumeIn(BaseModel):
    name: str
    size: int

class ServerIn(BaseModel):
    name: str
    image_id: str
    flavor_id: Optional[str] = None

# --- Middleware ---

async def require_token(x_auth_token: str = Header(None)):
    if not x_auth_token or x_auth_token not in TOKENS:
        raise HTTPException(status_code=401, detail="Invalid or missing token")

# --- AUTH (OpenStack Style) ---
@app.post("/v3/auth/tokens")
async def get_token(req: Request):
    data = await req.json()
    # OpenStack expects a big nested dict
    try:
        username = data["auth"]["identity"]["password"]["user"]["name"]
        password = data["auth"]["identity"]["password"]["user"]["password"]
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed authentication body")
    user = USERS.get(username)
    if not user or user["password"] != password:
        raise HTTPException(status_code=401, detail="Bad credentials")
    token = str(uuid.uuid4())
    TOKENS[token] = user["id"]
    persist()
    # OpenStack returns token in header X-Subject-Token
    response = JSONResponse(content={
        "token": token,
        "user": {"id": user["id"], "name": username, "role": user["role"]},
        "project": {"id": "mock-project", "name": "MockProject"}
    })
    response.headers["X-Subject-Token"] = token
    return response

# --- IMAGES ---
@app.get("/v2/images")
def list_images(token=Depends(require_token)):
    return {
        "images": [
            {
                "id": img["id"], "name": img["name"], "status": img["status"],
                "size": img["size"], "visibility": img.get("visibility", "public"),
                "container_format": img.get("container_format", "bare"),
                "disk_format": img.get("disk_format", "qcow2"),
                "created_at": img.get("created_at", "2024-08-01T00:00:00Z"),
                "links": [{"rel": "self", "href": f"/v2/images/{img['id']}"}]
            }
            for img in IMAGES
        ]
    }

@app.post("/v2/images", status_code=201)
async def create_image(img: ImageIn, token=Depends(require_token)):
    new_img = {
        "id": str(uuid.uuid4()), "name": img.name, "status": "queued", "size": img.size,
        "visibility": img.visibility, "container_format": img.container_format,
        "disk_format": img.disk_format, "created_at": "2024-08-01T00:00:00Z"
    }
    IMAGES.append(new_img)
    persist()
    return new_img

@app.get("/v2/images/{image_id}")
def get_image(image_id: str, token=Depends(require_token)):
    for img in IMAGES:
        if img["id"] == image_id:
            return img
    raise HTTPException(status_code=404, detail="Image not found")

# --- VOLUMES ---
@app.get("/v3/volumes")
def list_volumes(token=Depends(require_token)):
    return {"volumes": VOLUMES}

@app.post("/v3/volumes", status_code=201)
async def create_volume(vol: VolumeIn, token=Depends(require_token)):
    new_vol = {"id": str(uuid.uuid4()), "name": vol.name, "size": vol.size, "status": "available"}
    VOLUMES.append(new_vol)
    persist()
    return new_vol

@app.get("/v3/volumes/{volume_id}")
def get_volume(volume_id: str, token=Depends(require_token)):
    for vol in VOLUMES:
        if vol["id"] == volume_id:
            return vol
    raise HTTPException(status_code=404, detail="Volume not found")

# --- SERVERS ---
@app.get("/v2.1/servers")
def list_servers(token=Depends(require_token)):
    return {"servers": SERVERS}

@app.post("/v2.1/servers", status_code=202)
async def create_server(srv: ServerIn, token=Depends(require_token)):
    new_srv = {
        "id": str(uuid.uuid4()), "name": srv.name, "status": "BUILD",
        "image_id": srv.image_id, "flavor_id": srv.flavor_id
    }
    SERVERS.append(new_srv)
    persist()
    return new_srv

@app.get("/v2.1/servers/{server_id}")
def get_server(server_id: str, token=Depends(require_token)):
    for srv in SERVERS:
        if srv["id"] == server_id:
            return srv
    raise HTTPException(status_code=404, detail="Server not found")

# --- LOGOUT (optional) ---
@app.post("/v3/auth/logout")
def logout(x_auth_token: str = Header(None)):
    TOKENS.pop(x_auth_token, None)
    persist()
    return {"detail": "Logged out"}
