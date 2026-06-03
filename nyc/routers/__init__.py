from nyc.routers.vpcs import router as vpcs_router
from nyc.routers.volumes import router as volumes_router
from nyc.routers.vms import router as vms_router
from nyc.routers.reconcile import router as reconcile_router
from nyc.routers.snapshots import snapshots as snapshots_router
from nyc.routers.snapshots import images as images_router
from nyc.routers.domains import router as domains_router
from nyc.routers.public_ips import router as public_ips_router
from nyc.routers.tasks import router as tasks_router
from nyc.routers.proxy import router as proxy_router

ALL = [vpcs_router, volumes_router, vms_router, reconcile_router,
       snapshots_router, images_router,
       domains_router, public_ips_router, tasks_router, proxy_router]

__all__ = ["vpcs_router", "volumes_router", "vms_router", "reconcile_router",
           "snapshots_router", "images_router",
           "domains_router", "public_ips_router", "tasks_router", "proxy_router",
           "ALL"]
