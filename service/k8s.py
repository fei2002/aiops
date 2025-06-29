import time
import os
import yaml
from kubernetes import client, config, utils, watch
from kubernetes.client import ApiException, AppsV1Api
from kubernetes.utils import FailToCreateError
from kubernetes.stream import stream
from loguru import logger
from requests import HTTPError

from service.mongo import MongoConnectClient
import re
from datetime import datetime
from loguru import logger

mongo = MongoConnectClient(host="k8s.personai.cn", db="chaos", port=30332) 

from config.config import ignore_ns, custom_object_dict
from service.netservice import *
import grpc

from service.topology_vis import NetworkTopologyLayout
import networkx as nx

try:
    config.load_kube_config()  # used when have kubeconfig file locally
except Exception as e:
    logger.warning("load kubeconfig failed: {}".format(e))
    config.load_incluster_config()  # used in pod

v1 = client.CoreV1Api()
networkV1 = client.NetworkingV1Api()
k8s_client = client.ApiClient()
custom_api = client.CustomObjectsApi()
k8s_watch = watch.Watch()
rbac_api = client.RbacAuthorizationV1Api()


def create_serviceaccount_and_rolebinding_for_namespace(namespace: str):
    sa = client.V1ServiceAccount(metadata=client.V1ObjectMeta(name="generate-flow", namespace=namespace))
    v1.create_namespaced_service_account(namespace, sa)

    role_binding_name = f"generate-flow-rolebinding-{namespace}"

    body = client.V1RoleBinding(
        metadata=client.V1ObjectMeta(
            name=role_binding_name,
            namespace=namespace
        ),
        subjects=[
            client.V1Subject(
                kind="ServiceAccount",
                name="generate-flow",
                namespace=namespace
            )
        ],
        role_ref=client.V1RoleRef(
            kind="ClusterRole",
            name="generate-flow-controller-role", 
            api_group="rbac.authorization.k8s.io"
        )
    )

    try:
        rbac_api.create_namespaced_role_binding(namespace=namespace, body=body)
        logger.info(f"[+] RoleBinding '{role_binding_name}' created in namespace '{namespace}'")
    except client.exceptions.ApiException as e:
        if e.status == 409:
            logger.warning(f"[!] RoleBinding already exists in namespace '{namespace}'")
        else:
            logger.error(f"Error creating roleBinding in namespace {namespace}")
            return False
        
    return True    


def create_namespace(ns: str):
    # metadata = client.V1ObjectMeta(
    #    name = ns,
    #     创建testbed的namespace时istio自动注入  
    #    labels = {"istio-injection": "enabled"}
    #)
    ns_config = client.V1Namespace(metadata=client.V1ObjectMeta(name=ns)) # 创建配置
    v1.create_namespace(ns_config) # 创建命名空间
    logger.info("created namespace {}".format(ns))

def add_istioInjection(namespace_name : str) :
    # 定义要添加的标签
    new_labels = {"istio-injection" : "enabled"}

    # 获取命名空间对象
    namespace = v1.read_namespace(name=namespace_name)

    # 添加新的标签到现有标签中
    if namespace.metadata.labels is None:
        namespace.metadata.labels = {}

    namespace.metadata.labels.update(new_labels)

    # 创建包含标签的补丁对象
    namespace_patch = {"metadata": {"labels": namespace.metadata.labels}}

    # 更新命名空间，添加标签，打印日志
    v1.patch_namespace(name=namespace_name, body=namespace_patch)
    logger.info("istio injection namespace {}".format(namespace_name))    


def delete_namespace(ns: str):
    v1.delete_namespace(ns)
    logger.info("deleted namespace {}".format(ns))


def list_namespaces() -> list:
    ret = v1.list_namespace()
    name_list = []
    for item in ret.items:
        if item.metadata.name in ignore_ns:
            continue
        name_list.append(item.metadata.name)
    return name_list


def list_services(namespace: str) -> list:
    ret = v1.list_namespaced_service(namespace)
    service_list = []
    for item in ret.items:
        # logger.info("service: {}".format(item))
        port_list = []
        for port in list(item.spec.ports):
            port_list.append(port.port)
        info_dict = {
            "name": item.metadata.name,
            "namespace": item.metadata.namespace,
            "labels": item.metadata.labels,
            "type": item.spec.type,
            "ports": port_list
        }
        service_list.append(info_dict)
    return service_list


def list_pods(namespace: str) -> list:
    ret = v1.list_namespaced_pod(namespace)
    pod_list = []
    for item in ret.items:
        # 获取 Pod 的状态
        name = item.metadata.name
        phase = item.status.phase
        ready = False
        message = ""
        # 检查 Pod 是否处于 Ready 状态
        for c in item.status.conditions:
            # logger.info("condition: {}".format(c))
            if c.type == "Ready" and c.status == "True":
                ready = True
            if c.status == "False":
                message = "Condition: {}, Reason: {}, Message: {}".format(c.type, c.reason, c.message)
                break
        info_dict = {
            "name": name,
            "phase": phase,
            "ready": ready,
            "message": message,
            "labels": item.metadata.labels
        }
        pod_list.append(info_dict)
    return pod_list


def list_selectedTargetPodNames(selectedTarget: str, namespace: str) -> list:
    kind = ""
    if selectedTarget == "switch":
        kind = "sw"
    elif selectedTarget == "router":
        kind = "r"
    selectedTargetPodNames = []
    
    try:
        pods = v1.list_namespaced_pod(namespace)
        for pod in pods.items:
            if pod.status.phase == "Running":
                if pod.metadata.name.startswith(kind):
                    selectedTargetPodNames.append(pod.metadata.name)
                
    except client.ApiException as e:
        logger.warning(f"Error fetching pod_info: {e}")
        return []
    
    return selectedTargetPodNames


def list_selectedTargetPodInterfaces(targetpodname: str, namespace: str) -> list:
    command = ["sh", "-c", "ifconfig -a | awk '/^[^ ]/ {print $1}' | sed 's/://'"]
    try:
        resp = stream(
            v1.connect_get_namespaced_pod_exec,
            name=targetpodname,
            namespace=namespace,
            container="pod",
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
        )
        
        interfaces = [iface.strip() for iface in resp.split("\n") if iface.strip()]
        
        filtered_interfaces = [
            iface for iface in interfaces 
            if iface not in ["lo", "eth0", "tunl0", "ovs-system", "br0"]
        ]
        
        return filtered_interfaces
    
    except client.exceptions.ApiException as e:
        logger.warning(f"Error executing command in pod: {e}")
        return []
    except Exception as e:
        logger.warning(f"Unexpected error: {e}")
        return []


def list_ingresses(namespace: str) -> list:
    ingresses = networkV1.list_namespaced_ingress(namespace, pretty="true")
    ingress_list = []
    for ingress in ingresses.items:
        rule = ingress.spec.rules[0]
        ingress_list.append({
            "host": rule.host,
            "name": ingress.metadata.name,
            "service": rule.http.paths[0].backend.service.name
        })
    logger.info("ingresses: {}".format(ingresses))
    # logger.info("list: {}".format(ingress_list))
    return ingress_list

def extract_letters(s):
    # 找到第一个数字的位置
    split_index = 0
    while split_index < len(s) and s[split_index].islower():
        split_index += 1
    # 提取字母部分
    return s[:split_index]

def getPodInfoByLabel(namespace: str, label: str) -> dict:
    ret = v1.list_namespaced_pod(namespace, label_selector=label)
    for pod in ret.items:
        if pod.status.phase == "Running":
            return pod.to_dict()  # 转换为字典，便于处理
            
    logger.warning(f"No running pod found in namespace {namespace} with label {label}")
    return {}

def list_topology(namespace: str) -> dict:
    try:
        # 查询指定命名空间下的CRD实例
        topology = custom_api.list_namespaced_custom_object(
            group=custom_object_dict["Topology"]["group"],
            version=custom_object_dict["Topology"]["version"],
            namespace=namespace,
            plural=custom_object_dict["Topology"]["plural"],
        )
        topology_dict = {
            "nodes": [],
            "edges": [],
        }
        G = nx.Graph()
        processed_ids = set()
        for item in topology["items"]:
            topology_dict["nodes"].append({
                "id": item["metadata"]["name"],
                "label": item["metadata"]["name"],
                "data": {
                    "type": extract_letters(item["metadata"]["name"]),
                }
            })
            G.add_node(item["metadata"]["name"])
            for link in item["spec"]["links"]:
                if link["uid"] in processed_ids:
                    continue
                processed_ids.add(link["uid"])
                topology_dict["edges"].append({
                    "source": item["metadata"]["name"],
                    "target": link["peer_pod"],
                    "id": link["uid"]
                })
                G.add_edge(item["metadata"]["name"], link["peer_pod"])
        layout = NetworkTopologyLayout(G)
        layout.calculate_positions_kamada_kawai()
        pos = layout.get_positions()
        logger.info(f"Positions calculated: {pos}")
        for node in topology_dict["nodes"]:
            node["position"] = {
                "x": pos[node["id"]][0] * 500,
                "y": pos[node["id"]][1] * 500
            }
            node["dimension"]={
                "height":50,
                "width":50,
            }
        return topology_dict
    except client.exceptions.ApiException as e:
        print(f"API异常: {e.reason}")
        return {}

def delete_topology_link(namespace: str, name: str, uid: str):
    try:
        # 获取uid对应的接口和索引以便删除和更新
        topology = custom_api.get_namespaced_custom_object(
            group=custom_object_dict["Topology"]["group"],
            version=custom_object_dict["Topology"]["version"],
            namespace=namespace,
            plural=custom_object_dict["Topology"]["plural"],
            name=name,
        )
        logger.info(f"Topology {name} in namespace {namespace}: {topology}")

        # 查找链接的接口和索引
        interface, index = "", -1
        for i, link in enumerate(topology["spec"]["links"]):
            logger.info(f"link[uid]:{link['uid']}, uid:{uid}")
            if int(link["uid"]) == int(uid):
                interface = link["local_intf"]
                index = i
                break

        if index == -1:
            logger.warning(f"Link with uid {uid} not found in topology {name}")
            return False
        else:
            topology["spec"]["links"].pop(index)
            logger.info(f"Found link {uid} in topology {name} with interface {interface} at index {index}")

        # 使用grpc调用p2pnet守护进程删除链接
        pod_info = getPodInfoByLabel(namespace=namespace, label="device="+name)
        if not pod_info:
            logger.warning(f"Pod {name} not found in namespace {namespace}")
            return False
        
        with grpc.insecure_channel(pod_info.status.host_ip + ":50051") as channel:
            stub = netservice_pb2_grpc.LocalStub(channel)
            request = netservice_pb2.DeleteRequest(
                PodName=name,
                IfName=interface,
                Namespace=namespace
            )
            response = stub.DeleteLink(request)
            logger.info(f"Delete link {uid} in topology {name} response: {response}")

        # 更新crd拓扑资源
        updated_crd = custom_api.patch_namespaced_custom_object(
            group=custom_object_dict["Topology"]["group"],
            version=custom_object_dict["Topology"]["version"],
            namespace=namespace,
            plural=custom_object_dict["Topology"]["plural"],
            name=name,
            body={
                "spec": {
                    "links": topology["spec"]["links"]
                }
            }
        )
        logger.info(f"Updated topology {name} in namespace {namespace}: {updated_crd}")
        return True
    except client.exceptions.ApiException as e:
        logger.warning(f"Error deleting topology link {uid}: {str(e)}")
        return False
    except grpc.RpcError as e:
        logger.error(f"gRPC Error deleting link {uid}: {e}")
        return False

def delete_topology_node(namespace: str, name: str):
    try:
        # 获取uid对应的接口和索引以便删除和更新
        topology = custom_api.get_namespaced_custom_object(
            group=custom_object_dict["Topology"]["group"],
            version=custom_object_dict["Topology"]["version"],
            namespace=namespace,
            plural=custom_object_dict["Topology"]["plural"],
            name=name,
        )
        # 先去清除与该node相邻的所有节点的链接
        for link in topology["spec"]["links"]:
            delete_topology_link(namespace=namespace, name=link["peer_pod"], uid=link["uid"])

        # 删除node相关的pod和topology
        # 删除 Pod（同步操作）
        response = v1.delete_namespaced_pod(
            name=name,
            namespace=namespace,
            body=client.V1DeleteOptions(  # 可选的删除配置
                propagation_policy="Foreground",  # 删除策略（级联删除）
                grace_period_seconds=30,          # 优雅终止宽限期
            )
        )
        # 删除topology
        response = custom_api.delete_namespaced_custom_object(
            group=custom_object_dict["Topology"]["group"],
            version=custom_object_dict["Topology"]["version"],
            namespace=namespace,
            plural=custom_object_dict["Topology"]["plural"],
            name=name,
            body=client.V1DeleteOptions(
                propagation_policy="Foreground",
                grace_period_seconds=0  # 立即删除
            )
        )
        return True
    except client.exceptions.ApiException as e:    
        logger.warning(f"Error deleting topology node {name}: {str(e)}")
        return False

# def list_configmaps(namespace: str) -> list:
#     ret = v1.list_namespaced_config_map(namespace)
#     configmap_list = []
#     for item in ret.items:
#         configmap_list.append(item.metadata.name)
#     return configmap_list
#
#
# def list_secrets(namespace: str) -> list:
#     ret = v1.list_namespaced_config_map(namespace)
#     secret_list = []
#     for item in ret.items:
#         secret_list.append(item.metadata.name)
#     return secret_list


def get_configmap(namespace: str, configmap_name: str):
    cm = v1.read_namespaced_config_map(name=configmap_name, namespace=namespace)
    return cm


def delete_deployment(name: str, namespace: str):
    api_instance = AppsV1Api(k8s_client)
    try:
        resp = api_instance.delete_namespaced_deployment(name, namespace)
    except Exception as e:
        return False, e
    else:
        return True, resp


def delete_service(name: str, namespace: str):
    try:
        resp = v1.delete_namespaced_service(name, namespace)
    except Exception as e:
        return False, e
    else:
        return True, resp


def delete_configmap(name: str, namespace: str):
    try:
        resp = v1.delete_namespaced_config_map(name, namespace)
    except Exception as e:
        return False, e
    else:
        return True, resp


def delete_secret(name: str, namespace: str):
    try:
        resp = v1.delete_namespaced_secret(name, namespace)
    except Exception as e:
        return False, e
    else:
        return True, resp


def apply_from_yaml(yaml_content: dict, namespace: str):
    try:
        if yaml_content["kind"] in custom_object_dict.keys():
            resp = custom_api.create_namespaced_custom_object(
                group=custom_object_dict[yaml_content["kind"]]["group"],
                version=custom_object_dict[yaml_content["kind"]]["version"],
                namespace=namespace,
                plural=custom_object_dict[yaml_content["kind"]]["plural"],
                body=yaml_content,
                pretty=True,
            )
        else:
            resp = utils.create_from_dict(k8s_client, yaml_content, namespace=namespace)
    except FailToCreateError as e1:
        return False, e1.api_exceptions[0]
    except Exception as e:
        return False, e
    else:
        return True, resp

# 创建混沌实验
def create_chaos(plural: str, yaml_dict: dict):
    try:
        resp = custom_api.create_namespaced_custom_object(
            group="chaos-mesh.org",
            version="v1alpha1",
            namespace="chaos-mesh",
            plural=plural,
            body=yaml_dict,
            pretty=True,
        )
    except (ApiException, HTTPError) as e:
        return False, e
    else:
        return True, resp


def watch_event(namespace, kind: str, name: str, resource_version=None) -> (list, str):
    field_selector = "involvedObject.kind={},involvedObject.name={}".format(kind, name)
    event_list = []
    latest_resource_version = None
    if resource_version:
        resource_version = int(resource_version)
    try:
        stream = k8s_watch.stream(v1.list_namespaced_event, namespace, field_selector=field_selector,
                                  resource_version=resource_version, timeout_seconds=2)
        for event in stream:
            event_obj = event['object']
            info = {}
            info["type"] = event_obj.type
            info["reason"] = event_obj.reason
            info["timestamp"] = event_obj.first_timestamp.strftime("%Y-%m-%d %H:%M:%S")
            info["message"] = event_obj.message
            info["resource_version"] = event_obj.metadata.resource_version
            event_list.append(info)
            latest_resource_version = info["resource_version"]
            logger.info("event : {}".format(info))

    except ApiException as e:
        if e.status == 410:
            logger.warning("resource is too old, latest resource version is {}".format(latest_resource_version))
            return [], None
        else:
            raise
    return event_list, latest_resource_version
    # return event_list


# `plural` for chaos is just lowercase of `kind`
def delete_chaos(plural: str, name: str):
    try:
        resp = custom_api.delete_namespaced_custom_object(
            group="chaos-mesh.org",
            version="v1alpha1",
            namespace="chaos-mesh",
            plural=plural,
            name=name,
            body=client.V1DeleteOptions(),
        )
    except (ApiException, HTTPError) as e:
        return False, e
    else:
        return True, resp


def get_chaos_info(plural: str, name: str):
    resp = custom_api.get_namespaced_custom_object(
        group="chaos-mesh.org",
        version="v1alpha1",
        namespace="chaos-mesh",
        plural=plural,
        name=name)
    return resp


def annotate_chaos(plural: str, name: str, key: str, value: str):
    custom_api.patch_namespaced_custom_object(
        group="chaos-mesh.org",
        version="v1alpha1",
        namespace="chaos-mesh",
        plural=plural,
        name=name,
        body={
            "metadata": {"annotations": {key: value}}
        }
    )


def get_pod_info(namespace: str, pod_name: str):
    resp = v1.read_namespaced_pod(pod_name, namespace)
    return resp


def get_pod_interfaces_info(namespace: str, pod_name: str):
    try:
        pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
    except client.exceptions.ApiException as e:
        print(f"Error getting pod info: {e}")
        return None
    
    if pod_name.startswith("host") or pod_name.startswith("r"):
        command = ["ip", "route"]
    else:
        command = ["ovs-vsctl", "show"]

    try:
        resp = stream(
            v1.connect_get_namespaced_pod_exec,
            name=pod_name,
            namespace=namespace,
            container="pod",
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
    except client.exceptions.ApiException as e:
        print(f"Failed to execute command in pod {pod_name}: {e}")
        return None

    logger.info(resp)
    return {"output": resp}

def get_targetPod_IP(namespace: str, name: str):
    try:
        # 获取uid对应的接口和索引以便删除和更新
        topology = custom_api.get_namespaced_custom_object(
            group=custom_object_dict["Topology"]["group"],
            version=custom_object_dict["Topology"]["version"],
            namespace=namespace,
            plural=custom_object_dict["Topology"]["plural"],
            name=name,
        )
        logger.info(f"Topology {name} in namespace {namespace}: {topology}")

        for link in topology["spec"]["links"]:
            # 需要去除掩码部分
            return link["local_ip"].split("/")[0]
        
        return ""
    except client.exceptions.ApiException as e:
        logger.warning(f"Error api")
        return ""

def exec_ping_or_traceroute_command(namespace: str, source: str, target: str, action: str):
    targetIP = get_targetPod_IP(namespace=namespace, name=target)
    if targetIP == "":
        logger.info(f"get ip is null in name {target}, namespace {namespace}")
        return None

    if action == "ping":
        command = ["ping", "-c", "4", targetIP]
    elif action == "traceroute":
        command = ["traceroute", "-w", "3", targetIP]

    try:
        resp = stream(
            v1.connect_get_namespaced_pod_exec,
            name=source,
            namespace=namespace,
            container="pod",
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
    except client.exceptions.ApiException as e:
        print(f"Failed to execute command in pod {source}: {e}")
        return None

    logger.info(resp)
    return {"output": resp}

def add_pod(namespace: str, pod_name: str, pod_yaml: dict):
    try:
        v1.create_namespaced_pod(namespace=namespace, body=pod_yaml)
        logger.warning(f"Pod {pod_name} created successfully")
    except Exception as e:
        logger.warning(f"Error creating Pod {pod_name}: {str(e)}")
        raise


def delete_pod(namespace: str, pod_name: str):  # 删除指定pod，并监测其是否在规定时间内删除
    v1_delete_pod = client.CoreV1Api()
    try:
        v1_delete_pod.delete_namespaced_pod(name=pod_name, namespace=namespace)
        return True
    except ApiException as e:
        if e.status == 404:
            logger.warning(f"Pod {pod_name} not found in namespace {namespace}. Considering as deleted.")
            return True
        else:
            logger.warning(f"Error deleting pod {pod_name}: {e}")
            return False
    '''
    timeout = 120
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            pod_status = v1_delete_pod.read_namespaced_pod_status(pod_name, namespace)
            logger.warning(f"Pod {pod_name} still exists. Phase: {pod_status.status.phase}")
        except ApiException as e:
            if e.status == 404:
                logger.warning(f"Pod {pod_name} in namespace {namespace} has been successfully deleted.")
                return True
            else:
                logger.warning(f"Error checking pod status: {e}")
                return False
        
        time.sleep(2)
    
    logger.warning(f"Timeout reached. Pod {pod_name} may not have been fully deleted.")
    return False
    '''


def check_pod_running(namespace: str, pod_name: str):
    pod_info = None
    while pod_info == None:
        pod_info = get_pod_info(namespace, pod_name)
        time.sleep(3)
    pod_running = False
    MAX_RETRY = 10
    retry_count = 0

    while True:
        if pod_info.status.phase in ("Failed", "Succeeded"):
            raise RuntimeError(f"Pod {pod_name} exited with unexpected phase")

        # 确保 container_statuses 存在且非空
        container_statuses = getattr(pod_info.status, "container_statuses", None)
        if container_statuses is None:
            logger.warning(f"No container statuses found for pod {pod_name}, retrying...")
            containers_ready = False
        else:
            # 检查所有容器就绪状态
            containers_ready = all(
                container.ready for container in container_statuses
            )

        is_running = pod_info.status.phase == "Running"
        
        if is_running and containers_ready:
            pod_running = True
            break

        # 超时检测
        retry_count += 1
        if retry_count > MAX_RETRY:
            pod_running = False
            break
            
        # 指数退避重试
        sleep_time = min(2 ** (retry_count // 5), 30)  # 最大间隔30秒
        logger.warning(f"Current phase: {pod_info.status.phase}. Retrying in {sleep_time}s...")
        time.sleep(sleep_time)

        try:
            pod_info = get_pod_info(namespace, pod_name)
        except ApiException as e:
            if e.status == 404:
                logger.warning("Pod deleted during waiting, aborting")
                raise
            logger.warning(f"API error: {e}")
            continue
    
    logger.info(f"Pod {pod_name} now running")
    return pod_running


def get_pod_original_yaml(pod_name: str, namespace: str = "default") -> dict:
    try:
        v1 = client.CoreV1Api()
        apps_v1 = client.AppsV1Api()
        
        pod = v1.read_namespaced_pod(pod_name, namespace)
        
        # 检查 Pod 是否有 owner reference
        if pod.metadata.owner_references:
            for owner_ref in pod.metadata.owner_references:
                # 处理 Deployment 创建的 Pod
                if owner_ref.kind == "ReplicaSet":
                    try:
                        rs = apps_v1.read_namespaced_replica_set(owner_ref.name, namespace)
                        # 检查 ReplicaSet 的 owner reference (Deployment)
                        if rs.metadata.owner_references:
                            for rs_owner_ref in rs.metadata.owner_references:
                                if rs_owner_ref.kind == "Deployment":
                                    # 获取 Deployment
                                    deployment = apps_v1.read_namespaced_deployment(rs_owner_ref.name, namespace)
                                    # 返回 Deployment 中的 Pod template
                                    return deployment.spec.template.to_dict()
                    except ApiException:
                        continue
                
                # 处理 StatefulSet 创建的 Pod
                elif owner_ref.kind == "StatefulSet":
                    try:
                        sts = apps_v1.read_namespaced_stateful_set(owner_ref.name, namespace)
                        return sts.spec.template.to_dict()
                    except ApiException:
                        continue
                
                # 处理 DaemonSet 创建的 Pod
                elif owner_ref.kind == "DaemonSet":
                    try:
                        ds = apps_v1.read_namespaced_daemon_set(owner_ref.name, namespace)
                        return ds.spec.template.to_dict()
                    except ApiException:
                        continue
        
        # 没有找到控制器，返回 Pod 当前的定义
        return pod.to_dict()
    
    except ApiException as e:
        logger.warning(f"Error getting pod original YAML: {e}")
        return None
    except Exception as e:
        logger.warning(f"Unexpected error: {e}")
        return None


def update_topology_yaml(topology_yaml: list, namespace: str):
    for doc in topology_yaml["items"]:
        if doc['kind'] == 'Topology':
            try:
                client.CustomObjectsApi(k8s_client).patch_namespaced_custom_object(
                    group="networkop.co.uk",
                    version="v1beta1",
                    namespace=namespace,
                    plural="topologies",
                    name=doc['metadata']['name'],
                    body=doc
                )
            except Exception as e:
                logger.warning(f"Error updating topology for {doc['metadata']['name']}: {str(e)}")


def update_topology_item_link(namespace: str, topology: dict, name: str):
    updated_crd = custom_api.patch_namespaced_custom_object(
        group=custom_object_dict["Topology"]["group"],
        version=custom_object_dict["Topology"]["version"],
        namespace=namespace,
        plural=custom_object_dict["Topology"]["plural"],
        name=name,
        body={
            "spec": {
                "links": topology["spec"]["links"]
            }
        }
    )
    logger.info(f"Updated topology {name} in namespace {namespace}: {updated_crd}")
    return True


def add_topology_item(namespace: str, topology_item_doc: dict):
    updated_crd = custom_api.create_namespaced_custom_object(
        group=custom_object_dict["Topology"]["group"],
        version=custom_object_dict["Topology"]["version"],
        namespace=namespace,
        plural=custom_object_dict["Topology"]["plural"],
            body=topology_item_doc
        )
    logger.info(f"add new topology item in namespace {namespace}: {updated_crd}")
    return True


def delete_topology_item(namespace: str, name: str):
    response = custom_api.delete_namespaced_custom_object(
        group=custom_object_dict["Topology"]["group"],
        version=custom_object_dict["Topology"]["version"],
        namespace=namespace,
        plural=custom_object_dict["Topology"]["plural"],
        name=name,
        body=client.V1DeleteOptions(
            propagation_policy="Foreground",
            grace_period_seconds=0
        )
    )
    logger.info(f"delete topology item in namespace {namespace}: {response}")
    return True


# def update_configmap(configmap_name: str, namespace: str, cm):
#     try:
#         v1.patch_namespaced_config_map(
#             name=configmap_name,
#             namespace=namespace,
#             body=cm
#         )
#     except Exception as e:
#         logger.warning("updating kubeconfig failed: {}".format(e))
#         return False
#     return True


# def create_configmap(namespace: str, config_map: dict):
#     try:
#         v1.create_namespaced_config_map(namespace=namespace, body=config_map)
#     except Exception as e:
#         logger.warning(f"Error creating configmap: {str(e)}")
#         return False
#     return True


# def delete_configmap(namespace: str, routername: str):
#     # 删除其挂载的configmap
#     configmap_name = f"{routername}-config"
#     try:
#         v1.delete_namespaced_config_map(
#             name=configmap_name,
#             namespace=namespace,
#             propagation_policy='Foreground'
#         )
#         logger.info(f"ConfigMap {configmap_name} 删除成功")
#     except client.exceptions.ApiException as e:
#         if e.status != 404:  # 忽略NotFound错误
#             logger.error(f"删除ConfigMap失败: {e}")
#             raise


def add_connection_service(PodName1: str, IfName1: str, Ip1: str, PodName2: str, IfName2: str, Ip2: str, Namespace: str):
    pod1_info = get_pod_info(namespace=Namespace, pod_name=PodName1)
    pod2_info = get_pod_info(namespace=Namespace, pod_name=PodName2)
    if not pod1_info or not pod2_info:
        logger.warning(f"Pod {PodName1} or {PodName2} not found in namespace {Namespace}")
        return False
    with grpc.insecure_channel(pod1_info.status.host_ip + ":50051") as channel:
        stub = netservice_pb2_grpc.LocalStub(channel)
        request = netservice_pb2.MakeRequest(
            PodName1=PodName1,
            IfName1=IfName1,
            Ip1=Ip1,
            PodName2=PodName2,
            IfName2=IfName2,
            Ip2=Ip2,
            Namespace=Namespace
        )
        response = stub.MakeLink(request)
        logger.info(f"Successfully add new link between {PodName1}-{PodName2}: {response}")
    return True


def load_config():
    try:
        config.load_kube_config()
    except Exception as e:
        logger.warning("load kubeconfig failed: {}".format(e))
        config.load_incluster_config()


def load_topology_yaml(namespace: str) -> list:
    topology = custom_api.list_namespaced_custom_object(
        group=custom_object_dict["Topology"]["group"],
        version=custom_object_dict["Topology"]["version"],
        namespace=namespace,
        plural=custom_object_dict["Topology"]["plural"],
    )
    return topology


def generate_host_pod_yaml(hostname: str, default_route: str) -> dict:
    host_pod_yaml = {
        'apiVersion': 'v1',
        'kind': 'Pod',
        'metadata': {
            'name': hostname,
            'labels': {
                'name': hostname
            }
        },
        'spec': {
            'nodeName': 'k8s-large-1665988239',
            'initContainers': [{
                'name': 'initclient',
                'image': 'initclient:latest',
                'imagePullPolicy': 'IfNotPresent',
                'command': ["/entrypoint.sh"],
                'env': [
                    {
                        'name': 'POD_NAME',
                        'valueFrom': {
                            'fieldRef': {
                                'fieldPath': 'metadata.name'
                            }
                        }
                    },
                    {
                        'name': 'POD_NAMESPACE',
                        'valueFrom': {
                            'fieldRef': {
                                'fieldPath': 'metadata.namespace'
                            }
                        }
                    },
                    {
                        'name': 'HOST_IP',
                        'valueFrom': {
                            'fieldRef': {
                                'fieldPath': 'status.hostIP'
                            }
                        }
                    }
                ]
            }],
            'containers': [{
                'image': 'crpi-lz7p74weyijj71uf.cn-beijing.personal.cr.aliyuncs.com/yyc20001209/yyc:alpine-host',
                'imagePullPolicy': 'IfNotPresent',
                'name': 'pod',
                'command': [
                    '/bin/sh', '-c', f'sleep 5 && ip route add 10.12.0.0/16 via {default_route} && sleep infinity'
                ],
                'securityContext': {
                    'privileged': True
                }
            }]
        }
    }
    return host_pod_yaml


def generate_switch_pod_yaml(swname: str, peerpodname: str) -> dict:
    sw_pod_yaml = {
        'apiVersion': 'v1',
        'kind': 'Pod',
        'metadata': {
            'name': swname,
            'labels': {
                'name': swname
            }
        },
        'spec': {
            'nodeName': 'k8s-large-1665988239',
            'initContainers': [{
                'name': 'initclient',
                'image': 'initclient:latest',
                'imagePullPolicy': 'IfNotPresent',
                'command': ["/entrypoint.sh"],
                'env': [
                    {
                        'name': 'POD_NAME',
                        'valueFrom': {
                            'fieldRef': {
                                'fieldPath': 'metadata.name'
                            }
                        }
                    },
                    {
                        'name': 'POD_NAMESPACE',
                        'valueFrom': {
                            'fieldRef': {
                                'fieldPath': 'metadata.namespace'
                            }
                        }
                    },
                    {
                        'name': 'HOST_IP',
                        'valueFrom': {
                            'fieldRef': {
                                'fieldPath': 'status.hostIP'
                            }
                        }
                    }
                ]
            }],
            'containers': [{
                'image': 'openvswitch/ovs:2.11.2_debian',
                'imagePullPolicy': 'IfNotPresent',
                'name': 'pod',
                'command': [
                    '/bin/bash', '-c', f'/usr/share/openvswitch/scripts/ovs-ctl start && 'f'ovs-vsctl add-br br0 && 'f'ovs-vsctl add-port br0 {swname}_{peerpodname} && 'f'sleep infinity'
                ],
                'securityContext': {
                    'privileged': True
                },
                'volumeMounts': [
                    {
                        'mountPath': '/lib/modules',
                        'name': 'modules',
                        'readOnly': True
                    },
                    {
                        'mountPath': '/run',
                        'name': 'run'
                    }
                ]
            }],
            'volumes': [
                {
                    'name': 'modules',
                    'hostPath': {
                        'path': '/lib/modules'
                    }
                },
                {
                    'name': 'run',
                    'hostPath': {
                        'path': '/run'
                    }
                }
            ]
        }
    }
    return sw_pod_yaml


def generate_firewall_pod_yaml(fwname: str, peerpodname: str) -> dict:
    fw_pod_yaml = {
        'apiVersion': 'v1',
        'kind': 'Pod',
        'metadata': {
            'name': fwname,
            'labels': {
                'name': fwname
            }
        },
        'spec': {
            'nodeName': 'k8s-large-1665988239',
            'initContainers': [{
                'name': 'initclient',
                'image': 'initclient:latest',
                'imagePullPolicy': 'IfNotPresent',
                'command': ["/entrypoint.sh"],
                'env': [
                    {
                        'name': 'POD_NAME',
                        'valueFrom': {
                            'fieldRef': {
                                'fieldPath': 'metadata.name'
                            }
                        }
                    },
                    {
                        'name': 'POD_NAMESPACE',
                        'valueFrom': {
                            'fieldRef': {
                                'fieldPath': 'metadata.namespace'
                            }
                        }
                    },
                    {
                        'name': 'HOST_IP',
                        'valueFrom': {
                            'fieldRef': {
                                'fieldPath': 'status.hostIP'
                            }
                        }
                    }
                ]
            }],
            'containers': [{
                'image': 'alpine:latest',
                'imagePullPolicy': 'IfNotPresent',
                'name': 'pod',
                'command': ['/bin/sh', '-c', 'sysctl -w net.bridge.bridge-nf-call-iptables=1 && 'f'apk add bridge nftables && 'f'brctl addbr br0 && 'f'ip link set br0 up && 'f'brctl addif br0 {fwname}_{peerpodname} && 'f'ip link set {fwname}_{peerpodname} up && 'f'sleep infinity'],
                'securityContext': {
                    'privileged': True
                }
            }],
            #'hostPID': True
        }        
    }
    return fw_pod_yaml


def generate_router_pod_yaml(routername: str) -> dict:
    r_pod_yaml = {
        'apiVersion': 'v1',
        'kind': 'Pod',
        'metadata': {
            'name': routername,
            'labels': {
                'name': routername
            }
        },
        'spec': {
            'nodeName': 'k8s-large-1665988239',
            'initContainers': [{
                'name': 'initclient',
                'image': 'initclient:latest',
                'imagePullPolicy': 'IfNotPresent',
                'command': ["/entrypoint.sh"],
                'env': [
                    {
                        'name': 'POD_NAME',
                        'valueFrom': {
                            'fieldRef': {
                                'fieldPath': 'metadata.name'
                            }
                        }
                    },
                    {
                        'name': 'POD_NAMESPACE',
                        'valueFrom': {
                            'fieldRef': {
                                'fieldPath': 'metadata.namespace'
                            }
                        }
                    },
                    {
                        'name': 'HOST_IP',
                        'valueFrom': {
                            'fieldRef': {
                                'fieldPath': 'status.hostIP'
                            }
                        }
                    }
                ]
            }],
            'containers': [{
                'image': 'alpine:latest',
                'imagePullPolicy': 'IfNotPresent',
                'name': 'pod',
                'command': ['/bin/sh', '-c', f'echo 1 > /proc/sys/net/ipv4/ip_forward && 'f'sleep infinity'],
                'securityContext': {
                    'privileged': True
                }
            }],
        }
    }
    return r_pod_yaml


def generate_configmap_yaml(routername: str, subnet_prefix: str, peer_interface_name: str) -> dict:
    config_map = {
        'apiVersion': 'v1',
        'kind': 'ConfigMap',
        'metadata': {
            'name': f"{routername}-config"
        },
        'data': {
            'daemons': """\
bgpd=yes
ospfd=yes
ospf6d=no
ripd=no
ripngd=no
isisd=no
pimd=no
pim6d=no
ldpd=no
nhrpd=no
eigrpd=no
babeld=no
sharpd=no
pbrd=no
bfdd=no
fabricd=no
vrrpd=no
pathd=no
vtysh_enable=yes
zebra_options="  -A 127.0.0.1 -s 90000000"
mgmtd_options="  -A 127.0.0.1"
bgpd_options="   -A 127.0.0.1"
ospfd_options="  -A 127.0.0.1"
ospf6d_options=" -A ::1"
ripd_options="   -A 127.0.0.1"
ripngd_options=" -A ::1"
isisd_options="  -A 127.0.0.1"
pimd_options="   -A 127.0.0.1"
pim6d_options="  -A ::1"
ldpd_options="   -A 127.0.0.1"
nhrpd_options="  -A 127.0.0.1"
eigrpd_options=" -A 127.0.0.1"
babeld_options=" -A 127.0.0.1"
sharpd_options=" -A 127.0.0.1"
pbrd_options="   -A 127.0.0.1"
staticd_options="-A 127.0.0.1"
bfdd_options="   -A 127.0.0.1"
fabricd_options="-A 127.0.0.1"
vrrpd_options="  -A 127.0.0.1"
pathd_options="  -A 127.0.0.1"
""",
            'frr.conf': f"""\
frr version 8.5
frr defaults traditional
hostname {routername}
log file /var/log/frr/frr.log
service integrated-vtysh-config

router ospf
 network {subnet_prefix}.0/24 area 0
 network 10.12.0.0/16 area 0

interface {peer_interface_name}
 ip ospf area 0
""",
            'vtysh.conf': """\
service integrated-vtysh-config
"""
        }
    }
    return config_map


def generate_flow_controller_yaml(namespace: str) -> dict:
    gf_deployment_yaml = {
        'apiVersion': 'apps/v1',
        'kind': 'Deployment',
        'metadata': {
            'name': 'generate-flow-controller',
            'namespace': namespace,
            'labels': {
                'app': 'generate-flow-controller'
            }
        },
        'spec': {
            'replicas': 1,
            'selector': {
                'matchLabels': {
                    'app': 'generate-flow-controller'
                }
            },
            'template': {
                'metadata': {
                    'labels': {
                        'app': 'generate-flow-controller'
                    }
                },
                'spec': {
                    'nodeName': 'k8s-large-1665988239',
                    'serviceAccount': 'generate-flow',
                    'serviceAccountName': 'generate-flow',
                    'containers': [{
                        'name': 'generate-flow-controller',
                        'image': 'crpi-lz7p74weyijj71uf.cn-beijing.personal.cr.aliyuncs.com/yyc20001209/yyc:generate-flow-controller',
                        'imagePullPolicy': 'Always'
                    }]
                }
            }
        }
    }
    return gf_deployment_yaml

def parse_ping_output(output: str):
    try:
        loss_match = re.search(r'(\d+(?:\.\d+)?)% packet loss', output)
        rtt_match = re.search(r'rtt min/avg/max/mdev = [\d\.]+/([\d\.]+)/', output)
        loss = float(loss_match.group(1)) if loss_match else -1.0
        avg_rtt = float(rtt_match.group(1)) if rtt_match else -1.0
        return avg_rtt, loss
    except Exception as e:
        logger.warning(f"Error parsing ping output: {str(e)}")
        return -1.0, -1.0

def evaluate_topology_links(namespace: str):
    from service.k8s import load_topology_yaml
    from service.k8s import exec_ping_or_traceroute_command, get_targetPod_IP  # 延迟导入
    load_config()
    topology_docs = load_topology_yaml(namespace=namespace)
    updated_docs = []

    for doc in topology_docs["items"]:
        if doc['kind'] != 'Topology':
            continue

        node_name = doc['metadata']['name']
        links = doc['spec'].get('links', [])

        for link in links:
            peer_pod = link['peer_pod']
            uid = link['uid']

            try:
                target_ip = get_targetPod_IP(namespace, peer_pod)
                if not target_ip:
                    logger.warning(f"Cannot get target IP for {peer_pod}")
                    continue

                output = exec_ping_or_traceroute_command(namespace, node_name, target_ip, command_type="ping")
                latency, loss_rate = parse_ping_output(output)

                mongo.insert_one("link_metrics", {
                    "uid": uid,
                    "src_pod": node_name,
                    "dst_pod": peer_pod,
                    "latency_ms": latency,
                    "loss_rate_percent": loss_rate,
                    "timestamp": datetime.utcnow()
                })

                link['latency_ms'] = latency
                link['loss_rate_percent'] = loss_rate

            except Exception as e:
                logger.warning(f"Failed to evaluate link {node_name} -> {peer_pod}: {str(e)}")

        updated_docs.append(doc)

    return updated_docs


# VN_CHAOS注入
def injectVN_Chaos_service(namespace: str, selectedpodName: str, command: list):
    logger.info(f"selectedpodName: {selectedpodName}, command: {command}.")
    try:
        resp = stream(
            v1.connect_get_namespaced_pod_exec,
            name=selectedpodName,
            namespace=namespace,
            container="pod",
            command=command,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False
        )
    except client.exceptions.ApiException as e:
        print(f"Failed to execute command in pod {selectedpodName}: {e}")
        return False
    
    return True


if __name__ == '__main__':
    print(list_pods("wzk-sock-shop"))
