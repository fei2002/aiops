from flask import Blueprint, make_response, jsonify, request
from service.chart import schedule

chart_bp = Blueprint('chart', __name__, url_prefix='/chart')


@chart_bp.route('/<task_id>')
def get_chart(task_id):
    start_time = request.args.get("start_time")
    end_time = request.args.get("end_time")
    print(start_time, end_time)
    chart = schedule.load_chart(task_id,start_time,end_time)
    return make_response(jsonify(chart), 200)
