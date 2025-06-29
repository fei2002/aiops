import os
import threading
import time
from datetime import datetime, timedelta
from functools import reduce

from kubernetes.client import V1Pod
from loguru import logger

from config.config import CHAOS_TEMPLATE_DIR, KUBERNETES_CHAOS_CONFIG, NODE_CHAOS_CONFIG, node_address_map, \
    mongo_client_chaos
from service.file import read_json, read_yaml
from service.k8s import watch_event, get_chaos_info, list_pods, get_pod_info
from service.node_chaos import get_uuid_name
from service.timeutil import get_timestamp, cal_end_timestamp
import ast

# 获取fault_type对应的yaml字段
def get_fields(exp_type, fault_type):
    obj = {}
    if exp_type == "kubernetes":
        obj = read_json(KUBERNETES_CHAOS_CONFIG)
    else:  # node
        obj = read_json(NODE_CHAOS_CONFIG)
    logger.info("fault obj:{}".format(obj))
    fault_obj = obj['input'][fault_type]
    fields_resp = {}
    for field in fault_obj:
        fields_resp[field['name']] = field['msg']
    return fields_resp

# 递归更新字典值
def update_dict(d: dict, level: str, val):
    # check whether the path exists first
    try:
        v = reduce(lambda x, y: x[y], level.split("."), d)
        # logger.debug("path:{}, val: {}".format(level, v))
    except KeyError as e:
        logger.error("Missing key: {} in path {}".format(e, level))
    keys = level.split('.')
    current_dict = d
    for k in keys[:-1]:
        current_dict = current_dict[k]
    # Update the final nested dictionary with the new value
    current_dict[keys[-1]] = val

# 设置kubernetes的label
def set_label(yaml_dict: dict, params: dict, predefined: dict):
    label_str = params["label"]
    label_strs = label_str.split(':')
    label_dict = {str.strip(label_strs[0]): str.strip(label_strs[1])}
    logger.debug("use selected label dict: {}".format(label_dict))
    update_dict(yaml_dict, predefined["labelSelectors"], label_dict)


def render_chaos_yaml(exp_type: str, fault_type: str, params: dict) -> dict:
    """
    根据用户输入，生成故障实验yaml对应的字典对象
    :return:
    :param exp_type: 实验的种类，kubernetes或node
    :param fault_type: 故障类型，如StressChaos
    :param params: 前端传过来的用户输入
    :return: yaml对应的字典对象
    """
    # since we only allow users to inject kubernetes, so we don't need exp_type as prefix now
    name = "{}-{}".format(fault_type, get_uuid_name())  # generate experiment name 如 "StressChaos-abc123"
    params['name'] = name  # add name to params
    # fill list fields in yaml
    if exp_type == "kubernetes":
        obj = read_json(KUBERNETES_CHAOS_CONFIG)
        ns = params['namespace']
        params['namespace'] = [ns]
    else:  # node
        obj = read_json(NODE_CHAOS_CONFIG)
        address = node_address_map[params.get("node")]
        params['address'] = [address]
        
    predefined = obj['predefined']
    inputs = obj['input'][fault_type]
    # fill yaml empty fields
    tmpl_path = os.path.join(CHAOS_TEMPLATE_DIR, "{}/{}.yaml".format(exp_type, fault_type))

    d = read_yaml(tmpl_path)

    # set label first
    set_label(d, params, predefined)

    for key, val in params.items():
        if key in predefined:
            update_dict(d, predefined[key], val)
        # for name, level in predefined.items():  # fill predefined fields
        #     if key == name:  # label != labelSelectors, so it will not update twice
        #         update_dict(d, level, val)
        for user_input in inputs:  # fill user input fields
            if key == user_input['name']:
                if user_input['type'] == 'string':
                    update_dict(d, user_input['level'], str.strip(val))
                elif user_input['type'] == '[]string' or user_input['type'] == 'String Array': # 用户输入格式要求形如 "a b c"
                    update_dict(d, user_input['level'], val.split())
                elif user_input['type'] == '[][]string': # 用户输入格式要求形如 "[['a','b'], ['c','d']"
                    update_dict(d, user_input['level'], ast.literal_eval(val))
                elif user_input['type'] == 'dict': # 给labelSelectors传值
                    val_list = val.split(":")
                    val_dict = {str.strip(val_list[0]): str.strip(val_list[1])}
                    update_dict(d, user_input['level'], val_dict)
                else:
                    update_dict(d, user_input['level'], int(val))
    logger.info(d)

    return d

# 存储实验信息到数据库
def store_chaos(email: str, kind: str, name: str, testbed: str, namespace: str, label: str, start_time: int,
                end_time: int,
                yaml_dict: dict, detail: object = None):
    mongo_client_chaos.insert_one(collection="chaos", dic={
        "email": email,
        "kind": kind,  # used when deleting chaos
        "name": name,
        "testbed": testbed,
        "namespace": namespace,
        "label": label,
        "start_time": start_time,
        "end_time": end_time,
        "yaml": yaml_dict,
        "detail": detail,
        "archived": False
    })

# 定时存储？
def store_schedule(email: str, name: str, testbed: str, namespace: str, label: str, start_time: int, yaml_dict: dict,
                   detail: object):
    mongo_client_chaos.insert_one(collection="schedule", dic={
        "email": email,
        "name": name,
        "testbed": testbed,
        "namespace": namespace,
        "label": label,
        "start_time": start_time,
        "yaml": yaml_dict,
        "detail": detail,
        "archived": False
    })

# 小写首字母
def make_first_lower(word: str) -> str:
    # PhysicalMachineChaos has two capital letter need to be lower
    if word == "PhysicalMachineChaos":
        return "physicalmachineChaos"
    return word[:1].lower() + word[1:]


def render_schedule_yaml(exp_type: str, fault_type: str, params: dict) -> dict:
    # render corresponding chaos yaml first
    chaos_yaml = render_chaos_yaml(exp_type, fault_type, params)
    # add the chaos yaml to schedule yaml
    schedule_yaml = read_yaml(os.path.join(CHAOS_TEMPLATE_DIR, "schedule.yaml"))
    # truncate due to length limit
    initial_name = 'schedule-{}'.format(chaos_yaml['metadata']['name'])
    schedule_yaml['metadata']['name'] = "-".join(initial_name.split("-")[:5])
    schedule_yaml['spec']['schedule'] = params['schedule']
    schedule_yaml['spec']['type'] = chaos_yaml['kind']
    schedule_yaml['spec'][make_first_lower(chaos_yaml['kind'])] = chaos_yaml['spec']
    return schedule_yaml


# get exp_type: kubernetes/node for schedule
def get_exp_type(chaos_name: str):
    # return chaos_name.split("-")[1]
    return "kubernetes"  # only kubernetes now


def get_yaml_obj(resp: dict) -> dict:
    """
    generate yaml object from chaos info
    :param resp: chaos info like result of `kubectl describe ...`
    :return: dict that represents yaml
    """
    yaml = {}
    yaml["apiVersion"] = resp["apiVersion"]
    yaml["kind"] = resp["kind"]
    yaml["metadata"] = {}
    yaml["metadata"]["name"] = resp["metadata"]["name"]
    yaml["metadata"]["namespace"] = resp["metadata"]["namespace"]
    yaml["spec"] = resp["spec"]
    return yaml


# Deprecated!
# watch real time events of chaos schedule, and store new chaos info in database
def watch_schedule(stop_flag: threading.Event, namespace: str, kind: str, name: str, chaos_type: str):
    latest = None
    logger.info("start to watch schedule {}".format(name))
    while not stop_flag.is_set():
        event_list, tmp_latest = watch_event(namespace, kind, name, latest)
        for event in event_list:
            if event["type"] == "Normal" and event["reason"] == "Spawned":
                chaos_name = event["message"].split(" ")[-1]
                logger.info("chaos name:{}".format(chaos_name))
                resp = get_chaos_info(chaos_type.lower(), chaos_name)
                # 若异常yaml文件无duration（pod-kill），默认为0s
                if 'duration' not in resp['spec']:
                    resp['spec']['duration'] = '0s'
                logger.info("chaos info: {}".format(resp))
                yaml_dict = get_yaml_obj(resp)
                store_chaos(resp['kind'],
                            resp['metadata']['name'],
                            get_chaos_target(get_exp_type(chaos_name), yaml_dict),
                            get_timestamp(resp['metadata']['creationTimestamp'], "%Y-%m-%dT%H:%M:%SZ"),
                            cal_end_timestamp(resp['metadata']['creationTimestamp'], resp['spec']['duration']),
                            yaml_dict=yaml_dict,
                            detail=resp
                            )
            elif event["type"] == "Warning" and event["reason"] == "Failed":
                logger.error("failed because of too many missed scheduled jobs. (paused too long)")
        # if there's new event, we update the resource version and watch start from this version
        # if not, we still watch from the old version until new events come.
        if len(event_list) != 0:
            latest = tmp_latest
        # watch resource state every minute
        logger.info("start: sleep one minute")
        time.sleep(60)
        logger.info("end: sleep one minute")
    logger.info("-----------------------thread has been cancelled---------------")


def get_chaos_target(exp_type: str, yaml_dict: dict) -> str:
    if exp_type == "kubernetes":
        # chaos yaml need it in pred format, but we only use one namespace
        namespace = yaml_dict["spec"]["selector"]["namespaces"][0]
        label = yaml_dict["spec"]["selector"]["labelSelectors"]
        label_str = "{}: {}".format(list(label.keys())[0], list(label.values())[0])
        return "{}/{}".format(namespace, label_str)
    else:
        node = yaml_dict["spec"]["address"][0]
        return node


def get_archived_experiments(email: str) -> list:
    raw = mongo_client_chaos.get_all(collection="chaos", query={"email": email, "archived": True})
    records = list(raw)
    for record in records:
        del record["_id"]
    return records


def get_archived_schedules(email: str) -> list:
    raw = mongo_client_chaos.get_all(collection="schedule", query={"email": email, "archived": True})
    records = list(raw)
    for record in records:
        del record["_id"]
    return records


def get_all_archived_experiments() -> list:
    raw = mongo_client_chaos.get_all(collection="chaos", query={"archived": True})
    records = list(raw)
    for record in records:
        del record["_id"]
    return records


def get_all_archived_schedules() -> list:
    raw = mongo_client_chaos.get_all(collection="schedule", query={"archived": True})
    records = list(raw)
    for record in records:
        del record["_id"]
    return records


def delete_archived_experiment(name: str):
    mongo_client_chaos.delete_one(collection="chaos", query={"name": name})


def delete_archived_schedule(name: str):
    mongo_client_chaos.delete_one(collection="schedule", query={"name": name})


def delete_chaos_by_namespace(namespace: str):
    # delete experiments
    mongo_client_chaos.delete_all(collection="chaos", query={"namespace": namespace})
    # delete schedules
    mongo_client_chaos.delete_all(collection="schedule", query={"namespace": namespace})


def clear_stale_archives():
    experiments = get_all_archived_experiments()
    schedules = get_all_archived_schedules()
    logger.info("stale archive experiment: {}".format(experiments))
    current_time = datetime.now()
    for experiment in experiments:
        if "archived_at" in experiment:
            archive_time = experiment["archived_at"]
            if current_time - archive_time > timedelta(days=30):
                logger.info("stale archive experiment: {}".format(experiment))
                delete_archived_experiment(experiment["name"])
                logger.info("deleted stale archive experiment: {}".format(experiment["name"]))

    for schedule in schedules:
        if "archived_at" in schedule:
            archive_time = schedule["archived_at"]
            if current_time - archive_time > timedelta(days=30):
                logger.info("stale archive schedule: {}".format(schedule))
                delete_archived_schedule(schedule["name"])
                logger.info("deleted stale archive schedule: {}".format(schedule["name"]))


def get_ns_pod_labels(namespace: str) -> list:
    pods = list_pods(namespace)
    result = set()
    # get unique label strings
    for pod in pods:
        for key, value in pod["labels"].items():
            label_str = "{}: {}".format(key, value)
            result.add(label_str)
    result = list(result)
    result.sort()  # return sorted labels
    return result


def get_pod_labels(namespace: str, pod: str) -> list:
    pod_info: V1Pod = get_pod_info(namespace, pod)
    result = []
    for key, value in pod_info.metadata.labels.items():
        label_str = "{}: {}".format(key, value)
        result.append(label_str)
    result.sort()
    return result
