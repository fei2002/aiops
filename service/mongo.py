"""
    MongoDB 连接的客户端
"""

from pymongo import MongoClient
from pymongo.errors import OperationFailure
from loguru import logger


class MongoConnectClient:
    def __init__(self, host="localhost", db="sock-shop", port=27017, username="root", password="root"):
        """
        :param host: str mongodb地址
        :param db: str 数据库
        :param port: int 端口，默认为27017
        :param username: str 用户名
        :param password: str 密码
        """
        host = host
        db = db
        self.port = port
        self.client = MongoClient(host=host, port=port, username=username, password=password)
        self.db = self.client[db]

    def insert_one(self, collection, dic):
        """
        :param collection: str 数据库中的集合
        :param dic: dict 要插入的字典
        :return: 返回包含一个ObjectId类型的对象
        """
        collection = self.db[collection]
        rep = collection.insert_one(dic)

        return rep

    def insert_many(self, collection, lists):
        """
        :param lists: 要插入的列表，列表中的元素为字典
        :param collection: str 数据库中的集合
        :return: 返回包含多个ObjectId类型的列表对象
        """
        collection = self.db[collection]
        rep = collection.insert_many(lists)
        return rep

    def get_counts(self, collection):
        """
        获取表里的数据总数
        :param collection: str 表名称
        """
        return self.db[collection].count()

    def get_one(self, collection, query=None):
        """
        随机获取一条数据
        :param query: 查询语句
        :param collection: collection
        """
        return self.db[collection].find_one(query)

    def get_all(self, collection, query=None):
        """
        获取某个 collection 的所有数据
        :param query: 查询语句
        :param collection: collection名称
        :return: 所有的Object
        """
        if query is None:
            query = {}
        return self.db[collection].find(query)

    def get_last(self, collection, query=None):
        """
        获取插入最晚的一条数据
        :param query: 查询语句
        :param collection: collection的名称
        :return: 插入最晚的一条数据
        """
        resp = self.db[collection].find(query).sort('_id', -1).limit(1)
        for item in resp:
            return item

    def update_one(self, collection, query, newvalues):
        """
        更新query匹配到第一条记录为newvalues
        """
        self.db[collection].update_one(query, newvalues)

    def update_all(self, collection, query, newvalues):
        """
        更新query匹配到的所有记录为newvalues
        """
        self.db[collection].update_many(query, newvalues)

    def get_collections(self):
        """
        获取所有的collection的名称
        :return: 所有的collection的名称
        """
        return self.db.list_collection_names()

    def update(self, collection, query, update):
        return self.db[collection].update_one(query, update)

    def delete_one(self, collection, query):
        return self.db[collection].delete_one(query)

    def delete_all(self, collection, query):
        return self.db[collection].delete_many(query)
    
    def delete_collection(self, collection):
        return self.db[collection].drop()

    def perform_transaction(self, user_func, *args):
        try:
            # 开启会话并在事务中执行操作
            with self.client.start_session() as session:
                with session.start_transaction():
                    # 调用用户定义的函数，在事务中执行自定义操作
                    logger.info("args: {}".format(args))
                    user_func(*args)

                # 提交事务
                session.commit_transaction()
                logger.info("事务已提交")
        except OperationFailure as e:
            logger.info("事务执行失败:", str(e))
            session.abort_transaction()
            logger.info("事务已回滚")


if __name__ == '__main__':
    mongo = MongoConnectClient(host="k8s.personai.cn", db="chaos", port=30332)
    res = mongo.update(collection="chaos",
                       query={"name": "node-mem-stress-90b43ad5-30d0-4660-956c-b6e15ee00bc8"},
                       update={"$set": {"archive": True}}
                       )
    print(res)
