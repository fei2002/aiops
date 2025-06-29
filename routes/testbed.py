# 管理测试环境的生命周期，集成多种功能
import uuid
import yaml
import os
import time
from flask import Blueprint, request, make_response, jsonify
from langchain.chains.question_answering.map_reduce_prompt import messages

from service.file import read_file
from service.testbed import *
from service.chaos import *
from service.k8s import *
from service.topology import *
from service.llm import *

testbed_bp = Blueprint('testbed', __name__, url_prefix='/testbed')

# 4.处理api请求
@testbed_bp.route('/namespaces/<namespace>/history', methods=['GET'])
def history_handler(namespace: str):
    history = get_all_history(namespace)
    return make_response(jsonify(historys=history),200)

@testbed_bp.route('/namespaces/delete/history',methods=['DELETE'])
def delete_namespace_handler():
    namespace=request.args.get("namespace")
    id = request.args.get("id")
    if not namespace or not id:
        return make_response(jsonify(msg="missing required parameters"), 400)
    try:
        id = ObjectId(id)
        resp = delete_one_history(namespace,id)
    except Exception as e:
        # 捕获异常并返回适当的错误响应
        return make_response(jsonify(msg=f"invalid input: {str(e)}"), 400)
    resp = make_response()
    resp.status_code=204
    return resp

@testbed_bp.route('/namespaces/<namespace>/create_router', methods=['POST'])
def create_router(namespace:str):
    try:
        ids=request.form.get("ids")
        sids=request.form.get("sids")
        hids=request.form.get("hids")

        resp=add_single(namespace,ids,sids,hids)
        if not resp.startswith("True"):
            return make_response(jsonify(message=resp), 400)
        else:
            return make_response(jsonify(message=f"success:,{resp}"), 200)
    except Exception as e:
        return make_response(jsonify(error=str(e),message=f"uabi,{resp}"),500)
@testbed_bp.route('/namespaces/<namespace>/create_links', methods=['POST'])
def create_link(namespace:str):
    rr=request.form.get("rr")
    rs = request.form.get("rs")
    resp=create_router_links(namespace,rr,rs)
    if resp!="True":
        return make_response(jsonify(error=resp), 400)

    ss=request.form.get("ss")
    sh=request.form.get("sh")
    resp=create_switch_links(namespace,ss,sh)
    if resp!="True":
        return make_response(jsonify(error=resp), 400)
    return make_response(jsonify(message="success"),200)

@testbed_bp.route('/namespaces', methods=['GET', 'POST'])
def namespace_handler():
    email = request.headers.get("email")
    if not email:
        return make_response(jsonify(message="email cannot be empty"), 400)
    if request.method == 'GET':
        testbeds = get_user_testbed(email)
        return make_response(jsonify(testbeds=testbeds), 200)
    else:
        benchmark_email = request.form.get("benchmarkEmail")
        benchmark = request.form.get("benchmark")
        name = request.form.get("name")
        if not benchmark_email or not benchmark or not name:
            return make_response(jsonify(message="benchmark email, benchmark and name cannot be empty"), 400)
        # check number of testbed, maximum is 2
        testbeds = get_user_testbed(email)
        if len(testbeds) == 2:
            return make_response(jsonify(message="Currently, each user can create a maximum of two testbeds."), 403)
        # create unique namespace
        uid = str(uuid.uuid4())
        uid_arr = uid.split('-')
        ns = "{}-{}".format(benchmark, "-".join(uid_arr[:3]))
        logger.info("namespace: {}".format(ns))
        create_namespace(ns)

        # add_istioInjection(ns)

        # set cpu and memory limit range for each container
        # yaml_content = read_file("limit-range.yaml")
        # for yaml_dict in yaml.safe_load_all(yaml_content):
        #    success, resp = apply_from_yaml(yaml_dict, ns)
        #    if not success:
        #        delete_namespace(ns)
        #        return make_response(jsonify(message=resp.body), resp.status)
        # yaml_obj = read_yaml("memory-limit-range.yaml")
        # success, resp = apply_from_yaml(yaml_obj, ns)
        # if not success:
        #     delete_namespace(ns)
        #     return make_response(jsonify(resp.body), resp.status)

        # deploy requested microservice
        success, resp = deploy_benchmark(benchmark_email, benchmark, ns, "microservices")
        if not success:
            delete_namespace(ns)
            return make_response(jsonify(message=resp.body), resp.status)

        # deploy crawler
        success, resp = deploy_crawler(ns)
        if not success:
            delete_namespace(ns)
            return make_response(jsonify(message=resp.body), resp.status)

        # store info to mongo (email, namespace, microservice)
        store_user_testbed(email, ns, benchmark_email, benchmark, name)
        return make_response(jsonify(message="success"), 200)


@testbed_bp.route('/vn_namespaces', methods=['POST'])  # 意图部署虚拟网络
def vn_namespace_handler():
    email = request.headers.get("email")
    if not email:
        return make_response(jsonify(message="email cannot be empty"), 400)
    benchmark_email = request.form.get("benchmarkEmail")
    benchmark = request.form.get("benchmark")
    name = request.form.get("name")
    topologyYaml = request.form.get("topologyYaml")
    podYaml = request.form.get("podYaml")
    if not benchmark_email or not benchmark or not name or not topologyYaml or not podYaml:
        return make_response(jsonify(message="benchmark email, benchmark, name, topologyYaml and podYaml cannot be empty"), 400)
    testbeds = get_user_testbed(email)
    if len(testbeds) == 2:
        return make_response(jsonify(message="Currently, each user can create a maximum of two testbeds."), 403)
    uid = str(uuid.uuid4())
    uid_arr = uid.split('-')
    ns = "{}-{}".format(benchmark, "-".join(uid_arr[:3]))
    logger.info("namespace: {}".format(ns))
    create_namespace(ns)

    add_istioInjection(ns) # 用kiali采集拓扑图数据，需要注入istio

    generate_flow_controller_Yaml = generate_flow_controller_yaml(namespace=ns)
    success = create_serviceaccount_and_rolebinding_for_namespace(namespace=ns)
    if not success:
        delete_namespace(ns)
        return make_response(jsonify(message=resp.body), resp.status)
    
    success, resp = deploy_vn(namespace=ns, topologyYaml=topologyYaml, podYaml=podYaml, gfcYaml=generate_flow_controller_Yaml)
    if not success:
        delete_namespace(ns)
        return make_response(jsonify(message=resp.body), resp.status)

    success, resp = deploy_crawler(ns)
    if not success:
        delete_namespace(ns)
        return make_response(jsonify(message=resp.body), resp.status)

    store_user_vn_testbed(email, ns, benchmark_email, benchmark, name)
    return make_response(jsonify(message="success"), 200)


@testbed_bp.route('/namespaces/<namespace>', methods=['DELETE'])
def user_namespace_handler(namespace: str):
    email = request.headers.get("email")
    if not email or not namespace:
        return make_response(jsonify(message="email or namespace cannot be empty"), 400)
    # delete the namespace in the cluster
    delete_namespace(namespace)
    # delete testbed info in mongo
    delete_testbed(email, namespace)
    # delete related dataset info in mongo ：metrics, logs, traces, graph
    delete_dataset(namespace)
    # delete crawler
    delete_crawler(namespace)
    # delete related chaos
    delete_chaos_by_namespace(namespace)
    resp = make_response()
    resp.status_code = 204
    return resp


@testbed_bp.route('/namespaces/<namespace>/<resource>')
def get_resources(namespace: str, resource: str):
    if not namespace or not resource:
        return make_response(jsonify(message="email, namespace or resource cannot be empty"), 400)
    if resource == "pods":
        return make_response(jsonify(resources=list_pods(namespace)), 200)
    elif resource == "services":
        return make_response(jsonify(resources=list_services(namespace)), 200)
    elif resource == "ingresses":
        return make_response(jsonify(resources=list_ingresses(namespace)), 200)
    # elif resource == "configmaps":
    #     return make_response(jsonify(resources=list_configmaps(namespace)), 200)
    # elif resource == "secrets":
    #     return make_response(jsonify(resources=list_secrets(namespace)), 200)
    elif resource == "topology":
        return make_response(jsonify(resources=list_topology(namespace)), 200)
    elif resource == "llm_context":
        return make_response(jsonify(resources=list_llm_context(namespace)), 200)
    else:
        return make_response(jsonify(message="resource {} is not supported".format(resource)), 400)


@testbed_bp.route('/namespaces/<namespace>/topology/<elementType>', methods=['POST', 'DELETE'])
def add_or_delete_topology_element(namespace: str, elementType: str):
    if not namespace or not elementType:
        return make_response(jsonify(message="email, namespace or resource cannot be empty"), 400)
    # add topology node or link
    if request.method == 'POST':
        if elementType == "node":
            old_node_name = request.form.get("old_node_name")
            action = request.form.get("action")
            success = None
            if action == "addhost":
                success = add_host(namespace=namespace, switchname=old_node_name)
            elif action == "addswitch":
                if old_node_name.startswith("sw"):
                    success = add_switch_for_switch(namespace=namespace, oldswitchname=old_node_name)
                elif old_node_name.startswith("fw"):
                    success = add_switch_for_firewall(namespace=namespace, firewallname=old_node_name)
                elif old_node_name.startswith("r"):
                    success = add_switch_for_router(namespace=namespace, routername=old_node_name)
            elif action == "addrouter":
                success = add_router(namespace=namespace, oldroutername=old_node_name)
            elif action == "addfirewall":
                if old_node_name.startswith("r"):
                    success = add_firewall_for_router(namespace=namespace, routername=old_node_name)
            else:
                return make_response(jsonify(message="action {} is not supported".format(action)), 400)

            resp = make_response()
            if success == None:
                resp.status_code = 500
            else:
                resp.status_code = 204
            return resp
        
        elif elementType == "link":
            success = False
            source = request.form.get("source")
            target = request.form.get("target")
            success = add_connection(namespace=namespace, pod1=source, pod2=target)
            
            if not success:
                resp = make_response(jsonify(message="Cannot add link between {}-{}".format(source, target)), 400)
            else:
                resp = make_response()
                resp.status_code = 204
            return resp
        else:
            return make_response(jsonify(message="element type {} is not supported".format(elementType)), 400)
        
    else:
    # delete topology node or link
        if elementType == "link":
            source = request.args.get("source")
            uid = request.args.get("id")
            target = request.args.get("target")
            success = delete_connection(namespace=namespace, pod1=source, pod2=target)
            # success1 = delete_topology_link(namespace=namespace, name=source, uid=uid)
            # success2 = delete_topology_link(namespace=namespace, name=target, uid=uid)
            resp = make_response()
            if not success:
                resp.status_code = 500
            else:
                resp.status_code = 204
            return resp
        elif elementType == "node":
            pod_name = request.args.get("id")
            action = request.args.get("action")
            # success = delete_topology_node(namespace=namespace, name=uid)
            success = False
            if action == "deletehost":
                success = delete_host(namespace=namespace, hostname=pod_name)
            elif action == "deleteswitch":
                success = delete_switch(namespace=namespace, switchname=pod_name)
            elif action == "deletefirewall":
                success = delete_firewall(namespace=namespace, firewallname=pod_name)
            elif action == "deleterouter":
                success = delete_router(namespace=namespace, routername=pod_name)
            resp = make_response()
            if not success:
                resp.status_code = 500
            else:
                resp.status_code = 204
            return resp
        else:
            return make_response(jsonify(message="element type {} is not supported".format(elementType)), 400)


@testbed_bp.route('/namespaces/<namespace>/topology/reboot', methods=['POST'])
def reboot_topology_element(namespace: str):
    if not namespace:
        return make_response(jsonify(message="namespace cannot be empty"), 400)
    node_name = request.form.get("node_name")

    success = False
    if node_name.startswith("sw"):
        success = reboot_switch(namespace=namespace, switchname=node_name)
    elif node_name.startswith("r"):
        success = reboot_router(namespace=namespace, routername=node_name)

    resp = make_response()
    if not success:
        resp.status_code = 500
    else:
        resp.status_code = 204
    return resp


@testbed_bp.route('/namespaces/<namespace>/topology/getpodinfo', methods=['POST'])
def get_topology_element_info(namespace: str):
    if not namespace:
        return make_response(jsonify(message="namespace cannot be empty"), 400)
    
    action = request.form.get("action")
    response = None
    if action == "info":
        pod_name = request.form.get("source")
        response = get_pod_interfaces_info(namespace=namespace, pod_name=pod_name)
        logger.info(jsonify(resources=response))
    else:
        source = request.form.get("source")
        target = request.form.get("target")
        response = exec_ping_or_traceroute_command(namespace=namespace, source=source, target=target, action=action)
        
    if response is not None:
        return make_response(jsonify({"resources": response}), 200)
    else:
        return make_response(jsonify(message="Error getting podinfo"), 400)


@testbed_bp.route('/namespaces/<namespace>/ingresses', methods=['POST'])
def create_ingress(namespace: str):
    service = request.form.get("service")
    port = request.form.get("port", type=int)
    ingress_obj = read_yaml("ingress-template.yaml")
    ingress_obj['metadata']['name'] = service
    ingress_obj['metadata']['namespace'] = namespace

    rule = ingress_obj['spec']['rules'][0]  # 提取rule字典方便操作
    host_str = rule['host']
    prefix_host = "{}-{}".format(service, namespace)
    rule['host'] = host_str.format(prefix_host)
    logger.info("host result: {}".format(rule['host']))
    service_obj = rule['http']['paths'][0]['backend']['service']
    service_obj['name'] = service
    service_obj['port']['number'] = port
    success, response = apply_from_yaml(ingress_obj, namespace)
    if not success:
        return make_response(jsonify(response.body), response.status)
    return make_response(jsonify(message="success"), 200)


@testbed_bp.route('/namespaces/<namespace>/adjustconnection', methods=['POST'])
def adjust_connection(namespace: str, pod1: str, pod2: str, pod3: str):  # pod1-pod2 => pod1-pod3
    is_deleted = delete_connection(namespace=namespace, pod1=pod1, pod2=pod2)
    is_added = False
    if is_deleted:
        is_added = add_connection(namespace=namespace, pod1=pod1, pod2=pod3)
    else:
        return False
    
    if is_added:
        return True
    return False
            

@testbed_bp.route('/namespaces/<namespace>/llm', methods=['POST'])
def queryLLM(namespace: str):
    if not namespace:
        return make_response(jsonify(message="namespace cannot be empty"), 400)
    
    
    query = request.form.get("query")
    store_llm_context(namespace=namespace, role="user", content=query)
    text, router_config_limit_list = call_llm({"messages": [{"role": "user", "content": f"{query} namespace: {namespace}"}]})
    logger.info(f"LLM reply: {text}")
    result_dict = {}
    for content in router_config_limit_list:
        try:
            parsed_content = json.loads(content)
            result_dict.update(parsed_content)
        except json.JSONDecodeError:
            logger.warning(f"Unresolvable content: {content}")

    # 将result_dict发给路由配置控制器

    # 将大模型回复发送给前端
    if text:
        store_llm_context(namespace=namespace, role="assistant", content=text)
        return make_response(jsonify({"message": text}), 200)
    else:
        return make_response(jsonify(message="Error calling LLM"), 400)
    

@testbed_bp.route('/namespaces/<namespace>/vn_chaos/getselectedTargetPodNames', methods=['POST'])
def getselectedTargetPodNames(namespace: str):
    if not namespace:
        return make_response(jsonify(message="namespace cannot be empty"), 400)
    
    selectedTarget = request.form.get("selectedTarget")
    return make_response(jsonify(selectedTargetPodNames=list_selectedTargetPodNames(selectedTarget=selectedTarget, namespace=namespace)), 200)


@testbed_bp.route('/namespaces/<namespace>/vn_chaos/getselectedTargetPodInterfaces', methods=['POST'])
def getselectedTargetPodInterfaces(namespace: str):
    if not namespace:
        return make_response(jsonify(message="namespace cannot be empty"), 400)
    
    targetpodname = request.form.get("targetpodname")
    return make_response(jsonify(targetPodInterfaceNames=list_selectedTargetPodInterfaces(targetpodname=targetpodname, namespace=namespace)), 200)

# 重点！！！
@testbed_bp.route('/namespaces/<namespace>/vn_chaos/injectVN_Chaos', methods=['POST'])
def injectVN_Chaos(namespace: str):
    if not namespace:
        return make_response(jsonify(message="namespace cannot be empty"), 400)

    selectedpodName = request.form.get("selectedpodName")
    selectedFaultType = request.form.get("selectedFaultType")
    targetInterfaceName = request.form.get("targetInterfaceName")

    command = []
    if selectedFaultType == "Set interface down":
        command = ["ip", "link", "set", targetInterfaceName, "down"]
    elif selectedFaultType == "Disable MAC table automatic learning":
        command = ["ovs-ofctl", "mod-port", "br0", targetInterfaceName, "no-flood"]
    elif selectedFaultType == "Clear flow table":
        command = ["ovs-ofctl", "del-flows", "br0"]
    elif selectedFaultType == "Close STP": 
        command = ["ovs-vsctl", "set", "Bridge", "br0", "stp_enable=false"]
    elif selectedFaultType == "Kill ovs-vswitchd":
        command = ["pkill", "-9", "ovs-vswitchd"]
    elif selectedFaultType == "Kill ovsdb-server":
        command = ["pkill", "-9", "ovsdb-server"]
    elif selectedFaultType == "Destroy ovs database":
        command = ["rm", "/etc/openvswitch/conf.db"]
    else:
        return make_response(jsonify(message=f"faultType {selectedFaultType} is not supported."), 400)
    
    success = injectVN_Chaos_service(namespace, selectedpodName, command)
    if success:
        # 1.储存VN_CHAOS
        cur = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        store_vn_chaos(namespace,selectedFaultType,selectedpodName,targetInterfaceName,cur)
        return make_response(jsonify(message="VN_Chaos inject successfully"), 200)
    else:
        return make_response(jsonify(message="Failed to inject VN_Chaos"), 400)


@testbed_bp.route('/namespaces/<namespace>/load_test', methods=['POST', 'DELETE'])
def load_test_handler(namespace: str):
    email = request.headers.get("email")
    if request.method == 'POST':
        benchmark_email = request.form.get("benchmarkEmail")
        benchmark = request.form.get("benchmark")
        if not benchmark_email or not benchmark:
            return make_response(jsonify(message="benchmark or benchmark email cannot be empty"), 400)
        # deploy requested load test
        success, response = deploy_benchmark(benchmark_email, benchmark, namespace, "load-test")
        if not success:
            return make_response(jsonify(response.body), response.status)
        # set load test deployed
        set_load_deployed(email, namespace)
        return make_response(jsonify(message="deployed load test"), 200)
    else:
        logger.debug("request args: {}".format(request.args.to_dict()))
        benchmark_email = request.args.get("benchmarkEmail")
        benchmark = request.args.get("benchmark")
        logger.debug("benchmark: {} benchmark email:{}".format(benchmark, benchmark_email))
        if not benchmark_email or not benchmark:
            return make_response(jsonify(message="benchmark or benchmark email cannot be empty"), 400)
        delete_load(email, namespace, benchmark_email, benchmark)
        resp = make_response()
        resp.status_code = 204
        return resp


