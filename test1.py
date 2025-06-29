from service.evaluation_helper import evaluate_topology_links

if __name__ == '__main__':
    evaluate_topology_links(namespace="default", task="node_metrics_k8s1")
    print("✅ 测试数据已写入 MongoDB")
