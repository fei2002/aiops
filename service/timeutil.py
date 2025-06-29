from datetime import datetime

from dateutil import tz


def get_timestamp(time: str, datetime_format: str):
    t = datetime.strptime(time, datetime_format).replace(tzinfo=tz.tzutc())
    return int(t.timestamp())


def timestamp2str(timestamp: int, datetime_format: str):
    return datetime.utcfromtimestamp(float(timestamp)).strftime(datetime_format)


def cal_end_timestamp(start_time: str, duration: str):
    # 2023-03-25T12:45:06Z
    start_time = get_timestamp(start_time, "%Y-%m-%dT%H:%M:%SZ")

    if duration.endswith("s"):
        return start_time + int(duration[:-1])
    elif duration.endswith("m"):
        return start_time + int(duration[:-1]) * 60
    elif duration.endswith("h"):
        return start_time + int(duration[:-1]) * 60 * 60
    elif duration.endswith("d"):
        return start_time + int(duration[:-1]) * 60 * 60 * 24

    return start_time
