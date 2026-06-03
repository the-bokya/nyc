"""Tasks — async operations run inside guest VMs.

POST /vms/{vm_id}/tasks  — enqueue a task (async, poll via GET).
GET  /tasks              — list all tasks.
GET  /tasks/{id}         — task status + result.
"""
import json
import uuid
from datetime import datetime, timezone

from dadar.api.deps import get_client
from dadar.orm import Client
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from nyc.tables import Tasks, Vms

router = APIRouter()


class TaskIn(BaseModel):
    type: str
    params: dict | None = None


@router.get("/tasks")
def list_tasks(client: Client = Depends(get_client)) -> list[dict]:
    return [t.__dict__ for t in Tasks(client).docs.get_all()]


@router.get("/tasks/{task_id}")
def get_task(task_id: str, client: Client = Depends(get_client)) -> dict:
    row = Tasks(client).docs.get(where={"id": task_id})
    if row is None:
        raise HTTPException(404, "task not found")
    return row.__dict__


@router.post("/vms/{vm_id}/tasks", status_code=201)
def create_task(vm_id: str, body: TaskIn,
                client: Client = Depends(get_client)) -> dict:
    vm = Vms(client).docs.get(where={"id": vm_id})
    if vm is None:
        raise HTTPException(404, "vm not found")
    owner = vm.__dict__["node_id"]
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": str(uuid.uuid4()),
        "node_id": owner,
        "vm_id": vm_id,
        "type": body.type,
        "params": json.dumps(body.params) if body.params else None,
        "status": "pending",
        "result": None,
        "created_at": now,
        "updated_at": now,
    }
    Tasks(client).docs.insert(row)
    return row
