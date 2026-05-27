"""Pick a free host IP from a VPC CIDR + helpers around CIDR math."""
import ipaddress


def pick_ip(cidr: str, used: set[str]) -> str:
    net = ipaddress.ip_network(cidr, strict=True)
    for host in net.hosts():
        ip = str(host)
        if ip != gateway(cidr) and ip not in used:
            return ip
    raise RuntimeError(f"VPC {cidr} is full")


def gateway(cidr: str) -> str:
    net = ipaddress.ip_network(cidr, strict=True)
    return str(next(net.hosts()))


def netmask(cidr: str) -> str:
    return str(ipaddress.ip_network(cidr, strict=True).netmask)


def gateway_cidr(cidr: str) -> str:
    return f"{gateway(cidr)}/{cidr.split('/')[1]}"
