from nyc.routers.vpcs import router as vpcs_router
from nyc.routers.volumes import router as volumes_router
from nyc.routers.vms import router as vms_router
from nyc.routers.reconcile import router as reconcile_router
from nyc.routers.snapshots import snapshots as snapshots_router
from nyc.routers.snapshots import images as images_router

ALL = [vpcs_router, volumes_router, vms_router, reconcile_router,
       snapshots_router, images_router]

__all__ = ["vpcs_router", "volumes_router", "vms_router", "reconcile_router",
           "snapshots_router", "images_router", "ALL"]
