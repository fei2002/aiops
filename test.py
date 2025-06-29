from datetime import datetime
from functools import reduce
import os
from dateutil import tz

# time = "2023-2-4T14:40"
# local_time = datetime.strptime(time, "%Y-%m-%dT%H:%M").replace(tzinfo=tz.tzlocal())
# utc_time = datetime.strptime(time, "%Y-%m-%dT%H:%M").replace(tzinfo=tz.tzutc())
#
# print("local time:{} int_time:{}".format(local_time, int(local_time.timestamp())))
# print("utc time:{} int_time:{}".format(utc_time, int(utc_time.timestamp())))
#
# int_time = 1676225291
# print(datetime.utcfromtimestamp(float(int_time)).strftime("%Y-%m-%dT%H:%M"))
from service.file import read_yaml

if __name__ == '__main__':
    CHAOS_TEMPLATE_DIR = os.path.join(os.path.curdir, "../test.yaml")
    print(CHAOS_TEMPLATE_DIR)