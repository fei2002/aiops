from langchain_openai import ChatOpenAI
from langchain.agents import Tool
from langgraph.prebuilt import create_react_agent
from langchain.tools import StructuredTool
import os
import re

os.environ["OPENAI_API_KEY"] = "sk-908f6515959a49649e68808676f01c80"  # 可以是任意字符串
os.environ["OPENAI_API_BASE"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # 你的 Qwen OpenAI 接口地址

def deploy_virtualnetwork(subnetCount, routerCount, subnetRouterConnections, routerConnections, switchCounts, switchConnections, hostCounts, subnetRouterSwitchConnections):
    parameters = {}
    # frontend_routerConnections = []
    # for i in range(1, routerCount):
    #     connections = []
    #     for j in range(routerCount - i):
    #         connections.append(0)
    #     frontend_routerConnections.append(connections)

    # for con in routerConnections:
    #     idx1 = int(con.split(' ')[0][1:])
    #     idx2 = int(con.split(' ')[1][1:])
    #     if idx1 < idx2:
    #         r1_idx = idx1 - 1
    #         r2_idx = idx2 - 2
    #     else:
    #         r1_idx = idx2 - 1
    #         r2_idx = idx1 - 2

    #     frontend_routerConnections[r1_idx][r2_idx - r1_idx] = 1

    # sw_st_idx = 0
    # frontend_switchConnections = []
    # cnt = 0      
    # for i in range(len(switchCounts)):
    #     for j in range(1, switchCounts[i]):
    #         connections = []
    #         for k in range(switchCounts[i] - j):
    #             connections.append(0)
    #         frontend_switchConnections.append(connections)

    # for i in range(len(switchCounts)):
    #     flag = True
    #     for con in switchConnections[i]:
    #         idx1 = int(con.split(' ')[0][2:])
    #         idx2 = int(con.split(' ')[1][2:])
    #         if i > 0:
    #             if flag:
    #                 cnt += 1
    #                 #sw_st_idx += switchCounts[i - 1]
    #                 flag = False
    #             # idx1 += sw_st_idx
    #             # idx2 += sw_st_idx
    #         if idx1 < idx2:
    #             sw1_idx = idx1 - 1
    #             sw2_idx = idx2 - 2
    #         else:
    #             sw1_idx = idx2 - 1
    #             sw2_idx = idx1 - 2

    #         frontend_switchConnections[sw1_idx - cnt][sw2_idx - sw1_idx] = 1


    parameters["subnetCount"] = subnetCount
    parameters["routerCount"] = routerCount
    parameters["subnetRouterConnections"] = subnetRouterConnections
    parameters["routerConnections"] = routerConnections
    parameters["switchCounts"] = switchCounts
    parameters["switchConnections"] = switchConnections
    parameters["hostCounts"] = hostCounts
    parameters["subnetRouterSwitchConnections"] = subnetRouterSwitchConnections
    return parameters


tools = [
    StructuredTool.from_function(
        name="deploy_virtualnetwork",
        func=deploy_virtualnetwork,
        description="部署一个虚拟网络(拥有路由器, 交换机和主机三种类型的节点, 且网络里所有主机、交换机和路由器的数量之和必须大于等于15, 小于等于30个)。" \
        "首先你需要自主生成下述所有参数的值。若用户在输入文本里规定了某些参数的值, 则不需要生成该参数的值, 直接采用用户规定的值。所有参数含义描述如下: " \
        "subnetCount(子网数量, 整数), routerCount(路由器数量, 整数), " \
        "subnetRouterConnections(记录子网和路由器相连的一维字符串类型的数组。其第一个元素, 表示子网1和名称为第一个元素的路由器相连, 以此类推), " \
        "routerConnections(记录所有路由器之间的连接方式的一维字符串类型的数组, 存储所有路由器之间的连接对, 空格分隔。若routerConnections = ['r1 r2', 'r2 r3'], 则r1和r2, r2和r3相连), "\
        "switchCounts(一维整数数组, 记录每个子网内交换机的数量), " \
        "switchConnections(记录每个子网内的所有交换机之间的连接方式的二维字符串类型的数组。switchConnections的第一维表示子网号, 第二维是存储当前子网内所有交换机之间的连接对。" \
        "比如switchConnections的第一个元素为['sw1 sw2', 'sw2 sw3'], 表示子网1内sw1和sw2, sw2和sw3相连,), " \
        "比如其第二个元素为['sw4 sw5'], 表示子网2内sw4和sw5相连。" \
        "hostCounts(记录每个子网内, 每个交换机相连的主机数量的二维整数数组(里面的数必须均大于0)。 比如hostCounts[i][j] = 3(i, j从0开始), 表示子网i + 1内的第j + 1个交换机相连的主机个数为3), " \
        "subnetRouterSwitchConnections(记录每个子网内第几个交换机与连接该子网的路由器相连的一维整数数组, 每个子网内固定有1个交换机与路由器相连)。" \
        "进行参数补充生成时, 需要满足下述条件: " \
        "虚拟网络的拓扑图是连通图; 不存在主机和路由器、以及主机和主机之间的连接; 网络里所有主机、交换机和路由器的数量之和必须大于等于15, 小于等于30" \
        "输出格式: 先输出***并换行, 然后以json格式输出字典parameters里的所有内容(需要有必要的换行和缩进), 最后输出***并换行"
    )
]

llm = ChatOpenAI(
    model="qwen-plus",    
    temperature=0,
    openai_api_base=os.environ["OPENAI_API_BASE"],
    openai_api_key=os.environ["OPENAI_API_KEY"]
)


agent = create_react_agent(
    model=llm,
    tools=tools
)

def call_llm_generate_vn_parameters(query: dict):
    response = agent.invoke(query)
    text = response["messages"][-1].content
    pattern = r'\*\*\*(.*?)\*\*\*'
    matches = re.findall(pattern, text, re.DOTALL)
    if not matches:
        pattern = r'```(.*?)```'
        matches = re.findall(pattern, text, re.DOTALL)

    content_list = [match.strip() for match in matches if match.strip()]
    pattern = r'\{.*?\}'
    for content in content_list:
        matches = re.findall(pattern, content, re.DOTALL)
    content_list = [match.strip() for match in matches if match.strip()]
    return text, content_list