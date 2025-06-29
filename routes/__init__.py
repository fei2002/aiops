from .chaos import chaos_bp
from .chart import chart_bp
from .evaluation import evaluation_bp
from .testbed import testbed_bp
from .benchmark import benchmark_bp
from .virtualnetwork import virtualnetwork_bp
from service.chaos import clear_stale_archives
import time
import atexit

from apscheduler.schedulers.background import BackgroundScheduler


def init_app(app):
    app.register_blueprint(evaluation_bp)
    app.register_blueprint(chaos_bp)
    app.register_blueprint(chart_bp)
    app.register_blueprint(testbed_bp)
    app.register_blueprint(benchmark_bp)
    app.register_blueprint(virtualnetwork_bp)

    scheduler = BackgroundScheduler()
    scheduler.add_job(func=clear_stale_archives, trigger="interval", hours=1)
    scheduler.start()

    # Shut down the scheduler when exiting the app
    atexit.register(lambda: scheduler.shutdown())
