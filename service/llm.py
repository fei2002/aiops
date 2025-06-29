from langchain_openai import ChatOpenAI
from langchain.agents import Tool
from langgraph.prebuilt import create_react_agent
from langchain.tools import StructuredTool
from service.topology import *
import os
import re
import json
import threading
import concurrent.futures
import requests
from loguru import logger


# ✅ 设置环境变量，指向你自托管的 Qwen 服务（兼容 OpenAI 接口）
os.environ["OPENAI_API_KEY"] = "sk-403fc1ab019c46188cc19f6f065a61ae"  # 可以是任意字符串
os.environ["OPENAI_API_BASE"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # 你的 Qwen OpenAI 接口地址

# ✅ 本地函数
def get_weather(city: str):
    return f"{city} 的天气是晴天，气温 27℃"

# 定义工具函数 2：股票查询
def search_stock(stock_symbol: str):
    stock_prices = {
        "AAPL": "178.52 USD",
        "TSLA": "215.23 USD",
        "BABA": "84.17 USD"
    }
    return f"{stock_symbol} 当前价格为 {stock_prices.get(stock_symbol.upper(), '未知')}"


def generate_router_config_limit(source: str, target: str, avoid_routers_str: str, cross_routers_str: str):
    avoid_routers = []
    cross_routers = []
    if avoid_routers_str:
        avoid_routers = avoid_routers_str.split(' ')
    if cross_routers_str:
        cross_routers = cross_routers_str.split(' ')
    limit = {
        f"{source}": {
            f"{target}": {
                "AVOID": avoid_routers,
                "CROSS": cross_routers
            }
        }
    }

    return limit


def generate_virtualnetwork_config_parameters(subnetCount, routerCount, subnetRouterConnections, routerConnections, switchCounts, switchConnections, hostCounts, subnetRouterSwitchConnections):
    parameters = {}
    frontend_routerConnections = []
    for i in range(1, routerCount):
        connections = []
        for j in range(routerCount - i):
            connections.append(0)
        frontend_routerConnections.append(connections)

    for con in routerConnections:
        idx1 = int(con.split(' ')[0][1:])
        idx2 = int(con.split(' ')[1][1:])
        if idx1 < idx2:
            r1_idx = idx1 - 1
            r2_idx = idx2 - 2
        else:
            r1_idx = idx2 - 1
            r2_idx = idx1 - 2

        frontend_routerConnections[r1_idx][r2_idx - r1_idx] = 1

    sw_st_idx = 0
    frontend_switchConnections = []
    cnt = 0      
    for i in range(len(switchCounts)):
        for j in range(1, switchCounts[i]):
            connections = []
            for k in range(switchCounts[i] - j):
                connections.append(0)
            frontend_switchConnections.append(connections)

    for i in range(len(switchCounts)):
        flag = True
        for con in switchConnections[i]:
            idx1 = int(con.split(' ')[0][2:])
            idx2 = int(con.split(' ')[1][2:])
            if i > 0:
                if flag:
                    cnt += 1
                    sw_st_idx += switchCounts[i - 1]
                    flag = False
                idx1 += sw_st_idx
                idx2 += sw_st_idx
            if idx1 < idx2:
                sw1_idx = idx1 - 1
                sw2_idx = idx2 - 2
            else:
                sw1_idx = idx2 - 1
                sw2_idx = idx1 - 2

            frontend_switchConnections[sw1_idx - cnt][sw2_idx - sw1_idx] = 1


    parameters["subnetCount"] = subnetCount
    parameters["routerCount"] = routerCount
    parameters["subnetRouterConnections"] = subnetRouterConnections
    parameters["routerConnections"] = frontend_routerConnections
    parameters["switchCounts"] = switchCounts
    parameters["switchConnections"] = frontend_switchConnections
    parameters["hostCounts"] = hostCounts
    parameters["subnetRouterSwitchConnections"] = subnetRouterSwitchConnections
    return parameters


def check_node_existence(nodes, exist_nodes, topology_docs, err_message):
    for node in nodes:
        flag = False
        for doc in topology_docs["items"]:
            if node == doc['metadata']['name']:
                exist_nodes.append(node)
                flag = True
                break
        if not flag:
            if not err_message:
                err_message += node
            else:
                err_message += "、" + node 
    if err_message:
        err_message += "不存在。"
    return err_message


def add_nodes_for_already_exist_nodes(nodes, namespace):  # nodes = {"sw1": [2, 1, 0], "r1": [0, 2, 2]}
    topology_docs = load_topology_yaml(namespace=namespace)
    node_names = [] 
    exist_nodes = []
    err_message = ""
    final_message = ""
    
    for node in nodes:
        if node:
            if node.startswith("host"):
                err_message += "不能向主机类型节点添加新节点。"
            else:
                node_names.append(node)
        else:
            err_message += "被添加节点名称不能为空。"

    err_message = check_node_existence(nodes=node_names, exist_nodes=exist_nodes, topology_docs=topology_docs, err_message=err_message)
    logger.info(f"exist nodes: {exist_nodes}")
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        for node in exist_nodes:
            for i in range(3):
                if node.startswith("sw"):
                    if i == 0:
                        for _ in range(nodes[node][i]):
                            futures.append(executor.submit(add_host, namespace, node))
                            time.sleep(2)
                    if i == 1:
                        for _ in range(nodes[node][i]):
                            futures.append(executor.submit(add_switch_for_switch, namespace, node))
                            time.sleep(2)
                    if i == 2 and nodes[node][i] > 0:
                        err_message += "不能为交换机添加路由器。"

                elif node.startswith("r"):
                    if i == 0 and nodes[node][i] > 0:
                        err_message += "不能为路由器直接添加主机。"
                    if i == 1:
                        for _ in range(nodes[node][i]):
                            futures.append(executor.submit(add_switch_for_router, namespace, node))
                            time.sleep(2)
                    if i == 2:
                        for _ in range(nodes[node][i]):
                            futures.append(executor.submit(add_router, namespace, node))
                            time.sleep(2)

    final_message += err_message
    suc_cnt = 0
    fail_cnt = 0
    for future in concurrent.futures.as_completed(futures):     
        if future.result() != None:
            suc_cnt += 1
        else:
            fail_cnt += 1

    if suc_cnt > 0 or fail_cnt > 0:
        final_message += f"成功添加{suc_cnt}个节点, {fail_cnt}个节点添加失败。"
    return final_message
                

def add_new_subnet(routername_list, namespace):  # 为已存在的路由器添加一个新子网, 包含一个交换机和一个主机
    if not routername_list:
        return "必须指定路由器名称。"
    
    topology_docs = load_topology_yaml(namespace=namespace)
    exist_routers = [] 
    suc_message = "" 
    err_message = ""
    final_message = ""

    err_message += check_node_existence(nodes=routername_list, exist_nodes=exist_routers, topology_docs=topology_docs, err_message=err_message)

    with concurrent.futures.ThreadPoolExecutor() as executor:  # 并行添加sw
        futures_sw = []
        for routername in exist_routers:
            futures_sw.append(executor.submit(add_switch_for_router, namespace, routername))
            time.sleep(2)

    with concurrent.futures.ThreadPoolExecutor() as executor_host:  # 并行添加host
        futures_host = []
        for future_sw in concurrent.futures.as_completed(futures_sw):    
            if future_sw.result() != None:
                suc_message += f"{future_sw.result()}添加成功。"
                futures_host.append(executor_host.submit(add_host, namespace, future_sw.result()))
                time.sleep(2)
            else:
                err_message += f"{future_sw.result()}添加失败。" 
    
    for future_host in concurrent.futures.as_completed(futures_host):   
        if future_host.result() != None:
            suc_message += f"{future_host.result()}添加成功。"
        else:
            err_message += f"{future_host.result()}添加失败。" 
    
    final_message += err_message + suc_message
    return final_message


def delete_all_hosts_for_switches(switchname_list, namespace):
    if not switchname_list:
        return "必须指定交换机名称。"
    
    topology_docs = load_topology_yaml(namespace=namespace)
    exist_switches = [] 
    suc_message = "" 
    err_message = ""
    final_message = ""

    err_message = check_node_existence(nodes=switchname_list, exist_nodes=exist_switches, topology_docs=topology_docs, err_message=err_message)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        hosts = []
        for switchname in exist_switches:
            for doc in topology_docs["items"]:
                if doc['metadata']['name'] == switchname:
                    for link in doc['spec']['links']:
                        if link['peer_pod'].startswith("host"):
                            hosts.append(link['peer_pod'])
            
            if not hosts:
                err_message += f"{switchname}没有连接主机。"
            else:
                for host in hosts:
                    futures.append(executor.submit(delete_host, namespace, hostname=host))
                    time.sleep(3)
    
    suc_cnt = 0
    fail_cnt = 0
    for future in concurrent.futures.as_completed(futures):   
        if future.result():
            suc_cnt += 1
        else:
            fail_cnt += 1

    suc_message += f"成功删除{suc_cnt}个主机。"
    err_message += f"{fail_cnt}个主机删除失败。"
    final_message += err_message + suc_message
    return final_message


def delete_nodes(nodes, namespace): 
    topology_docs = load_topology_yaml(namespace=namespace)
    exist_nodes = []
    err_message = ""
    delete_str = ""
    final_message = ""
    err_message = check_node_existence(nodes=nodes, exist_nodes=exist_nodes, 
                                     topology_docs=topology_docs, err_message=err_message)

    def delete_node_helper(node):
        if node.startswith("r"):
            return delete_router(namespace=namespace, routername=node)
        elif node.startswith("sw"):
            return delete_switch(namespace=namespace, switchname=node)
        elif node.startswith("host"):
            return delete_host(namespace=namespace, hostname=node)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(exist_nodes)) as executor:
        logger.info(f"cpu core count: {os.cpu_count()}")
        futures = {}
        
        for node in exist_nodes:
            future = executor.submit(delete_node_helper, node)
            futures[future] = node
            time.sleep(2)  # 每隔两秒触发函数执行
        
        for future in concurrent.futures.as_completed(futures):
            node = futures[future]
            try:
                result = future.result()
                logger.info(f"Node {node} deletion {'succeeded' if result else 'failed'}")
                delete_str += f"Node {node} deletion {'succeeded' if result else 'failed'}"
            except Exception as e:
                logger.error(f"Node {node} deletion failed with exception: {e}")
    
    if err_message:
        final_message += err_message
    if delete_str:
        final_message += delete_str

    return final_message

def configure_router_between_points(namespace, source, target):
    topology = load_topology_yaml(namespace=namespace)
    # Constructing a graph based on topology
    '''
        "router1": {
            "label": "route",
            "links": {
                "router2": {
                    "local_ip": "10.1.12.1/24",
                    "local_intf": "eth1",
                    "peer_ip": "10.1.12.2/24",
                    "peer_intf": "eth1"
                }
            }
        }
    '''
    graph = {}
    for item in topology["items"]:
        key = item['metadata']['name']
        value = {}
        if key[0] == 'r':
            value['label'] = 'route'
        elif key[0] == 's':
            value['label'] = 'switch'
        else:
            value['label'] = 'host'

        value['links'] = {}
        for link in item['spec']['links']:
            peer_pod = link['peer_pod']
            value['links'][peer_pod] = {
                "local_intf": link['local_intf'],
                "peer_intf": link['peer_intf']
            }        
            if 'local_ip' in link:
                value['links'][peer_pod]["local_ip"] = link['local_ip']
            else: 
                value['links'][peer_pod]["local_ip"] = ""
            if 'peer_ip' in link:
                value['links'][peer_pod]["peer_ip"] = link['peer_ip']
            else:
                value['links'][peer_pod]["peer_ip"] = ""        
        graph[key] = value

    logger.info(f"construct graph: {graph}, source: {source}, target: {target}")
    url = f"http://192.168.31.212:5000/update"
    payload = {
        "graph": graph,
        "source": source,
        "target": target
    }
    headers = {
        "Content-Type": "application/json"
    }
    
    try:
        # send post request
        response = requests.post(
            url,
            data=json.dumps(payload),  # transform payload to json string
            headers=headers
        )
        logger.info("response status code: " + str(response.status_code))
        return response
    
    except requests.exceptions.RequestException as e:
        print(f"requesst failed: {e}")
        return None
    
# 注册工具/函数
tools = [
    Tool(
        name="get_weather",
        func=get_weather,
        description="查询天气，输入参数是 city(城市名)"
    ),
    Tool(
        name="search_stock",
        func=search_stock,
        description="查询股票，输入参数是 stock_symbol(公司名)"
    ),
    StructuredTool.from_function(
        name="configure_router_between_points",
        func=configure_router_between_points,
        description="配置两个设备使他们连通。输入参数有3个, 分别是 source(源设备名称), target(终点设备名称), namespace(一个字符串, 命名空间的值, 包含在用户输入文本的最后)" \
        "输出内容包含下述内容: 根据函数返回状态码输出, 如果是200,则返回路径数组,这些数组包含从source和target之间的所有设备,输出为这些设备已配置的回复性文本,若状态码非200,提示配置出错信息"
    ),
    StructuredTool.from_function(
        name="generate_router_config_limit",
        func=generate_router_config_limit,
        description="路由限制转换, 输入参数有4个, 分别是 source(源路由器名称), target(终点路由器名称), " \
        "avoid_routers_str(不能经过的路由器名称, 若有多个路由器名称(格式为字母r加一个整数), 需要将路由器名称之间的字符或汉字去除, 之后再用一个空格将路由器名称进行拼接。该值可能为空), " \
        "cross_routers_str(必须经过的路由器名称, 若有多个路由器名称(格式为字母r加一个整数), 需要将路由器名称之间的字符或汉字去除, 之后再用一个空格将路由器名称进行拼接。该值可能为空), " \
        "输出内容包含下述内容: 先输出路由限制已添加并换行, 之后输出***并换行, 然后以json格式化(需要有必要的换行和缩进)输出路由限制转换后的字典并换行, 最后输出***并换行。这部分输出完成后可以再输出一些回复性文本"
    ),
    StructuredTool.from_function(
        name="generate_virtualnetwork_config_parameters",
        func=generate_virtualnetwork_config_parameters,
        description="部署一个虚拟网络(拥有路由器, 交换机和主机三种类型的节点, 该虚拟网络的节点总数不能超过30个)。输入参数有8个, 分别是: subnetCount(子网数量, 一个整数), routerCount(路由器数量, 一个整数), " \
        "subnetRouterConnections(记录子网和路由器相连的一维字符串类型的数组。其第一个元素, 表示子网1和名称为第一个元素的路由器相连, 以此类推), " \
        "routerConnections(记录所有路由器之间的连接方式的一维字符串类型的数组, 存储所有路由器之间的连接对, 空格分隔。比如routerConnections = ['r1 r2', 'r2 r3'], 则表示r1和r2, r2和r3相连), "\
        "switchCounts(一维整数数组, 记录每个子网内交换机的数量), " \
        "switchConnections(记录每个子网内的所有交换机之间的连接方式的二维字符串类型的数组。switchConnections的第一维表示子网号, 第二维是存储当前子网内所有交换机之间的连接对, 比如switchConnections的第一个元素为['sw1 sw2', 'sw2 sw3'], 表示子网1内sw1和sw2, sw2和sw3相连。), " \
        "hostCounts(记录每个子网内, 每个交换机相连的主机数量的二维整数数组。 比如hostCounts[i][j] = 3(i, j从0开始), 表示子网i + 1内的第j + 1个交换机相连的主机个数为3), " \
        "subnetRouterSwitchConnections(记录每个子网内第几个交换机与连接该子网的路由器相连的一维整数数组, 意味着每个子网内固定有1个交换机与路由器相连)。" \
        "注意, 用户的输入文本中几乎不会全部包含上述8个输入参数的内容, 甚至不会包含任何一个参数。此时需要你自己进行参数补充生成。" \
        "进行参数补充生成时, 需要满足下述条件: " \
        "虚拟网络的拓扑图是连通图; 不存在主机和路由器、以及主机和主机之间的连接; 虚拟网络的节点总数不能超过30个; 路由器、交换机和主机的个数尽量做到合理分配, 每个交换机连接的主机个数不能为0" \
        "输出格式: 以json格式(需要有必要的换行和缩进)输出字典parameters的所有内容"
    ),
    StructuredTool.from_function(
        name="delete_nodes",
        func=delete_nodes,
        description="删除虚拟网络中的节点。输入参数有2个, 分别是: node(一个包含用户需要删除的所有节点名称的一维字符串数组), " \
        "namespace(一个字符串, 命名空间的值, 包含在用户输入文本的最后)"
        "(节点名称有3类, 分别是: host跟一个数字, sw跟一个数字, r跟一个数字), " \
        "需要正确地将用户输入文本里所有节点名称提出来放到数组node里。" \
        "输出格式: 仅输出delete_nodes函数的返回值final_message的值。"
    ),
    StructuredTool.from_function(
        name="add_nodes_for_already_exist_nodes",
        func=add_nodes_for_already_exist_nodes,
        description="为虚拟网络中的节点添加新节点。被添加的节点类型可能是交换机和路由器(它们的名称格式分别为: sw跟一个数字, r跟一个数字), 新增节点可能是主机、交换机和路由器。" \
        "输入参数有2个, 分别是: node(一个字典。键: 被添加的节点的名称, 值: 包含3个整数的列表, 3个整数分别代表添加的主机、交换机和路由器的个数。若用户在文本中指定了添加的主机、交换机或路由器个数, 那么对应的数字就为用户指定的数量, 若未指定则为0), " \
        "namespace(一个字符串, 命名空间的值, 包含在用户输入文本的最后), " \
        "需要正确地提取用户输入文本里所有这样的键值对, 并放到字典node里。(若用户输入了主机的名称, 仍需正确理解每个交换机分别需要添加的主机数量)" \
        "输出: 仅输出函数add_nodes_for_already_exist_nodes的返回值即可。"
    ),
    StructuredTool.from_function(
        name="add_new_subnet",
        func=add_new_subnet,
        description="为指定的路由器添加一个新子网。可能有多个路由器。" \
        "输入参数有2个, 分别是 routername_list(包含所有路由器名称的一维字符串列表), " \
        "namespace(一个字符串, 命名空间的值, 包含在用户输入文本的最后), " \
        "输出: 仅输出函数add_nodes_for_already_exist_nodes的返回值即可。"
    ),
    StructuredTool.from_function(
        name="delete_all_hosts_for_switches",
        func=delete_all_hosts_for_switches,
        description="为指定的交换机添删除所有与其相连的主机。可能有多个交换机。" \
        "输入参数有2个, 分别是 switchname_list(包含所有交换机名称的一维字符串列表), " \
        "namespace(一个字符串, 命名空间的值, 包含在用户输入文本的最后)" \
        "输出: 仅输出函数delete_all_hosts_for_switches的返回值即可。"
    ),
]

llm = ChatOpenAI(
    model="qwen-plus-0806", 
    temperature=0,
    openai_api_base=os.environ["OPENAI_API_BASE"],
    openai_api_key=os.environ["OPENAI_API_KEY"]
)

agent = create_react_agent(
    model=llm,
    tools=tools
)

# 测试调用
# query =  {"messages": [{"role": "user", "content": "添加路由限制, 源路由器为r1, 终点路由器为r2, 它们之间的流量不能经过r3 r4"}]}
# response = agent.invoke(query)
# for message in response["messages"]:
#     print("🤖 模型回复：", message)
# print(response["messages"][-1].content)

def call_llm(query: dict):
    response = agent.invoke(query)
    text = response["messages"][-1].content
    pattern = r'\*\*\*(.*?)\*\*\*'
    matches = re.findall(pattern, text, re.DOTALL)
    content_list = [match.strip() for match in matches if match.strip()]

    # result_dict = {}
    # for content in content_list:
    #     try:
    #         parsed_content = json.loads(content)
    #         result_dict.update(parsed_content)
    #     except json.JSONDecodeError:
    #         print(f"无法解析的内容: {content}")
    
    return text, content_list