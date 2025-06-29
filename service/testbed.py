import yaml
import time

from bson import ObjectId

from config.config import mongo_client_platform_meta, mongo_client_pure_data, mongo_client_vn_chaos
from service.k8s import *
from service.file import read_yaml

# 2.操作mongo对象，完成插入。
def store_vn_chaos(namespace:str,fault_name:str,target_pod:str,field:str,inject_time:str):
    resp=mongo_client_vn_chaos.insert_one(collection="vn_chaos", dic={
        # "testbed_name": name,
        "namespace": namespace,
        "fault_name": fault_name,
        "targetPod": target_pod,
        "field":field,
        "inject_time":inject_time
    })
    return resp

def delete_one_history(namespace:str,id:str):
    resp = mongo_client_vn_chaos.delete_one(collection="vn_chaos", query={"namespace": namespace,"_id":id})
    return resp

# 3.获取所有vn_chaos注入历史
def get_all_history(namespace: str):
        records = mongo_client_vn_chaos.get_all(collection="vn_chaos",
                                                query={"namespace": namespace})
        raw = list(records)
        for item in raw:
            item['id']=str(item['_id'])
            del item["_id"]
        return raw

def store_user_testbed(email: str, namespace: str, benchmark_email: str, benchmark: str, name: str):
    # query corresponding benchmark and check whether it has load-test files
    record = mongo_client_platform_meta.get_one(collection="benchmark",
                                                query={"email": benchmark_email, "name": benchmark})
    if record["hasLoad"]:
        load = 1
    else:
        load = 0
    # load field: 0-> without load files, 1->load not deployed, 2->load deployed
    resp = mongo_client_platform_meta.insert_one(collection="testbed", dic={
        "email": email,
        "namespace": namespace,
        "benchmarkEmail": benchmark_email,
        "benchmark": benchmark,
        "name": name,
        "load": load
    })
    return resp


def store_user_vn_testbed(email: str, namespace: str, benchmark_email: str, benchmark: str, name: str):
    resp = mongo_client_platform_meta.insert_one(collection="testbed", dic={
        "email": email,
        "namespace": namespace,
        "benchmarkEmail": benchmark_email,
        "benchmark": benchmark,
        "name": name,
        "load": 0
    })
    return resp



def get_user_testbed(email: str):
    records = mongo_client_platform_meta.get_all(collection="testbed", query={"email": email})
    raw = list(records)
    for item in raw:
        del item["_id"]
    return raw

def list_llm_context(namespace: str):
    collection = "topology_llm_context_" + namespace
    records = mongo_client_pure_data.get_all(collection=collection, query={})
    raw = list(records)
    for item in raw:
        del item["_id"]
    return raw

def store_llm_context(namespace: str, role: str, content: str):
    collection = "topology_llm_context_" + namespace
    resp = mongo_client_pure_data.insert_one(collection=collection, dic={
        "role": role,
        "content": content,
        "timestamp": time.time()
    })
    return resp

def delete_testbed(email: str, namespace: str):
    resp = mongo_client_platform_meta.delete_one(collection="testbed", query={
        "email": email,
        "namespace": namespace
    })
    return resp

def delete_dataset(namespace : str) :
    pods = list_pods(namespace)
    resp1 = resp2 = resp3 = resp4 = resp5 = None
    # delete metrics
    for pod in pods :
        metric_collection = "container_metrics_" + namespace + "_" + pod["name"]
        resp1 = mongo_client_pure_data.delete_collection(collection=metric_collection)
    # delete logs
    for pod in pods :
        log_collection = "container_logs_" + namespace + "_" + pod["name"]
        resp2 = mongo_client_pure_data.delete_collection(collection=log_collection)
    # delete trace
    services = list_services(namespace)
    for service in services : 
        trace_collection = "container_traces_" + namespace + "_" + service["name"]
        resp3 = mongo_client_pure_data.delete_collection(collection=trace_collection)
    # delete graph
    graph_collection = "topology_graph_" + namespace
    resp4 = mongo_client_pure_data.delete_collection(collection=graph_collection)

    # delete llm context
    llm_context_collection = "topology_llm_context_" + namespace
    resp5 = mongo_client_pure_data.delete_collection(collection=llm_context_collection)

    return [resp1, resp2, resp3, resp4, resp5]

def create_benchmark(email: str, name: str, visibility: str, description: str, hasLoad: bool):
    resp = mongo_client_platform_meta.insert_one(collection="benchmark", dic={
        "email": email,
        "name": name,
        "visibility": visibility,
        "description": description,
        "hasLoad": hasLoad
    })
    return resp


def benchmark_exists(email: str, name: str) -> bool:
    records = mongo_client_platform_meta.get_all(collection="benchmark", query={
        "email": email,
        "name": name
    })
    raw = list(records)
    if len(raw) == 0:
        return False
    else:
        return True


def get_public_benchmarks():
    records = mongo_client_platform_meta.get_all(collection="benchmark", query={"visibility": "public"})
    raw = list(records)
    for item in raw:
        del item["_id"]
    return raw


def get_private_benchmarks(email: str):
    records = mongo_client_platform_meta.get_all(collection="benchmark",
                                                 query={"email": email, "visibility": "private"})
    raw = list(records)
    for item in raw:
        del item["_id"]
    return raw


def get_benchmarks_by_email(email: str):
    records = mongo_client_platform_meta.get_all(collection="benchmark", query={"email": email})
    raw = list(records)
    for item in raw:
        del item["_id"]
    return raw


def store_benchmark_file(email: str, benchmark_name: str, file: dict):
    result = {"email": email, "benchmarkName": benchmark_name}
    for key, value in file.items():
        result[key] = value
    resp = mongo_client_platform_meta.insert_one(collection="benchmark-files", dic=result)
    return resp


def get_benchmark_files(email: str, benchmark_name: str):
    records = mongo_client_platform_meta.get_all(collection="benchmark-files",
                                                 query={"email": email, "benchmarkName": benchmark_name})
    raw = list(records)
    for item in raw:
        del item["_id"]
    return raw


def get_benchmark_file(email: str, benchmark_name: str, dir_path: str, file_name: str):
    record = mongo_client_platform_meta.get_one(collection="benchmark-files", query={
        "email": email,
        "benchmarkName": benchmark_name,
        "dirPath": dir_path,
        "fileName": file_name
    })
    del record["_id"]
    return record


def deploy_benchmark(benchmark_email: str, benchmark: str, namespace: str, file_type: str):
    # read benchmark files
    records = mongo_client_platform_meta.get_all(collection="benchmark-files",
                                                 query={"email": benchmark_email,
                                                        "benchmarkName": benchmark,
                                                        "dirPath": file_type})
    yamls = list(records)
    # deploy each resource
    for yaml_file in yamls:
        logger.info("yaml file: {}".format(yaml_file))
        yaml_content = yaml_file["fileContent"]
        for yaml_dict in yaml.safe_load_all(yaml_content):
            success, response = apply_from_yaml(yaml_dict, namespace)
            if not success:
                return False, response
    return True, response


def deploy_vn(namespace: str, topologyYaml: str, podYaml: str, gfcYaml: dict):
    for yaml_dict in yaml.safe_load_all(topologyYaml):
        success, response = apply_from_yaml(yaml_dict, namespace)
        if not success:
            return False, response
        
    for yaml_dict in yaml.safe_load_all(podYaml):
        success, response = apply_from_yaml(yaml_dict, namespace)
        if not success:
            return False, response
        
    success, response = apply_from_yaml(gfcYaml, namespace)
    if not success:
        return False, response
    
    return True, response


def set_load_deployed(email: str, namespace: str):
    mongo_client_platform_meta.update_one(collection="testbed", query={
        "email": email,
        "namespace": namespace
    }, newvalues={
        "$set": {
            "load": 2
        }
    })


def delete_load(email: str, namespace: str, benchmark_email: str, benchmark: str):
    # delete load test resources
    records = list(mongo_client_platform_meta.get_all(collection="benchmark-files", query={
        "email": benchmark_email,
        "benchmarkName": benchmark,
        "dirPath": "load-test"
    }))
    for record in records:
        yamls = yaml.load_all(record["fileContent"], Loader=yaml.FullLoader)
        for yaml_dict in yamls:
            kind = yaml_dict["kind"]
            name = yaml_dict["metadata"]["name"]
            if kind == "Deployment":
                success, resp = delete_deployment(name, namespace)
                if success:
                    logger.info("deleted deployment {} in namespace {}".format(name, namespace))
                else:
                    logger.error("Exception when calling delete_namespaced_deployment: %s\n" % resp)
            elif kind == "Service":
                success, resp = delete_service(name, namespace)
                if success:
                    logger.info("deleted service {} in namespace {}".format(name, namespace))
                else:
                    logger.error("Exception when calling delete_namespaced_service: %s\n" % resp)
            elif kind == "ConfigMap":
                success, resp = delete_configmap(name, namespace)
                if success:
                    logger.info("deleted configmap {} in namespace {}".format(name, namespace))
                else:
                    logger.error("Exception when calling delete_namespaced_configmap: %s\n" % resp)
            elif kind == "Secret":
                success, resp = delete_secret(name, namespace)
                if success:
                    logger.info("deleted secret {} in namespace {}".format(name, namespace))
                else:
                    logger.error("Exception when calling delete_namespaced_secret: %s\n" % resp)

    # set load to 1 (not deployed)
    mongo_client_platform_meta.update_one(collection="testbed", query={
        "email": email,
        "namespace": namespace
    }, newvalues={
        "$set": {
            "load": 1
        }
    })


def delete_benchmark(email: str, benchmark: str):
    logger.info("email: {}, benchmark: {}".format(email, benchmark))
    mongo_client_platform_meta.delete_all(collection="benchmark", query={
        "email": email,
        "name": benchmark
    })
    mongo_client_platform_meta.delete_all(collection="benchmark-files", query={
        "email": email,
        "benchmarkName": benchmark
    })


def replace_benchmark_files(email: str, benchmark: str, files: list):
    mongo_client_platform_meta.delete_all(collection="benchmark-files", query={
        "email": email,
        "benchmarkName": benchmark
    })
    for file in files:
        store_benchmark_file(email, benchmark, file)


def get_benchmark_files_under_dir(email: str, benchmark: str, dir: str):
    records = mongo_client_platform_meta.get_all(collection="benchmark-files", query={
        "email": email,
        "benchmarkName": benchmark,
        "dirPath": dir
    })
    raw = list(records)
    for item in raw:
        del item["_id"]
    return raw


def deploy_crawler(testbed: str) -> (bool, str):
    tpl = yaml.safe_load_all(open(r"data-crawler-template.yaml"))

    for item in tpl:
        if item["kind"] == "Deployment":
            item["metadata"]["name"] = testbed

            # label and matchLabel
            item["spec"]["template"]["metadata"]["labels"]["app"] = testbed
            item["spec"]["selector"]["matchLabels"]["app"] = testbed

            # container env
            item["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] = testbed

            return apply_from_yaml(item, "data-crawler")
        else:
            return False, "template file error"


def delete_crawler(testbed: str) -> (bool, str):
    return delete_deployment(testbed, "data-crawler")


if __name__ == '__main__':
    delete_load("hello@world.com", "sock-shop-0f618cc6-5a07-4a38", "hello@world.com", "sock-shop")
