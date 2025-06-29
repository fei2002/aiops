import uuid
import yaml
import os
import time
from flask import Blueprint, request, make_response, jsonify

from service.file import read_file
from service.testbed import *
from service.chaos import *
from service.k8s import *
from service.topology import *
from service.llm import *

virtualnetwork_bp = Blueprint('virtualnetwork', __name__, url_prefix='/virtualnetwork')

# @virtualnetwork_bp.route('/<email>/llm/generatevnparameters', methods=['POST'])
# def llm_generate_vn_parameters(email: str):
#     if not email:
#         return make_response(jsonify(message="email cannot be empty"), 400)
    
#     query = request.form.get("query")
#     text, router_config_limit_list = call_llm({"messages": [{"role": "user", "content": f"{query}"}]})
#     logger.info(f"LLM reply: {text}")
#     result_dict = {}
#     for content in router_config_limit_list:
#         try:
#             parsed_content = json.loads(content)
#             result_dict.update(parsed_content)
#         except json.JSONDecodeError:
#             logger.warning(f"Unresolvable content: {content}")

#     if result_dict:
#         return make_response(jsonify({"config": result_dict}), 200)
#     else:
#         return make_response(jsonify(message="Error calling LLM"), 400)


