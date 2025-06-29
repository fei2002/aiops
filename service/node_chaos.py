# this file contains methods to build chaos yamls
import os
import uuid

from config.config import CHAOS_HISTORY_DIR, node_address_map
from service.k8s import create_chaos
from .file import read_file, write_file, read_yaml

user_input = {
    "cpu-stress": ["load", "workers", "duration"],
    "mem-stress": ["size", "duration"]
}


def get_user_input_fields(fault_type: str) -> list:
    return user_input[fault_type]


def get_uuid_name():
    return str(uuid.uuid4())


def build_node_mem(template: str, address: str, size: str, duration: str) -> str:
    """
    build node memory stress yaml
    :param template:  yaml template
    :param address:  node address http://xxx:xxx
    :param size: memory size  The supported formats of the size are: B, KB/KiB, MB/MiB, GB/GiB, TB/TiB.
    :param duration: stress duration 持续时间支持的格式有：ms / s / m / h。例如：100ms、1.5s、2m、1h。
    :return: yaml
    """
    return template.format(get_uuid_name(), address, size, duration)


def build_node_cpu(template: str, address: str, load: int, workers: int, duration: str):
    """
    build node cpu stress yaml
    :param template:   yaml template
    :param address:  node address http://xxx:xxx
    :param load:  cpu load 0-100
    :param workers:  cpu workers
    :param duration:  stress duration 持续时间支持的格式有：ms / s / m / h。例如：100ms、1.5s、2m、1h。
    :return:  yaml
    """
    return template.format(get_uuid_name(), address, load, workers, duration)


def apply_node_chaos(fault_type: str, tmpl_path: str, params: dict) -> bool:
    content = ""
    name = "{}-{}-{}.yaml".format("node", fault_type, get_uuid_name())
    temp_path = os.path.join(CHAOS_HISTORY_DIR, name)
    address = node_address_map[params.get("node")]
    if fault_type == "cpu-stress":
        content = build_node_cpu(read_file(tmpl_path), address, params["load"], params["workers"], params["duration"])
    elif fault_type == "mem-stress":
        content = build_node_mem(read_file(tmpl_path), address, params["size"], params["duration"])

    write_file(temp_path, content)
    chaos_dict = read_yaml(temp_path)
    return create_chaos(chaos_dict["kind"].lower(), chaos_dict)


if __name__ == '__main__':
    template_dir = "/chaos_template/node"
    templates = os.listdir(template_dir)
    mock_address = "http://192.168.31.25:31767"

    chaos_examples_dir = "/home/aiops/PycharmProjects/aiops-evaluation/chaos_examples"
    if not os.path.exists(chaos_examples_dir):
        os.mkdir(chaos_examples_dir)

    for template in templates:
        file = read_file(os.path.join(template_dir, template))
        if template == "cpu-stress.yaml":
            yaml = (build_node_cpu(file, mock_address, 90, 16, "1m"))
            # save yaml to file
            with open(os.path.join(chaos_examples_dir, "cpu-stress.yaml"), "w") as f:
                f.write(yaml)
        elif template == "mem-stress.yaml":
            yaml = build_node_mem(file, mock_address, "1G", "1m")
            # save yaml to file
            with open(os.path.join(chaos_examples_dir, "mem-stress.yaml"), "w") as f:
                f.write(yaml)
