import time
from datetime import datetime
from typing import List

import pandas as pd
import pytz
from bson.objectid import ObjectId
from loguru import logger

from config.config import mongo_client_platform_meta, mongo_client_pure_data, mongo_client_chaos, mongo_client_eval
from service.chaos import get_pod_labels


def build_mongo_field_query(target_fields) -> dict:
    """
    convert target fields to mongo query format
    :param target_fields:  pred of target fields
    :return:  dict of target fields
    """
    if target_fields:
        target_fields = {field: 1 for field in target_fields}
        target_fields["timestamp"] = 1
    else:
        target_fields = {"timestamp": 1}
    target_fields["_id"] = 0
    return target_fields


def get_chaos(namespace, pod, start_time=0, end_time=int(time.time()), collection_name="chaos"):
    """
    从 chaos 表中获取异常信息
    :param pod:
    :param namespace:
    :param start_time:  开始时间
    :param end_time:  结束时间
    :param collection_name:  集合名
    :return: list of chaos
    """
    if namespace == "" or pod == "":
        return []

    labels = get_pod_labels(namespace, pod)
    target_collection = mongo_client_chaos.db[collection_name]
    chaos = []
    for label in labels:
        chaos.extend(
            list(
                target_collection.find(
                    {"namespace": namespace,
                     "label": label,
                     "start_time": {"$gte": start_time, "$lte": end_time}
                     }
                )
            )
        )
    return chaos


def get_schedule(task_id):
    """
    从 InferenceTask 表中获取调度信息
    :param task_id: 任务id
    :return: dict of schedule
    """
    logger.info(f"get schedule task detail, task_id: {task_id}")
    target_collection = mongo_client_platform_meta.db["InferenceTask"]
    task = target_collection.find_one({"_id": ObjectId(task_id)}, {"_id": 0})
    return task


def get_point(collection_name, target_fields=None, start_time=0, end_time=int(time.time())):
    """
    从 pure_data 表中获取数据 point
    :param collection_name:  集合名
    :param target_fields:   目标字段
    :param start_time:  开始时间
    :param end_time:  结束时间
    :return:  pandas dataframe
    """
    target_collection = mongo_client_pure_data.db[collection_name]
    point = list(target_collection.find({"timestamp": {"$gte": start_time, "$lte": end_time}}, build_mongo_field_query(target_fields)))
    # convert to pandas dataframe
    df = pd.DataFrame(point)

    df.fillna(0, inplace=True)

    # if it has 'NaN' value, fill with 0
    df.replace('NaN', 0, inplace=True)
    return df


def get_predicted(collection_name, start_time=0, end_time=int(time.time()), target_fields=None) -> list:
    """
    从 eval 表中获取预测结果
    :param collection_name: 集合名
    :param start_time:  开始时间
    :param end_time:  结束时间
    :param target_fields:  目标字段，一般是预测结果，即 predicted 这一列
    :return:  list of predicted
    """
    target_collection = mongo_client_eval.db[collection_name]
    predicted = list(target_collection.find({"timestamp": {"$gte": start_time, "$lte": end_time}}, build_mongo_field_query(target_fields)))

    # if it has NAN value, fill with 0
    for item in predicted:
        if item.get("predicted") is None:
            item["predicted"] = 0

    return predicted


def build_pred_area(pred: List[dict]):
    """
    找出异常区域,给前端显示用，格式参考 echarts 的 markArea
    格式如下： [  [{xAxis: '07:30'}, {xAxis: '10:00'}]  ]
    最外围是一个大的list，里面是一个个的异常区域，每个异常区域是一个list，里面是两个dict，分别是异常区域的起始时间和结束时间，xAxis是前端的x轴

    :param pred: 预测结果列表,列表中的每一项是一个dict，包含了预测结果的所有信息 {'timestamp': 1680438689.0, 'predicted': 0}
    :return: [[{xAxis: '07:30'}, {xAxis: '10:00'}], [{xAxis: '07:30'}, {xAxis: '10:00'}]]
    """

    resp = []
    length = len(pred)
    index_i = 0
    # 这里必须用while循环，不能用for循环，因为for循环中的index 只能连续自增 ，我们需要它跳跃更新
    while index_i < length:
        if pred[index_i]["predicted"] == 1:
            range_start = {"xAxis": timestamp_to_datetime(pred[index_i]["timestamp"])}
            end_time = 0
            # 别问我为什么这么写，问就是因为我不知道为什么，我也不想知道为什么，我只想让它能跑起来
            index_j = index_i + 1008611
            for index_j in range(index_i + 1, length):
                if pred[index_j]["predicted"] == 0:
                    end_time = timestamp_to_datetime(pred[index_j]["timestamp"])
                    break
            if end_time == 0:
                end_time = timestamp_to_datetime(pred[length - 1]["timestamp"])
            range_end = {"xAxis": end_time}
            resp.append([range_start, range_end])
            index_i = index_j + 1
        else:
            index_i += 1
    logger.info(f"build pred area: {resp}")
    return resp


def build_chaos_injected_area(chaos: list, max_time: int):
    """
    构建注入异常区域,给前端显示用
    格式如下： [  [{xAxis: '07:30'}, {xAxis: '10:00'}]  ]
    最外围是一个大的list，里面是一个个的异常区域，每个异常区域是一个list，里面是两个dict，分别是异常区域的起始时间和结束时间，xAxis是前端的x轴

    :param max_time: 真实数据的时间戳，因为异常注入是具有未来性的，所以注入的异常可能会超过真实数据的时间范围，所以需要传入真实数据的最大时间戳
    :param chaos: 注入异常列表,列表中的每一项是一个dict，包含了注入异常的所有信息 {'target': 'cpu.usage', 'start_time': 1680438689.0, 'end_time': 1680438689.0}
    :return: [[{xAxis: '07:30'}, {xAxis: '10:00'}], [{xAxis: '07:30'}, {xAxis: '10:00'}]]
    """
    max_time = int(max_time)
    resp = []
    for item in chaos:
        start_t = int(item["start_time"])
        end_t = int(item["end_time"])

        # add 1 min offset to start time and end time
        start_t += 60
        end_t += 60

        # 如果注入异常的起始时间大于真实数据的最大时间戳，那么这个异常就不用显示了
        if start_t > max_time:
            continue

        # 如果注入异常的结束时间大于真实数据的最大时间戳，那么就把结束时间设置为真实数据的最大时间戳
        if end_t > max_time:
            end_t = max_time
        range_start = {"xAxis": timestamp_to_datetime(start_t)}
        range_end = {"xAxis": timestamp_to_datetime(end_t)}
        resp.append([range_start, range_end])
    return resp


def timestamp_to_datetime(timestamp):
    """
    时间戳转换成datetime，设置时区为Asia/Shanghai
    :param timestamp: unix seconds
    :return: datetime string
    """
    # 显式指明时区，避免不必要的麻烦
    # 时间精确至分钟
    tz = pytz.timezone("Asia/Shanghai")
    return datetime.fromtimestamp(timestamp, tz=tz).strftime("%Y-%m-%d %H:%M")


def df_to_series(df) -> list:
    """
    将dataframe转换成 Echarts 需要的series格式
    :param df:  dataframe
    :return:  list of series
    """

    '''
       series: [
          {
            name: 'cpu.usage',      
            type: 'line',
            data: [
              ['2019-10-10', 200],
            ]
          }
        ]
    '''

    res = []
    for column in df.columns:
        if column == "datetime" or column == "timestamp":
            continue
        series = {
            "name": column,
            "type": "line",
            "smooth": True,
            "data": []
        }
        for index, row in df.iterrows():
            series["data"].append([timestamp_to_datetime(row["timestamp"]), row[column]])
        res.append(series)
    return res


def create_area_series(area_list, color, area_type="chaos"):
    """
    构建标记区域series
    :param area_type: 标记区域类型, chaos/pred
    :param color: 标记区域颜色
    :param area_list: 标记区域列表
    :return: series
    """
    series = {
        "name": area_type,
        "type": "line",
        # 这里的data可以是空的，不影响标记区域的显示
        "data": [],
        "markArea": {
            "silent": True,
            "itemStyle": {
                "normal": {
                    "color": color,
                }
            },
            "data": area_list
        }
    }
    return series


def load_chart(task_id: str, start_time: int, end_time: int):
    task_detail = get_schedule(task_id)
    task_name = task_detail["name"]
    collection = task_detail["dataSource"]["name"]

    try:
        namespace = task_detail["dataSource"]["properties"]["namespace"]
        pod = task_detail["dataSource"]["properties"]["pod"]
    except KeyError:
        namespace = ""
        pod = ""

    time_end = int(end_time)
    time_start = int(start_time)

    if time_start >= time_end:
        return {"code": 400, "msg": "start_time must less than end_time"}

    logger.info(f"load chart for: {task_name}, {collection}, {time_start}, {time_end}")

    fields = task_detail["trainTask"]["selectedFields"]
    logger.info(f"fields: {fields}")
    # fields = ["service_memory_usage_bytes"]

    # 拉取真实的数据点
    gt_frame = get_point(collection, fields, time_start, time_end)
    # 找到最大的时间戳
    max_timestamp = gt_frame["timestamp"].max()

    # 加一列datetime，前端显示用
    gt_frame["datetime"] = gt_frame["timestamp"].apply(timestamp_to_datetime)
    series = df_to_series(gt_frame)

    # 拉取chaos数据
    chaos_info = get_chaos(namespace, pod, start_time=time_start, end_time=time_end)
    logger.info(f"chaos info: {chaos_info}")
    chaos_area = build_chaos_injected_area(chaos_info, max_timestamp)
    logger.info(f"chaos area: {chaos_area}")

    # 拉取预测数据
    pred_list = get_predicted(task_name, start_time=time_start, end_time=time_end, target_fields=["predicted"])
    # logger.info(f"pred list: {pred_list}")

    pred_area = build_pred_area(pred_list)
    logger.info(f"pred area: {pred_area}")

    pred_series = create_area_series(pred_area, color="rgba(255, 165, 0, 0.5)", area_type="pred")
    chaos_series = create_area_series(chaos_area, color="rgba(255, 0, 0, 0.5)", area_type="chaos")

    series.append(pred_series)
    series.append(chaos_series)
    return {"series": series}


if __name__ == '__main__':
    # load_chart("6429757bb5e0f259b61b2414", start_time=int(time.time()) - 3600, end_time=int(time.time()))

    # pred_list = [{'timestamp': 1680694949.0, 'predicted': 0}, {'timestamp': 1680695009.0, 'predicted': 0},
    #              {'timestamp': 1680698189.0, 'predicted': 1}]
    # area = build_pred_area(pred_list)
    # print(area)
    pass
