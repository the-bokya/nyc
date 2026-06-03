from nyc.tables.vpcs import Vpcs
from nyc.tables.volumes import Volumes
from nyc.tables.vms import Vms
from nyc.tables.snapshots import Snapshots

ALL = [Vpcs, Volumes, Vms, Snapshots]

__all__ = ["Vpcs", "Volumes", "Vms", "Snapshots", "ALL"]
