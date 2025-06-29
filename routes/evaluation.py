from flask import Blueprint, request, make_response, jsonify
from flask import g
from loguru import logger
from sklearn.metrics import *

from config.config import mongo_client_eval, mongo_client_chaos

evaluation_bp = Blueprint('evaluation', __name__, url_prefix='/anomaly_detection/evaluation')


@evaluation_bp.before_request
def get_data_for_evaluation():
    task = request.args.get("task")
    target = request.args.get("target")
    start_time = request.args.get("start_time")
    end_time = request.args.get("end_time")

    if not all([task, target, start_time, end_time]):
        return make_response(jsonify(msg="Input cannot be empty"), 400)

    start_time = int(start_time)
    end_time = int(end_time)
    if start_time >= end_time:
        return make_response(jsonify(msg="Start time should be earlier than end time"), 400)

    # transform target: node_metrics_aiops-k8s1  aiops-k8s1
    if "node_metrics" in target:
        target = target.split("_")[-1]

    # transform target: service_metrics_sock-shop_orders sock-shop-orders
    if "service_metrics" in target:
        target = target.split("_")[-2] + "-" + target.split("_")[-1]

    logger.info("task:{} \t target:{} \t start_time:{} \t end_time:{}".format(task, target, start_time, end_time))

    '''
        从 chaos 库中获取所有的异常注入信息
    '''

    # 设置查询语句,查询 start_time 和 end_time 之间的所有异常注入信息,这里限制会多一些，要求再这个区间内异常注入必须结束
    # todo 这里的查询条件可能需要修改,因为异常注入的结束时间可能不准确

    query = {
        "target": target,
        # "start_time": {"$gte": start_time, "$lte": end_time},
        # "end_time": {"$gte": start_time, "$lte": end_time}
    }

    records = list(mongo_client_chaos.get_all("chaos", query))
    logger.info("chaos records:{}".format(records))

    '''
        从 eval 库中获取所有的预测结果
    '''

    # 只查询 start_time 和 end_time 之间的所有预测结果,只需要预测结果字段
    query = {
        "timestamp": {"$gte": start_time, "$lte": end_time},
    }
    db = mongo_client_eval.db
    predict_val = list(db[task].find(query, {"_id": 0, "predicted": 1, "timestamp": 1}))

    chaos_span = [(r['start_time'], r['end_time']) for r in records]
    for record in predict_val:
        # 如果这个事件点在异常注入区间内，则标记为异常
        record_time = record["timestamp"]
        ground_truth = int(any([start_time <= record_time <= end_time for start_time, end_time in chaos_span]))
        record["ground_truth"] = ground_truth

    g.predicted = [record["predicted"] for record in predict_val]
    g.ground_truth = [record["ground_truth"] for record in predict_val]

    logger.info("ground_truth:{} \t predicted:{}".format(str(g.ground_truth), str(g.predicted)))


@evaluation_bp.route('/precision')
def precision():
    logger.info("ground_truth:{} \t predicted:{}".format(str(g.ground_truth), str(g.predicted)))
    return make_response(jsonify(data=precision_score(g.ground_truth, g.predicted)), 200)


@evaluation_bp.route('/recall')
def recall():
    logger.info("ground_truth:{} \t predicted:{}".format(str(g.ground_truth), str(g.predicted)))
    return make_response(jsonify(data=recall_score(g.ground_truth, g.predicted)), 200)


@evaluation_bp.route('/f1')
def f1():
    logger.info("ground_truth:{} \t predicted:{}".format(
        str(g.ground_truth), str(g.predicted)))
    return make_response(jsonify(data=f1_score(g.ground_truth, g.predicted)), 200)
