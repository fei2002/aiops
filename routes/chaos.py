# 创建和管理故障注入实验
from flask import Blueprint, make_response, jsonify, request

from config.config import available_nodes, CHAOS_HISTORY_DIR
from service.chaos import *
from service.file import *
from service.history import load_all_chaos, load_all_vn_chaos, archive_experiment_by_name, load_all_schedules, archive_schedule_by_name
from service.k8s import *
from service.timeutil import get_timestamp, cal_end_timestamp

chaos_bp = Blueprint('chaos', __name__, url_prefix='/chaos')


if not os.path.exists(CHAOS_TEMPLATE_DIR):
    os.mkdir(CHAOS_TEMPLATE_DIR)

if not os.path.exists(CHAOS_HISTORY_DIR):
    os.mkdir(CHAOS_HISTORY_DIR)
history_list = []


@chaos_bp.route('/namespaces')
def get_namespaces():
    ret = list_namespaces()
    logger.info("get namespaces: {}".format(ret))
    return make_response(jsonify(namespaces=ret), 200)


@chaos_bp.route('/services')
def get_services():
    namespace = request.args.get("namespace")
    if not namespace:
        return make_response(jsonify(msg="namespace cannot be empty"), 400)
    ret = list_services(namespace)
    logger.info("get services in namespace {}: {}".format(namespace, ret))
    return make_response(jsonify(services=ret), 200)


@chaos_bp.route('/<namespace>/pod_labels')
def pod_labels_handler(namespace: str):
    labels = get_ns_pod_labels(namespace)
    return make_response(jsonify(labels), 200)


@chaos_bp.route('/nodes')
def get_nodes():
    logger.info("get nodes")
    return make_response(jsonify(nodes=available_nodes), 200)


@chaos_bp.route('/<exp_type>/fault_types')
def get_fault_types(exp_type):
    if exp_type == "kubernetes" or exp_type == "node":
        dir_path = os.path.join(CHAOS_TEMPLATE_DIR, exp_type)
        return make_response(jsonify(fault_types=get_filenames(dir_path)), 200)
    else:
        return make_response(jsonify(msg="invalid experiment type"), 404)

# 故障处理
@chaos_bp.route('/<exp_type>/fault_types/<fault_type>', methods=['GET', 'POST'])
def fault_type_handler(exp_type, fault_type):
    if exp_type != "kubernetes" and exp_type != "node":
        return make_response(jsonify(msg="invalid experiment type"), 404)

    if request.method == 'GET':
        return make_response(jsonify(resp=get_fields(exp_type, fault_type)), 200)

    # post method
    else:
        email = request.headers.get("email")
        params = request.values.to_dict()
        if "yaml" in params:  # reuse yaml
            yaml_dict = yaml.load(params.get("yaml"), Loader=yaml.FullLoader)
        else:  # render yaml according to fields
            yaml_dict = render_chaos_yaml(exp_type, fault_type, params.copy())
        success, resp = create_chaos(yaml_dict["kind"].lower(), yaml_dict)
        # 若异常yaml文件无duration（pod-kill），默认为0s
        if 'duration' not in resp['spec']:
            resp['spec']['duration'] = '0s'
        if success:
            logger.info("created chaos, detail: {}".format(resp))
            # retrieve info and write to mongo
            store_chaos(email,
                        resp['kind'],
                        resp['metadata']['name'],
                        params["testbed"],
                        params["namespace"],
                        params["label"],
                        get_timestamp(resp['metadata']['creationTimestamp'], "%Y-%m-%dT%H:%M:%SZ"),
                        cal_end_timestamp(resp['metadata']['creationTimestamp'], resp['spec']['duration']),
                        yaml_dict=yaml_dict,
                        detail=resp
                        )
            return make_response(jsonify(msg="apply chaos successfully"), 200)
        else:
            return make_response(jsonify(resp=resp.body), resp.status)


@chaos_bp.route('/experiment/history', methods=['GET', 'DELETE'])
def history_all():
    if request.method == 'GET':
        email = request.headers.get("email")
        event_list = load_all_chaos(email)
        return make_response(jsonify(resp=event_list), 200)

    name = request.values.get("name")
    if not name:
        return make_response(jsonify(msg="missing input"), 400)
    # delete
    count = archive_experiment_by_name(name)
    return make_response(jsonify(msg="success" if count == 1 else "fail"), 200)


@chaos_bp.route('/<exp_type>/vn_fault_types/<fault_type>', methods=['POST'])
def add_vn_chaos(exp_type):
    if exp_type != "switch" and exp_type != "router":
        return make_response(jsonify(msg="invalid experiment type"), 404)
    
    email = request.headers.get("email")
    params = request.values.to_dict()



@chaos_bp.route('/vn_history', methods=['GET', 'DELETE'])
def vn_history_all():
    if request.method == 'GET':
        email = request.headers.get("email")
        event_list = load_all_vn_chaos(email)
        return make_response(jsonify(resp=event_list), 200)
    



@chaos_bp.route('/schedule/<exp_type>/fault_types/<fault_type>', methods=['POST'])
def add_schedule(exp_type, fault_type):
    email = request.headers.get("email")
    # generate schedule yaml and apply it
    params = request.values.to_dict()
    if "yaml" in params:
        d = yaml.load(params.get("yaml"), Loader=yaml.FullLoader)
    else:
        d = render_schedule_yaml(exp_type, fault_type, params.copy())
    # schedule_obj = render_schedule_yaml(exp_type, fault_type, params)
    # with open("chaos_template/schedule_test.yaml", "w") as f:
    #     f.write(yaml.dump(schedule_obj))
    # logger.info("schedule_obj:{}".format(schedule_obj))
    success, resp = create_chaos("schedules", d)
    logger.info("resp:{}".format(resp))
    if success:
        logger.info("created schedule, detail: {}".format(resp))
        # store schedule info to mongo
        store_schedule(email, resp['metadata']['name'],
                       params["testbed"],
                       params["namespace"],
                       params["label"],
                       get_timestamp(resp['metadata']['creationTimestamp'], "%Y-%m-%dT%H:%M:%SZ"),
                       yaml_dict=d,
                       detail=resp)
        return make_response(jsonify(msg="add schedule successfully"), 200)
    else:
        return make_response(jsonify(resp=resp.body), resp.status)


@chaos_bp.route('/schedule/<name>', methods=['PATCH'])
def patch_schedule(name: str):
    params = request.values.to_dict()
    pause = params.get("pause")
    annotate_chaos("schedules", name, "experiment.chaos-mesh.org/pause", pause)
    return make_response(jsonify(resp="Update successfully"), 200)


@chaos_bp.route('/schedule/history', methods=['GET', 'DELETE'])
def schedule_history_all():
    if request.method == 'GET':
        email = request.headers.get("email")
        schedule_list = load_all_schedules(email)
        for schedule in schedule_list:
            schedule_info = get_chaos_info("schedules", schedule['name'])
            logger.info("schedule info: {}".format(schedule_info))
            if 'annotations' in schedule_info['metadata'] \
                    and 'experiment.chaos-mesh.org/pause' in schedule_info['metadata']['annotations']:
                if schedule_info['metadata']['annotations']['experiment.chaos-mesh.org/pause'] == "true":
                    schedule['isPaused'] = True
                else:
                    schedule['isPaused'] = False
            else:
                schedule['isPaused'] = False
        logger.info("schedule list: {}".format(schedule_list))
        return make_response(jsonify(resp=schedule_list), 200)
    # delete
    name = request.values.get("name")
    if not name:
        return make_response(jsonify(msg="missing input"), 400)
    count = archive_schedule_by_name(name)
    return make_response(jsonify(msg="success" if count == 1 else "fail"), 200)


@chaos_bp.route('/events')
def get_chaos_events():
    namespace = "chaos-mesh"
    params = request.values.to_dict()
    kind = params.get("kind")
    name = params.get("name")
    event_list, _ = watch_event(namespace, kind, name)
    logger.info("events: {}".format(event_list))
    return make_response(jsonify(events=event_list), 200)


@chaos_bp.route('/archives/experiments')
def archived_experiments_handler():
    email = request.headers.get("email")
    return make_response(jsonify(get_archived_experiments(email)), 200)


@chaos_bp.route('/archives/schedules')
def archived_schedules_handler():
    email = request.headers.get("email")
    return make_response(jsonify(get_archived_schedules(email)), 200)


@chaos_bp.route('/archives/experiments/<name>', methods=['DELETE'])
def delete_archived_experiments_handler(name: str):
    delete_archived_experiment(name)
    resp = make_response()
    resp.status_code = 204
    return resp


@chaos_bp.route('/archives/schedules/<name>', methods=['DELETE'])
def delete_archived_schedules_handler(name: str):
    delete_archived_schedule(name)
    resp = make_response()
    resp.status_code = 204
    return resp
