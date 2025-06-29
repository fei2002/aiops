def transform(target: str) -> str:
    """
    transform target to the format that can be used in chao
    :param target: target
    :return:  transformed target
    """
    # transform target: node_metrics_aiops-k8s1  aiops-k8s1
    if "node_metrics" in target:
        target = target.split("_")[-1]

    # transform target: service_metrics_sock-shop_orders sock-shop-orders
    if "service_metrics" in target:
        target = target.split("_")[-2] + "-" + target.split("_")[-1]
    return target
