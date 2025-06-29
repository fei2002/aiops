from flask import Flask
from flask_cors import CORS

import routes

app = Flask(__name__)
# 此处需要注意，跨域必须在路由注册之前
# 因为有了网关，网关出统一做跨域处理，所以这里不需要再做跨域处理
# CORS(app)
routes.init_app(app)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
