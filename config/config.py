import os.path

from service import mongo

mongo_client_eval = mongo.MongoConnectClient(host="k8s.personai.cn", db="evaluation", port=30332)

# 历史记录存放
mongo_client_vn_chaos = mongo.MongoConnectClient(host="k8s.personai.cn", db="vn_chaos", port=30332)

mongo_client_chaos = mongo.MongoConnectClient(host="k8s.personai.cn", db="chaos", port=30332)

mongo_client_platform_meta = mongo.MongoConnectClient(host="k8s.personai.cn", db="aiops", port=30332)

mongo_client_pure_data = mongo.MongoConnectClient(host="k8s.personai.cn", db="pure_data", port=30332)

available_nodes = ["aiops-k8s1"]

node_address_map = {
    "aiops-k8s1": "http://192.168.31.25:31767"
}

address_node_map = {
    "http://192.168.31.25:31767": "aiops-k8s1"
}

MICROSERVICES_DEPLOY_DIR = os.path.abspath("testbed_deploy/microservices")
LOAD_MAKER_DIR = os.path.abspath("testbed_deploy/load-maker")


CHAOS_TEMPLATE_DIR = os.path.abspath("chaos_template/")
CHAOS_HISTORY_DIR = os.path.abspath("chaos_history/")

KUBERNETES_CHAOS_CONFIG = os.path.abspath("./config/kubernetes_chaos.json")
NODE_CHAOS_CONFIG = os.path.abspath("config/node_chaos.json")

DOWNLOAD_TEMP_DIR = os.path.abspath("download_temp")

# 不允许注入的namespace
ignore_ns = ["anomaly-detection", "cattle-dashboards", "cattle-fleet-system", "cattle-impersonation-system",
             "cattle-system",
             "chaos-mesh", "collector", "deepflow", "default", "istio-system", "kafka", "kube-node-lease",
             "kube-public",
             "kube-system", "local", "monitoring", "schedule", "train-job", "wanz"]

custom_object_dict = {
    "Topology" : {
        "group" : "networkop.co.uk",
        "version" : "v1beta1",
        "plural" : "topologies"
    }
}