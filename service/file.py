import os

import yaml
import json
from loguru import logger


# get filenames without extension under dir
def get_filenames(dir_path: str):
    return [os.path.splitext(filename)[0] for filename in os.listdir(dir_path)]


def read_yaml(path: str):
    with open(path, 'r') as f:
        yaml_obj = yaml.load(f, Loader=yaml.FullLoader)
    return yaml_obj


def read_json(path: str):
    with open(path, 'r') as f:
        json_obj = json.load(f)
    return json_obj


def read_file(path: str):
    with open(path, "r") as f:
        content = f.read()
        # 为了防止手贱格式化yaml文件，导致yaml文件中的空字典被格式化成{ }，这里做一下处理
        content.replace("{ }", "{}")
    return content


def write_file(path: str, content: str):
    with open(path, "w") as f:
        f.write(content)


def get_keys_for_empty_value(d: dict):
    for key, value in d.items():
        if isinstance(value, dict):
            if value == {}:
                yield key
            else:
                yield from get_keys_for_empty_value(value)


def delete_folder(folder_path):
    if os.path.exists(folder_path):
        # 遍历文件夹中的所有文件和子文件夹
        for root, dirs, files in os.walk(folder_path, topdown=False):
            for name in files:
                file_path = os.path.join(root, name)
                # 删除文件
                os.remove(file_path)
            for name in dirs:
                dir_path = os.path.join(root, name)
                # 删除子文件夹
                os.rmdir(dir_path)
        # 删除最外层的文件夹
        os.rmdir(folder_path)
        logger.info("文件夹删除成功！")
    else:
        logger.info("文件夹不存在！")
