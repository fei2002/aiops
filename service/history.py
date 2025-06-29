from datetime import datetime

from config.config import mongo_client_chaos
from .k8s import delete_chaos


def load_all_chaos(email: str):
    """
    get all chaos experiments created by specific user.
    :param email: user email
    :return: a list of chaos experiments
    """
    cursor = mongo_client_chaos.get_all(collection="chaos", query={"email": email, "archived": False})
    raw = list(cursor)
    for item in raw:
        del item["_id"]
    return raw


def load_all_vn_chaos(email: str):
    cursor = mongo_client_chaos.get_all(collection="vn_chaos", query={"email": email})
    raw = list(cursor)
    for item in raw:
        del item["_id"]
    return raw


def load_all_schedules(email: str):
    """
    get all chaos experiments created by specific user.
    :param email: user email
    :return: a pred of chaos
    """
    cursor = mongo_client_chaos.get_all(collection="schedule", query={"email": email, "archived": False})
    raw = list(cursor)
    for item in raw:
        del item["_id"]
    return raw


def archive_experiment_by_name(name: str):
    """
    Instead of deleting the chaos, we just mark it as "archived"
    :param name: the name of the chaos
    :return: the number of documents modified
    """
    # delete experiment
    result = mongo_client_chaos.get_one(collection="chaos", query={"name": name})
    delete_chaos(result['kind'].lower(), name)
    # set "archived" field
    result = mongo_client_chaos.update(collection="chaos", query={"name": name},
                                       update={"$set": {"archived": True, "archived_at": datetime.now()}})
    return result.modified_count


def archive_schedule_by_name(name):
    """
    Instead of deleting the chaos, we just mark it as "archived"
    :param name: the name of the chaos
    :return: the number of documents modified
    """
    # delete schedule
    delete_chaos("schedules", name)
    # add an "archive" field to the document
    result = mongo_client_chaos.update(collection="schedule", query={"name": name},
                                       update={"$set": {"archived": True, "archived_at": datetime.now()}})
    return result.modified_count

