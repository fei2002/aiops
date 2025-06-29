import time
import uuid
import yaml
import os
from .k8s import *
from collections import deque
from kubernetes import client, config
from kubernetes.stream import stream
from flask import Blueprint, request, make_response, jsonify
from loguru import logger

from config.config import ignore_ns, custom_object_dict

try:
    config.load_kube_config()  # used when have kubeconfig file locally
except Exception as e:
    logger.warning("load kubeconfig failed: {}".format(e))
    config.load_incluster_config()  # used in pod


def get_next_avaliable_pod_name(node_kind: str, topology_docs: list):
    node_kind_st = ""
    index = 0
    if node_kind == "host":
        node_kind_st = "host"
        index = 4

    elif node_kind == "switch":
        node_kind_st = "sw"
        index = 2

    elif node_kind == "firewall":
        node_kind_st = "fw"
        index = 2

    elif node_kind == "router":
        node_kind_st = "r"
        index = 1

    existing_pods = set()
    for doc in topology_docs["items"]:
        if doc['kind'] == 'Topology' and doc['metadata']['name'].startswith(node_kind_st):
            try:
                pod_num = int(doc['metadata']['name'][index:])
                existing_pods.add(pod_num)
            except ValueError:
                continue
    
    pod_id = 1
    while pod_id in existing_pods:
        pod_id += 1
    
    return node_kind_st + str(pod_id)


def delete_peer_host_or_firewall_or_router_interface(pod: str, namespace: str, interface: str):
    try:
        command = ["ip", "link", "delete", interface]
        resp = stream(v1.connect_get_namespaced_pod_exec,
                        pod,
                        namespace,
                        container="pod",
                        command=command,
                        stderr=True, stdin=False,
                        stdout=True, tty=False)
    except Exception as e:
        logger.warning(f"Error deleting interface {interface} from {pod}: {str(e)}")
        return False
    logger.info(f"Successfully deleting OVS port {interface} from {pod}: {resp}")
    return True

    
def delete_peer_switch_ovs_br0_interface(switch_pod: str, namespace: str, switch_interface: str):
    try:
        command = ["ovs-vsctl", "del-port", "br0", switch_interface]
        resp = stream(v1.connect_get_namespaced_pod_exec,
                        switch_pod,
                        namespace,
                        container="pod",
                        command=command,
                        stderr=True, stdin=False,
                        stdout=True, tty=False)
    except Exception as e:
        logger.warning(f"Error deleting OVS port {switch_interface} from {switch_pod}: {str(e)}")
        return False
    logger.info(f"Successfully deleting OVS port {switch_interface} from {switch_pod}: {resp}")
    return True


def add_peer_firewall_br0_interface(firewall_pod: str, namespace: str, interface: str):
    time.sleep(3)  # 防止veth_pair的一端接口还没绑定到pod的网络命名空间，就将该端口绑到br0上
    add_cmd = ["brctl", "addif", "br0", interface]
    try:
        resp = stream(
            v1.connect_get_namespaced_pod_exec,
            firewall_pod,
            namespace,
            container="pod",
            command=add_cmd,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
            )
    except Exception as e:
        logger.warning(f"Error deleting port {interface} from {firewall_pod}: {str(e)}")
        return False
    logger.info(f"Successfully deleting port {interface} from {firewall_pod}: {resp}")
    return True


def add_peer_switch_ovs_br0_interface(switch_pod: str, namespace: str, switch_interface: str):
    check_cmd = ["ovs-vsctl", "list-ports", "br0"]
    add_cmd = ["ovs-vsctl", "add-port", "br0", switch_interface]
    max_retries = 5

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Attempt {attempt}/{max_retries}: Adding {switch_interface} to br0")
            resp = stream(
                v1.connect_get_namespaced_pod_exec,
                switch_pod,
                namespace,
                container="pod",
                command=add_cmd,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False
            )
            logger.debug(f"Add port response: {resp}")

            # 检查端口是否实际存在
            for _ in range(3):
                check_resp = stream(
                    v1.connect_get_namespaced_pod_exec,
                    switch_pod,
                    namespace,
                    command=check_cmd,
                    stderr=True,
                    stdin=False,
                    stdout=True,
                    tty=False
                )
                if switch_interface in check_resp:
                    logger.info(f"Interface {switch_interface} successfully added to br0")
                    return True
                time.sleep(1)

            logger.warning(f"Interface {switch_interface} not found in br0 after add command")

        except Exception as e:
            logger.warning(f"Attempt {attempt} failed: {str(e)}")
            if attempt == max_retries:
                raise RuntimeError(f"Failed to add interface {switch_interface} to br0 after {max_retries} attempts") from e

        sleep_time = min(2 ** (attempt // 2), 30)
        logger.info(f"Retrying in {sleep_time} seconds...")
        time.sleep(sleep_time)

    raise RuntimeError(f"Failed to verify interface {switch_interface} in br0 after {max_retries} attempts")


def add_default_route_ip_for_host(namespace: str, pod_name: str, swname: str, default_route_ip: str):
    # 先判断host里接口是否存在
    timeout = 30
    start_time = time.time()
    exist = False
    while time.time() - start_time < timeout:
        try:
            check_cmd = ["ip", "link", "show", f"{pod_name}_{swname}"]
            resp = stream(
                v1.connect_get_namespaced_pod_exec,
                pod_name,
                namespace,
                container="pod",
                command=check_cmd,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False
            )

            if resp.startswith("ip"):
                time.sleep(1)
                logger.warning(f"interface {pod_name}_{swname} does not exist, continue checking...")
            else:
                exist = True
                break
        except Exception as e:
            print(f"Error checking interface: {e}")
            time.sleep(1)
    
    if exist == False:
        logger.warning(f"interface {pod_name}_{swname} does not exist")
        return False
    
    try:
        command = [
            "ip", 
            "route", 
            "add", 
            f"{default_route_ip.split('.')[0]}.{default_route_ip.split('.')[1]}.0.0/16", 
            "via", 
            default_route_ip.split('/')[0]
        ]
        
        resp = stream(
            v1.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            container="pod",
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
    
    except Exception as e:
        logger.warning(f"Error adding default route ip for {pod_name}: {resp}")
        return False
    
    logger.info(f"Successfully adding default route ip for {pod_name}: {resp}")
    return True


def get_next_avaliable_uid(topology_docs: dict):
    existing_uids = []
    for doc in topology_docs["items"]:
        for link in doc['spec']['links']:
            existing_uids.append(link['uid'])
    
    uid = 1
    while uid in existing_uids:
        uid += 1

    return uid


def get_next_avaliable_subnet_id(topology_docs: dict):
    existing_subnet_id = []

    for doc in topology_docs["items"]:
        if doc['metadata']['name'].startswith('r') or doc['metadata']['name'].startswith('sw'):
            for link in doc['spec']['links']:
                if "local_ip" in link.keys():
                    if link["local_ip"]:
                        subnet_id = int(link["local_ip"].split('.')[2])
                        if subnet_id not in existing_subnet_id:
                            existing_subnet_id.append(subnet_id)
                if "peer_ip" in link.keys():
                    if link["peer_ip"]:
                        subnet_id = int(link["peer_ip"].split('.')[2])
                        if subnet_id not in existing_subnet_id:
                            existing_subnet_id.append(subnet_id)
    
    # 获取最小未使用子网号
    id = 1
    while id in existing_subnet_id:
        id += 1

    return id


# 获取默认路由
def find_router_ip(topology_docs: list, start_switch: str):
    visited = set()
    queue = deque()
    queue.append(start_switch)
    
    while queue:
        current = queue.popleft()
        visited.add(current)
        
        # Find the topology doc for current node
        current_doc = None
        for doc in topology_docs["items"]:
            if doc['kind'] == 'Topology' and doc['metadata']['name'] == current:
                current_doc = doc
                break
        
        if not current_doc:
            continue
            
        if current.startswith('r'):
            for link in current_doc['spec']['links']:
                if 'local_ip' in link and link['peer_pod'] in visited:
                    return link['local_ip'].split('/')[0]

            return None
        
        # If not a router, add all connected nodes to queue
        for link in current_doc['spec']['links']:
            peer = link['peer_pod']
            if peer not in visited:
                queue.append(peer)
    
    return None


# 获取子网内所有已使用的主机号
def get_next_avaliable_host_number_in_subnet(topology_docs: list, start_switch: str, target_subnet: str):
    visited = set()
    queue = deque()
    queue.append(start_switch)
    host_ips = set()
    
    while queue:
        current = queue.popleft()
        visited.add(current)
        
        current_doc = None
        for doc in topology_docs["items"]:
            if doc['kind'] == 'Topology' and doc['metadata']['name'] == current:
                current_doc = doc
                break
        
        if not current_doc:
            continue
            
        # Check all links for IPs in target subnet
        for link in current_doc['spec'].get('links', []):
            if 'local_ip' in link:
                ip = link['local_ip'].split('/')[0]
                if ip.startswith(target_subnet + '.'):
                    host_ips.add(int(ip.split('.')[-1]))
            
            if 'peer_ip' in link:
                ip = link['peer_ip'].split('/')[0]
                if ip.startswith(target_subnet + '.'):
                    host_ips.add(int(ip.split('.')[-1]))
            
            peer = link['peer_pod']
            if peer not in visited and (peer.startswith('sw') or peer.startswith('fw')):
                queue.append(peer)
    
    host_number = 1
    while host_number in host_ips:
        host_number += 1

    return host_number


def add_connection(namespace: str, pod1: str, pod2: str):
    logger.info(f"pod1: {pod1}, pod2: {pod2}")
    # 禁止向路由器-主机、主机-主机、防火墙-主机间添加连接
    if (pod1.startswith('host') and pod2.startswith('r')) or (pod1.startswith('r') and pod2.startswith('host')) or (pod1.startswith('host') and pod2.startswith('host')) or (pod1.startswith('host') and pod2.startswith('fw')) or (pod1.startswith('fw') and pod2.startswith('host')):
        return False
    
    topology_docs = load_topology_yaml(namespace=namespace)
    new_uid = get_next_avaliable_uid(topology_docs=topology_docs)
    logger.info(f"Next avaliable uid: {new_uid}")
    pod1_doc = None
    pod2_doc = None
    for doc in topology_docs["items"]:
        if doc['kind'] == 'Topology' and doc['metadata']['name'] == pod1:
            pod1_doc = doc
        if doc['kind'] == 'Topology' and doc['metadata']['name'] == pod2:
            pod2_doc = doc
        if pod1_doc != None and pod2_doc != None:
            break

    pod1_dict = {
        'uid': new_uid,
        'peer_pod': pod2,
        'local_intf': f"{pod1}_{pod2}",
        'peer_intf': f"{pod2}_{pod1}",
        'local_ip': "",
        'peer_ip': "",        
    }
    pod2_dict = {
        'uid': new_uid,
        'peer_pod': pod1,
        'local_intf': f"{pod2}_{pod1}",
        'peer_intf': f"{pod1}_{pod2}",
        'local_ip': "",
        'peer_ip': "",        
    }

    default_route = None
    if pod1.startswith('host') or pod2.startswith('host'):
        if pod1.startswith('host'):
            start_switch = pod2 
        else:
            start_switch = pod1
        default_route = find_router_ip(topology_docs=topology_docs, start_switch=start_switch)
        logger.info(f"Default_route: {default_route}")
        if not default_route:  # 此时即将与该host相连的sw不存在到路由器的路径，找不到默认路由
            return False
        
        subnet = '.'.join(default_route.split('.')[:3])

        host_part = get_next_avaliable_host_number_in_subnet(topology_docs=topology_docs, start_switch=start_switch, target_subnet=subnet)
        host_ip = f"{subnet}.{host_part}/24"
        logger.info(f"Host_ip: {host_ip}")
        if pod1.startswith('host'):
            pod1_dict['local_ip'] = host_ip
            pod2_dict['peer_ip'] = host_ip
        else:
            pod1_dict['peer_ip'] = host_ip
            pod2_dict['local_ip'] = host_ip
    
    elif (pod1.startswith('r') and pod2.startswith('sw')) or (pod2.startswith('r') and pod1.startswith('sw')):
        new_subnet_id = get_next_avaliable_subnet_id(topology_docs=topology_docs)
        subnet_prefix = f"10.12.{new_subnet_id}"
        r_ip = f"{subnet_prefix}.1/24"
        if pod1.startswith('r'):
            pod1_dict['local_ip'] = r_ip
            pod2_dict['peer_ip'] = r_ip
        elif pod2.startswith('r'):
            pod1_dict['peer_ip'] = r_ip
            pod2_dict['local_ip'] = r_ip

    elif (pod1.startswith('r') and pod2.startswith('fw')) or (pod2.startswith('r') and pod1.startswith('fw')):
        new_subnet_id = get_next_avaliable_subnet_id(topology_docs=topology_docs)
        subnet_prefix = f"10.12.{new_subnet_id}"
        r_ip = f"{subnet_prefix}.1/24"
        if pod1.startswith('r'):
            pod1_dict['local_ip'] = r_ip
            pod2_dict['peer_ip'] = r_ip
        elif pod2.startswith('r'):
            pod1_dict['peer_ip'] = r_ip
            pod2_dict['local_ip'] = r_ip

    elif pod1.startswith('r') and pod2.startswith('r'):
        new_subnet_id = get_next_avaliable_subnet_id(topology_docs=topology_docs)
        subnet_prefix = f"10.12.{new_subnet_id}"
        r1_ip = f"{subnet_prefix}.1/24"
        r2_ip = f"{subnet_prefix}.2/24"
        pod1_dict['local_ip'] = r1_ip
        pod1_dict['peer_ip'] = r2_ip
        pod2_dict['local_ip'] = r2_ip
        pod2_dict['peer_ip'] = r1_ip

    pod1_doc['spec']['links'].append(pod1_dict)
    pod2_doc['spec']['links'].append(pod2_dict)
    update_topology_item_link(namespace=namespace, topology=pod1_doc, name=pod1_doc['metadata']['name'])
    update_topology_item_link(namespace=namespace, topology=pod2_doc, name=pod2_doc['metadata']['name'])
    time.sleep(1)
    logger.info("Topology items update complete.")
    
    add_connection_service(PodName1=pod1, IfName1=f"{pod1}_{pod2}", Ip1=pod1_dict['local_ip'], PodName2=pod2, IfName2=f"{pod2}_{pod1}", Ip2=pod2_dict['local_ip'], Namespace=namespace)
    time.sleep(3)

    if pod1.startswith('sw'):
        add_peer_switch_ovs_br0_interface(switch_pod=pod1, namespace=namespace, switch_interface=pod1_dict['local_intf'])
    elif pod1.startswith('host'):
        add_default_route_ip_for_host(namespace=namespace, pod_name=pod1, swname=pod2, default_route_ip=default_route)
    elif pod1.startswith('fw'):
        add_peer_firewall_br0_interface(firewall_pod=pod1, namespace=namespace, interface=pod1_dict['local_intf'])

    if pod2.startswith('sw'):
        add_peer_switch_ovs_br0_interface(switch_pod=pod2, namespace=namespace, switch_interface=pod2_dict['local_intf'])
    elif pod2.startswith('host'):
        add_default_route_ip_for_host(namespace=namespace, pod_name=pod2, swname=pod1, default_route_ip=default_route)
    elif pod1.startswith('fw'):
        add_peer_firewall_br0_interface(firewall_pod=pod2, namespace=namespace, interface=pod2_dict['local_intf'])
    return True


def delete_connection(namespace: str, pod1: str, pod2: str):
    pod1_interface = f"{pod1}_{pod2}"
    pod2_interface = f"{pod2}_{pod1}"
    if pod1.startswith("sw"):
        delete_peer_switch_ovs_br0_interface(switch_pod=pod1, namespace=namespace, switch_interface=pod1_interface)
    else:
        delete_peer_host_or_firewall_or_router_interface(pod=pod1, namespace=namespace, interface=pod1_interface)
    
    time.sleep(1)

    if pod2.startswith("sw"):
        delete_peer_switch_ovs_br0_interface(switch_pod=pod2, namespace=namespace, switch_interface=pod2_interface)
    else:
        delete_peer_host_or_firewall_or_router_interface(pod=pod2, namespace=namespace, interface=pod2_interface)

    topology_docs = load_topology_yaml(namespace=namespace)
    for doc in topology_docs["items"]:
        if doc['kind'] == 'Topology' and doc['metadata']['name'] in [pod1, pod2]:
            for link in doc['spec']['links']:
                if link['peer_pod'] in [pod1, pod2]:
                    doc['spec']['links'].remove(link)
                    update_topology_item_link(namespace=namespace, topology=doc, name=doc['metadata']['name'])

    logger.info(f"Successfully delete connection between {pod1}-{pod2}")
    return True


def add_host(namespace: str, switchname: str,hostname: str=None):
    topology_docs = load_topology_yaml(namespace=namespace)
    if not hostname:
        hostname = get_next_avaliable_pod_name(node_kind="host", topology_docs=topology_docs)
    default_route = find_router_ip(topology_docs=topology_docs, start_switch=switchname)
    if not default_route:
        raise ValueError(f"Could not find a router connected to switch {switchname}")

    subnet = '.'.join(default_route.split('.')[:3])
    host_part = get_next_avaliable_host_number_in_subnet(topology_docs=topology_docs, start_switch=switchname, target_subnet=subnet)
    host_ip = f"{subnet}.{host_part}/24"
    
    host_pod_yaml = generate_host_pod_yaml(hostname=hostname, default_route=default_route)
    new_uid = get_next_avaliable_uid(topology_docs=topology_docs)

    for doc in topology_docs["items"]:
        if doc['kind'] == 'Topology' and doc['metadata']['name'] == switchname:
            doc['spec']['links'].append({
                'uid': new_uid,
                'peer_pod': hostname,
                'local_intf': f"{switchname}_{hostname}",
                'peer_intf': f"{hostname}_{switchname}",
                'peer_ip': host_ip
            })
            update_topology_item_link(namespace=namespace, topology=doc, name=switchname)
            break
    
    host_doc = {
        'apiVersion': 'networkop.co.uk/v1beta1',
        'kind': 'Topology',
        'metadata': {
            'name': hostname
        },
        'spec': {
            'links': [{
                'uid': new_uid,
                'peer_pod': switchname,
                'local_intf': f"{hostname}_{switchname}",
                'peer_intf': f"{switchname}_{hostname}",
                'local_ip': host_ip
            }]
        }
    }

    add_topology_item(namespace=namespace, topology_item_doc=host_doc)
    add_pod(namespace=namespace, pod_name=hostname, pod_yaml=host_pod_yaml)
    host_pod_running = check_pod_running(namespace=namespace, pod_name=hostname)
    if host_pod_running:
        add_peer_switch_ovs_br0_interface(switch_pod=switchname, namespace=namespace, switch_interface=f"{switchname}_{hostname}")
        return hostname
    else:
        return None


def delete_host(namespace: str, hostname: str):
    logger.info(f"deleting pod {hostname}")
    load_config()
    topology_docs = load_topology_yaml(namespace=namespace)
    delete_topology_item(namespace=namespace, name=hostname)
    time.sleep(1)
    
    for doc in topology_docs["items"]:
        if doc['kind'] == 'Topology' and doc['metadata']['name'].startswith('sw'):          
            for link in doc['spec']['links']:
                if link['peer_pod'] == hostname:
                    doc['spec']['links'].remove(link)
                    update_topology_item_link(namespace=namespace, topology=doc, name=doc['metadata']['name'])
                    delete_peer_switch_ovs_br0_interface(switch_pod=doc['metadata']['name'], namespace=namespace, switch_interface=link['local_intf'])
    
    host_deleted = delete_pod(namespace=namespace, pod_name=hostname)
    if host_deleted:
        return True
    else:
        return False


def add_switch_for_switch(namespace: str, oldswitchname: str):
    topology_docs = load_topology_yaml(namespace=namespace)
    swname = get_next_avaliable_pod_name("switch", topology_docs=topology_docs)
    new_uid = get_next_avaliable_uid(topology_docs=topology_docs)

    for doc in topology_docs["items"]:
        if doc['kind'] == 'Topology' and doc['metadata']['name'] == oldswitchname:
            doc['spec']['links'].append({
                'uid': new_uid,
                'peer_pod': swname,
                'local_intf': f"{oldswitchname}_{swname}",
                'peer_intf': f"{swname}_{oldswitchname}"
            })
            update_topology_item_link(namespace=namespace, topology=doc, name=oldswitchname)
            break
    
    new_switch_doc = {
        'apiVersion': 'networkop.co.uk/v1beta1',
        'kind': 'Topology',
        'metadata': {
            'name': swname
        },
        'spec': {
            'links': [{
                'uid': new_uid,
                'peer_pod': oldswitchname,
                'local_intf': f"{swname}_{oldswitchname}",
                'peer_intf': f"{oldswitchname}_{swname}"
            }]
        }
    }
    add_topology_item(namespace=namespace, topology_item_doc=new_switch_doc)
    sw_pod_yaml = generate_switch_pod_yaml(swname=swname, peerpodname=oldswitchname)
    load_config()
    add_pod(namespace=namespace, pod_name=swname, pod_yaml=sw_pod_yaml)
    sw_pod_info = check_pod_running(namespace=namespace, pod_name=swname)

    if sw_pod_info:
        add_peer_switch_ovs_br0_interface(switch_pod=oldswitchname, namespace=namespace, switch_interface=f"{oldswitchname}_{swname}")
        return swname 
    else:
        return None


def add_switch_for_firewall(namespace: str, firewallname: str):
    topology_docs = load_topology_yaml(namespace=namespace)
    swname = get_next_avaliable_pod_name("switch", topology_docs=topology_docs)
    firwall_doc = None
    for doc in topology_docs["items"]:
        if doc['kind'] == 'Topology' and doc['metadata']['name'] == firewallname:
            firwall_doc = doc
            break

    new_uid = get_next_avaliable_uid(topology_docs=topology_docs)
    firwall_doc['spec']['links'].append({
        'uid': new_uid,
        'peer_pod': swname,
        'local_intf': f"{firewallname}_{swname}",
        'peer_intf': f"{swname}_{firewallname}",
    })
    update_topology_item_link(namespace=namespace, topology=firwall_doc, name=firewallname)
    
    new_switch_doc = {
        'apiVersion': 'networkop.co.uk/v1beta1',
        'kind': 'Topology',
        'metadata': {
            'name': swname
        },
        'spec': {
            'links': [{
                'uid': new_uid,
                'peer_pod': firewallname,
                'local_intf': f"{swname}_{firewallname}",
                'peer_intf': f"{firewallname}_{swname}",
            }]
        }
    }
    add_topology_item(namespace=namespace, topology_item_doc=new_switch_doc)

    new_pod_yaml = generate_switch_pod_yaml(swname=swname, peerpodname=firewallname)
    add_pod(namespace=namespace, pod_name=swname, pod_yaml=new_pod_yaml)
    sw_pod_info = check_pod_running(namespace=namespace, pod_name=swname)
    if sw_pod_info:
        add_peer_firewall_br0_interface(firewall_pod=firewallname, namespace=namespace, interface=f"{firewallname}_{swname}")
        return swname 
    else:
        logger.warning(f"Error creating pod {swname}")
        return None


def add_switch_for_router(namespace: str, routername: str,swname=""):
    topology_docs = load_topology_yaml(namespace=namespace)
    if not swname:
        swname = get_next_avaliable_pod_name("switch", topology_docs=topology_docs)
    
    route_doc = None
    for doc in topology_docs["items"]:
        if doc['kind'] == 'Topology' and doc['metadata']['name'] == routername:
            route_doc = doc
            break
    
    new_uid = get_next_avaliable_uid(topology_docs=topology_docs)
    subnet_id = get_next_avaliable_subnet_id(topology_docs=topology_docs)
    r_ip = f"10.12.{subnet_id}.1/24"

    route_doc['spec']['links'].append({
        'uid': new_uid,
        'peer_pod': swname,
        'local_intf': f"{routername}_{swname}",
        'peer_intf': f"{swname}_{routername}",
        'local_ip': r_ip
    })
    update_topology_item_link(namespace=namespace, topology=route_doc, name=routername)

    new_switch_doc = {
        'apiVersion': 'networkop.co.uk/v1beta1',
        'kind': 'Topology',
        'metadata': {
            'name': swname
        },
        'spec': {
            'links': [{
                'uid': new_uid,
                'peer_pod': routername,
                'local_intf': f"{swname}_{routername}",
                'peer_intf': f"{routername}_{swname}",
                'peer_ip': r_ip
            }]
        }
    }
    add_topology_item(namespace=namespace, topology_item_doc=new_switch_doc)

    new_pod_yaml = generate_switch_pod_yaml(swname=swname, peerpodname=routername)
    load_config()
    add_pod(namespace=namespace, pod_name=swname, pod_yaml=new_pod_yaml)
    sw_pod_info = check_pod_running(namespace=namespace, pod_name=swname)
    if sw_pod_info:
        return swname 
    else:
        logger.warning(f"Error creating pod {swname}")
        return None
    

def reboot_switch(namespace: str, switchname: str):
    load_config()
    topology_docs = load_topology_yaml(namespace=namespace)

    sw_new_yaml = generate_switch_pod_yaml(swname=switchname, peerpodname="")

    # 获取该sw所有的对端设备名，和对应接口名
    sw_doc = None
    peer_pod_interfaces = {}
    default_ip = {} # 需要额外为对端主机配默认路由

    for doc in topology_docs["items"]:
        if doc['kind'] == 'Topology' and doc['metadata']['name'] == switchname:
            sw_doc = doc
            break
    
    command = ['/bin/bash', '-c']
    cmd_str = '/usr/share/openvswitch/scripts/ovs-ctl start && ovs-vsctl add-br br0'
    if sw_doc:
        for link in sw_doc['spec']['links']:
            peer_pod_interfaces[link['peer_pod']] = link['peer_intf']
            own_interface_name = link['local_intf']
            cmd_str += f' && ovs-vsctl add-port br0 {own_interface_name}'
            if link['peer_pod'].startswith("host"):
                ip_part, cidr = link['peer_ip'].split('/')
                octets = ip_part.split('.')
                octets[-1] = '1'
                default_ip[link['peer_pod']] = f"{'.'.join(octets)}/{cidr}"
        cmd_str += ' && sleep infinity' 
        command.append(cmd_str)
        sw_new_yaml['spec']['containers'][0]['command'] = command
    else:
        return False

    # 删除该sw所有对端设备对应的接口
    for pod_name, interface_name in peer_pod_interfaces.items():
        if pod_name.startswith("sw"):
            delete_peer_switch_ovs_br0_interface(switch_pod=pod_name, namespace=namespace, switch_interface=interface_name)
        else:
            delete_peer_host_or_firewall_or_router_interface(pod=pod_name, namespace=namespace, interface=interface_name)

    sw_deleted = delete_pod(namespace=namespace, pod_name=switchname)
    if sw_deleted:
        add_pod(namespace=namespace, pod_name=switchname, pod_yaml=sw_new_yaml)

    sw_pod_running = check_pod_running(namespace=namespace, pod_name=switchname) 
    
    if sw_pod_running:
        time.sleep(3)
        for pod_name, interface_name in peer_pod_interfaces.items():
            if pod_name.startswith("host"):
                add_default_route_ip_for_host(namespace=namespace, pod_name=pod_name, swname=switchname, default_route_ip=default_ip[pod_name])

            elif pod_name.startswith("sw"):
                add_peer_switch_ovs_br0_interface(switch_pod=pod_name, namespace=namespace, switch_interface=peer_pod_interfaces[pod_name])

        logger.info(f"{switchname} reboot complete")
        return True
    else:
        logger.warning(f"{switchname} does not run in time")
        return False
    

def reboot_switch_ovs_service(namespace: str, switchname: str):
    try:
        command = ["/usr/share/openvswitch/scripts/ovs-ctl", "restart"]
        resp = stream(v1.connect_get_namespaced_pod_exec,
                        switchname,
                        namespace,
                        container="pod",
                        command=command,
                        stderr=True, stdin=False,
                        stdout=True, tty=False)
    except Exception as e:
        logger.warning(f"Error restarting ovs service: {str(e)}")
        return False
    logger.info(f"Successfully restarting ovs service.")
    return True


def delete_switch(namespace: str, switchname: str):
    logger.info(f"deleting pod {switchname}")
    load_config()
    topology_docs = load_topology_yaml(namespace=namespace)
    delete_topology_item(namespace=namespace, name=switchname)
    time.sleep(1)

    for doc in topology_docs["items"]:
        for link in doc['spec']['links']:
            if link['peer_pod'] == switchname:
                doc['spec']['links'].remove(link)
                update_topology_item_link(namespace=namespace, topology=doc, name=doc['metadata']['name'])
                if doc['metadata']['name'].startswith("sw"):
                    delete_peer_switch_ovs_br0_interface(switch_pod=doc['metadata']['name'], namespace=namespace, switch_interface=link['local_intf'])
                else:
                    delete_peer_host_or_firewall_or_router_interface(pod=doc['metadata']['name'], namespace=namespace, interface=link['local_intf'])

    sw_delete = delete_pod(namespace=namespace, pod_name=switchname)
    if sw_delete:
        return True
    else:
        return False

# 创建单个路由
def add_single(namespace: str, routernames: str,switchnames:str,hostnames:str):
    if routernames:
        routernames=routernames.split(",")
        for routername in routernames:
            new_r_pod_yaml = generate_router_pod_yaml(routername=routername)
            new_router_topology = {
                'apiVersion': 'networkop.co.uk/v1beta1',
                'kind': 'Topology',
                'metadata': {
                    'name': routername
                },
                'spec': {
                    'links': [{
                        'uid': 999,  # 唯一标识
                        'peer_pod': routername,  # 对端是自己
                        'local_intf': "lo0",  # 虚拟接口
                        'peer_intf': "lo0",  # 虚拟接口
                        'local_ip': "127.0.0.1/8",  # 本地回环地址
                        'peer_ip': "127.0.0.1/8"  # 本地回环地址
                    }]
                }
            }
            add_topology_item(namespace=namespace, topology_item_doc=new_router_topology)
            delete_connection(namespace=namespace,pod1=routername,pod2=routername)
            try:
                add_pod(namespace=namespace, pod_name=routername, pod_yaml=new_r_pod_yaml)
            except Exception as e:
                return(f"Error adding new pod yaml: {str(e)},now ids:{routernames}")
            r_pod_running = check_pod_running(namespace=namespace, pod_name=routername)
            if not r_pod_running:
                return "False"

    if switchnames:
        routername="r1"
        switchnames = switchnames.split(",")
        for switchname in switchnames:
            add_switch_for_router(namespace,routername,switchname)
        time.sleep(7)
        for switchname in switchnames:
            delete_connection(namespace,routername,switchname)

    if hostnames:
        switchname="sw1"
        hostnames = hostnames.split(",")
        for hostname in hostnames:
            add_host(namespace,switchname,hostname)
        time.sleep(7)
        for hostname in hostnames:
            try:
                delete_connection(namespace,switchname,hostname)
            except Exception as e:
                return f'Error deleting host: {str(e)}'
    return "True"

def create_router_links(namespace: str, rr: str,rs:str):
    if rr:
        rr=rr.split(",")
        for sourceId,targetId in zip(rr[::2], rr[1::2]):
            success=add_connection(namespace,pod1=sourceId,pod2=targetId)
            if not success:
                return f"Error adding connection to pod {sourceId}:{targetId}"
    if rs:
        rs=rs.split(",")
        for sourceId,targetId in zip(rs[::2],rs[1::2]):
            success=add_connection(namespace,pod1=sourceId,pod2=targetId)
            if not success:
                return f"Error adding connection to router:{sourceId} with switch:{targetId}"
    return "True"

def create_switch_links(namespace:str,ss:str,sh:str):
    if ss:
        ss=ss.split(",")
        for sourceId, targetId in zip(ss[::2], ss[1::2]):
            success = add_connection(namespace, pod1=sourceId, pod2=targetId)
            if not success:
                return f"Error adding connection to switch:{sourceId} with switch:{targetId}"
    if sh:
        sh = sh.split(",")
        for sourceId, targetId in zip(sh[::2], sh[1::2]):
            success = add_connection(namespace, pod1=sourceId, pod2=targetId)
            if not success:
                return f"Error adding connection to switch:{sourceId} with host:{targetId}"
    return "True"
# 添加路由（命名空间，已存在路由）
def add_router(namespace: str, oldroutername: str):
    load_config()
    topology_docs = load_topology_yaml(namespace=namespace) # 加载拓扑文件

    new_uid = get_next_avaliable_uid(topology_docs=topology_docs) # 获取 link 的 uid？
    new_subnet_id = get_next_avaliable_subnet_id(topology_docs=topology_docs) # 获取可用子网的id
    new_routername = get_next_avaliable_pod_name(node_kind="router", topology_docs=topology_docs) # 获取下一个可用路由器的名称

    # 生成新子网+两端路由接口的ip
    subnet_prefix = f"10.12.{new_subnet_id}"
    old_router_ip = f"{subnet_prefix}.1/24"
    new_router_ip = f"{subnet_prefix}.2/24"
    interface_name = f"{oldroutername}_{new_routername}"
    peer_interface_name = f"{new_routername}_{oldroutername}"

    # config_map_yaml = generate_configmap_yaml(routername=new_routername, subnet_prefix=subnet_prefix, peer_interface_name=peer_interface_name)
    new_r_pod_yaml = generate_router_pod_yaml(routername=new_routername) # 生成新路由器Pod的yaml

    # 更新旧路由器的拓扑连接
    for doc in topology_docs["items"]:
        if doc['kind'] == 'Topology' and doc['metadata']['name'] == oldroutername: #找到旧路由器
            doc['spec']['links'].append({
                'uid': new_uid,
                'peer_pod': new_routername,
                'local_intf': interface_name,
                'peer_intf': peer_interface_name,
                'local_ip': old_router_ip,
                'peer_ip': new_router_ip
            })
            # 更新
            update_topology_item_link(namespace=namespace, topology=doc, name=oldroutername)
            break

    new_router_topology = {
        'apiVersion': 'networkop.co.uk/v1beta1',
        'kind': 'Topology',
        'metadata': {
            'name': new_routername
        },
        'spec': {
            'links': [{
                'uid': new_uid,
                'peer_pod': oldroutername,
                'local_intf': peer_interface_name,
                'peer_intf': interface_name,
                'local_ip': new_router_ip,
                'peer_ip': old_router_ip
            }]
        }
    }
    add_topology_item(namespace=namespace, topology_item_doc=new_router_topology)
    # create_configmap(namespace=namespace, config_map=config_map_yaml)
    add_pod(namespace=namespace, pod_name=new_routername, pod_yaml=new_r_pod_yaml)
    r_pod_running = check_pod_running(namespace=namespace, pod_name=new_routername)
    if r_pod_running:
        return new_routername
    return None


def delete_firewall(namespace: str, firewallname: str):
    logger.info(f"deleting pod {firewallname}")
    load_config()
    topology_docs = load_topology_yaml(namespace=namespace)
    delete_topology_item(namespace=namespace, name=firewallname)
    time.sleep(1)

    for doc in topology_docs["items"]:
        for link in doc['spec']['links']:
            if link['peer_pod'] == firewallname:
                doc['spec']['links'].remove(link)
                update_topology_item_link(namespace=namespace, topology=doc, name=doc['metadata']['name'])
                if doc['metadata']['name'].startswith("sw"):
                    delete_peer_switch_ovs_br0_interface(switch_pod=doc['metadata']['name'], namespace=namespace, switch_interface=link['local_intf'])
                else:
                    delete_peer_host_or_firewall_or_router_interface(pod=doc['metadata']['name'], namespace=namespace, interface=link['local_intf'])

    fw_delete = delete_pod(namespace=namespace, pod_name=firewallname)
    if fw_delete:
        return True
    else:
        return False


def add_firewall_for_router(namespace, routername):
    load_config()
    topology_docs = load_topology_yaml(namespace=namespace)

    route_doc = None
    for doc in topology_docs["items"]:
        if doc['kind'] == 'Topology' and doc['metadata']['name'] == routername:
            route_doc = doc
            break

    new_uid = get_next_avaliable_uid(topology_docs=topology_docs)
    new_firewallname = get_next_avaliable_pod_name(node_kind="firewall", topology_docs=topology_docs)
    subnet_id = get_next_avaliable_subnet_id(topology_docs=topology_docs)
    r_ip = f"10.12.{subnet_id}.1/24"

    route_doc['spec']['links'].append({
        'uid': new_uid,
        'peer_pod': new_firewallname,
        'local_intf': f"{routername}_{new_firewallname}",
        'peer_intf': f"{new_firewallname}_{routername}",
        'local_ip': r_ip
    })
    update_topology_item_link(namespace=namespace, topology=route_doc, name=routername)

    new_switch_doc = {
        'apiVersion': 'networkop.co.uk/v1beta1',
        'kind': 'Topology',
        'metadata': {
            'name': new_firewallname
        },
        'spec': {
            'links': [{
                'uid': new_uid,
                'peer_pod': routername,
                'local_intf': f"{new_firewallname}_{routername}",
                'peer_intf': f"{routername}_{new_firewallname}",
                'peer_ip': r_ip
            }]
        }
    }
    add_topology_item(namespace=namespace, topology_item_doc=new_switch_doc)

    new_pod_yaml = generate_firewall_pod_yaml(fwname=new_firewallname, peerpodname=routername)
    add_pod(namespace=namespace, pod_name=new_firewallname, pod_yaml=new_pod_yaml)
    fw_pod_info = check_pod_running(namespace=namespace, pod_name=new_firewallname)
    if fw_pod_info:
        return new_firewallname
    else:
        logger.warning(f"Error creating pod {new_firewallname}")
        return None
    

def reboot_router(namespace: str, routername: str):
    load_config()
    topology_docs = load_topology_yaml(namespace=namespace)

    r_origin_yaml = generate_router_pod_yaml(routername=routername)
    r_doc = None
    peer_pod_interfaces = {}

    for doc in topology_docs["items"]:
        if doc['kind'] == 'Topology' and doc['metadata']['name'] == routername:
            r_doc = doc
    
    if r_doc:
        for link in r_doc['spec']['links']:
            peer_pod_interfaces[link['peer_pod']] = link['peer_intf']
    else:
        return False

    # 删除该router所有对端设备对应的接口
    for pod_name, interface_name in peer_pod_interfaces.items():
        if pod_name.startswith("sw"):
            delete_peer_switch_ovs_br0_interface(switch_pod=pod_name, namespace=namespace, switch_interface=interface_name)
        else:
            delete_peer_host_or_firewall_or_router_interface(pod=pod_name, namespace=namespace, interface=interface_name)
            
    r_deleted = delete_pod(namespace=namespace, pod_name=routername)
    if r_deleted:
        add_pod(namespace=namespace, pod_name=routername, pod_yaml=r_origin_yaml)

    r_pod_running = check_pod_running(namespace=namespace, pod_name=routername) 
    
    if r_pod_running:
        time.sleep(3)
        for pod_name, interface_name in peer_pod_interfaces.items():
            if pod_name.startswith("sw"):
                add_peer_switch_ovs_br0_interface(switch_pod=pod_name, namespace=namespace, switch_interface=peer_pod_interfaces[pod_name])

    logger.info(f"{routername} reboot complete")
    return True


def delete_router(namespace: str, routername: str):
    logger.info(f"deleting pod {routername}")
    load_config()
    topology_docs = load_topology_yaml(namespace=namespace)
    delete_topology_item(namespace=namespace, name=routername)
    time.sleep(1)

    for doc in topology_docs["items"]:
        for link in doc['spec']['links']:
            if link['peer_pod'] == routername:
                doc['spec']['links'].remove(link)
                update_topology_item_link(namespace=namespace, topology=doc, name=doc['metadata']['name'])
                if doc['metadata']['name'].startswith("sw"):
                    delete_peer_switch_ovs_br0_interface(switch_pod=doc['metadata']['name'], namespace=namespace, switch_interface=link['local_intf'])
                else:
                    delete_peer_host_or_firewall_or_router_interface(pod=doc['metadata']['name'], namespace=namespace, interface=link['local_intf'])

    r_delete = delete_pod(namespace=namespace, pod_name=routername)
    if r_delete:
        # delete_configmap(namespace=namespace, routername=routername)
        return True
    else:
        return False
    

