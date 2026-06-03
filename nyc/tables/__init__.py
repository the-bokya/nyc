from nyc.tables.vpcs import Vpcs
from nyc.tables.volumes import Volumes
from nyc.tables.vms import Vms
from nyc.tables.snapshots import Snapshots
from nyc.tables.public_ips import PublicIps
from nyc.tables.domains import Domains
from nyc.tables.tasks import Tasks
from nyc.tables.proxies import Proxies

ALL = [Vpcs, Volumes, Vms, Snapshots, PublicIps, Domains, Tasks, Proxies]

__all__ = ["Vpcs", "Volumes", "Vms", "Snapshots", "PublicIps", "Domains", "Tasks", "Proxies", "ALL"]
