import zipfile
from os.path import abspath, dirname

from flask import Blueprint, request, make_response, jsonify, send_file
from loguru import logger
from config.config import DOWNLOAD_TEMP_DIR
from service.testbed import *
from service.file import *
from service.llm_generate_vn_parameters import *

benchmark_bp = Blueprint('benchmark', __name__, url_prefix='/benchmark')


@benchmark_bp.route('', methods=['GET', 'POST'])
def benchmarks_handler():
    # GET：获取公共或私有基准测试列表
    # POST：创建新的基准测试
    email = request.headers.get("email")
    if request.method == 'GET':
        visibility = request.args.get("visibility")
        if visibility == "public":
            benchmarks = get_public_benchmarks()
        elif visibility == "private":
            benchmarks = get_private_benchmarks(email)
        else:
            return make_response(jsonify(message="support visibility: public, private"), 400)
        return make_response(jsonify(benchmarks=benchmarks), 200)
    else:  # post
        # store email, benchmark name, visibility (0 for private and 1 for public) and hasLoad
        # 创建名称、可见性、描述和是否包含负载的基准测试
        benchmark_name = request.form.get("name")
        visibility = request.form.get("visibility")
        description = request.form.get("description")
        has_load = request.form.get("hasLoad")
        if not benchmark_name or not visibility or not description or not has_load:
            return make_response(jsonify(message="name, description, visibility or hasLoad cannot be empty"), 400)
        if has_load == "true":
            has_load = True
        else:
            has_load = False
        create_benchmark(email, benchmark_name, visibility, description, has_load)
        return make_response(jsonify(message="created benchmark",
                                     benchmark=benchmark_name))

# 配置集详情管理
# get method needs email in the url
# other methods extract email from header
@benchmark_bp.route('/<email>/<benchmark_name>')
def benchmark_info_handler(email: str, benchmark_name: str):
    exists = request.args.get("exists", type=bool)
    if exists:
        result = benchmark_exists(email, benchmark_name)
        return make_response(jsonify(exists=result), 200)
    else:
        files = get_benchmark_files(email, benchmark_name)
        return make_response(jsonify(files=files), 200)


@benchmark_bp.route('/<benchmark_name>', methods=['POST', 'PUT', 'DELETE'])
def own_benchmark_handler(benchmark_name: str):
    if request.method == 'POST':  # post
        email = request.headers.get("email")
        # store email, benchmark name, file type, path and content
        form_data = request.form.to_dict()
        # add annotation "name: service_name" for services if they don't have
        # store in the database
        store_benchmark_file(email, benchmark_name, form_data)
        return make_response(jsonify(message="stored benchmark files", benchmark={
            "benchmarkName": benchmark_name,
            "email": email
        }), 200)
    elif request.method == 'PUT':  # put file list in json format
        email = request.headers.get("email")
        # store email, benchmark name, file type, path and content
        data = json.loads(request.get_data())
        mongo_client_platform_meta.perform_transaction(replace_benchmark_files, email, benchmark_name, data["files"])
        return make_response(jsonify(message="replaced benchmark files", benchmark={
            "benchmarkName": benchmark_name,
            "email": email
        }), 200)
    else:  # delete
        email = request.headers.get("email")
        # delete info data and files
        mongo_client_platform_meta.perform_transaction(delete_benchmark, email, benchmark_name)
        return make_response('', 204)


@benchmark_bp.route('/<email>/<benchmark_name>/files')
def download_benchmark_files(email: str, benchmark_name: str):
    # get files record
    files = get_benchmark_files(email, benchmark_name)
    # write files into a temp folder
    file_path_list = []
    if not os.path.exists(DOWNLOAD_TEMP_DIR):
        os.mkdir(DOWNLOAD_TEMP_DIR)
    for file in files:
        dir_path = DOWNLOAD_TEMP_DIR
        # create subdirectory
        if file["dirPath"] != "":
            dir_path = os.path.join(DOWNLOAD_TEMP_DIR, file["dirPath"])
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)
        # create file
        file_path = os.path.join(dir_path, file["fileName"])
        file_path_list.append(file_path)
        logger.info("file path: {}".format(file_path))
        with open(file_path, "w") as f:
            f.write(file["fileContent"])
    # read the folder and write into the zip file
    zip_path = os.path.join(DOWNLOAD_TEMP_DIR, benchmark_name + ".zip")  # determine the name of downloaded file
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path in file_path_list:
            zipf.write(file_path, os.path.relpath(file_path, DOWNLOAD_TEMP_DIR))
    response = send_file(zip_path, as_attachment=True, mimetype="application/zip")
    logger.info("response :{}".format(response))
    os.remove(zip_path)
    delete_folder(DOWNLOAD_TEMP_DIR)
    return response


@benchmark_bp.route('/<email>/<benchmark_name>/dir/<dir>')
def benchmark_dir_handler(email: str, benchmark_name: str, dir: str):
    if dir == "root":  # 根目录在mongo中的dir为空
        dir = ""

    return make_response(jsonify(files=get_benchmark_files_under_dir(email, benchmark_name, dir)), 200)


@benchmark_bp.route('/<email>/<benchmark_name>/files/<file_name>')
@benchmark_bp.route('/<email>/<benchmark_name>/files/<path:dir_path>/<file_name>')
def benchmark_file_handler(email: str, benchmark_name: str, file_name: str, dir_path=None):
    if not dir_path:
        dir_path = ""
    logger.info("dir_path: {}".format(dir_path))
    file = get_benchmark_file(email, benchmark_name, dir_path, file_name)
    return make_response(jsonify(file=file), 200)


@benchmark_bp.route('/<email>/llm/generatevnparameters', methods=['POST'])
def llm_generate_vn_parameters(email: str):
    if not email:
        return make_response(jsonify(message="email cannot be empty"), 400)
    
    query = request.form.get("query")
    logger.info(f"query: {query}")
    text, parameters_list = call_llm_generate_vn_parameters({"messages": [{"role": "user", "content": f"{query} 若上述部署要求所提供的参数不完整, 请对缺失的参数进行自动生成。"}]})
    logger.info(f"LLM reply: {text}")
    logger.info(f"parameters: {parameters_list}")
    if not parameters_list:
        return make_response(jsonify(message="LLM encountered some issues while generating configurations, please try again"), 400)
    result_dict = {}
    for content in parameters_list:
        try:
            parsed_content = json.loads(content)
            result_dict.update(parsed_content)
        except json.JSONDecodeError:
            logger.warning(f"Unresolvable content: {content}")

    if result_dict:
        return make_response(jsonify({"config": result_dict}), 200)
    else:
        return make_response(jsonify(message="Error calling LLM"), 400)
