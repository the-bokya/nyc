from dadar.api.deps import get_client, get_node_id
from dadar.orm import Client
from fastapi import APIRouter, Depends

from nyc.reconciler.pass_once import run as reconcile_once

router = APIRouter()


@router.post("/reconcile")
def reconcile_now(client: Client = Depends(get_client),
                  node_id: str = Depends(get_node_id)) -> dict:
    report = reconcile_once(client, node_id)
    return {"node_id": node_id, **report}
