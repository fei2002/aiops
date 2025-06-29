import re
from datetime import datetime
from loguru import logger

from service.k8s import load_topology_yaml, get_targetPod_IP, exec_ping_or_traceroute_command, load_config
from config.config import mongo_client_eval


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


def evaluate_topology_links(namespace: str, task: str = "node_metrics_k8s1"):
    """
    :param namespace: k8s namespace
    :param task: 插入的 MongoDB 集合名称，默认是 node_metrics_k8s1
    :return: updated topology docs
    """
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

                # ✅ 写入到 evaluation.task (如 evaluation.node_metrics_k8s1)
                mongo_client_eval.db[task].insert_one({
                    "timestamp": datetime.utcnow().timestamp(),
                    "metrics": [latency, loss_rate],
                    "target_list": f"{node_name}->{peer_pod}",
                    "predicted": 0  # 默认可填 0，后续模型更新可替换
                })

                # ✅ 更新 link 信息（可用于展示或前端刷新）
                link['latency_ms'] = latency
                link['loss_rate_percent'] = loss_rate

            except Exception as e:
                logger.warning(f"Failed to evaluate link {node_name} -> {peer_pod}: {str(e)}")

        updated_docs.append(doc)

    return updated_docs
