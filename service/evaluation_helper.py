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


def evaluate_topology_links(namespace: str, task: str = "network_metrics"):
    """增强版链路评估函数"""
    try:
        # 获取拓扑数据
        topology_docs = load_topology_yaml(namespace)
        updated_count = 0
        
        # 获取MongoDB集合
        collection = mongo_client_eval.evaluation[task]  # 使用evaluation数据库
        
        for doc in topology_docs["items"]:
            if doc['kind'] != 'Topology':
                continue
                
            node_name = doc['metadata']['name']
            for link in doc['spec'].get('links', []):
                try:
                    # 执行ping测试
                    output = exec_ping_or_traceroute_command(
                        namespace, 
                        node_name, 
                        get_targetPod_IP(namespace, link['peer_pod']),
                        "ping"
                    )
                    latency, loss = parse_ping_output(output)
                    
                    # 写入MongoDB
                    collection.insert_one({
                        "timestamp": datetime.utcnow(),
                        "source": node_name,
                        "target": link['peer_pod'],
                        "latency_ms": latency,
                        "loss_percent": loss,
                        "link_id": link['uid']
                    })
                    updated_count += 1
                    
                except Exception as e:
                    logger.error(f"链路 {node_name}->{link['peer_pod']} 测试失败: {str(e)}")
        
        logger.success(f"成功写入 {updated_count} 条链路指标到 evaluation.{task}")
        return True
        
    except Exception as e:
        logger.critical(f"拓扑评估全局错误: {str(e)}")
        return False