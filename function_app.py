import logging

import azure.functions as func

from velib_datalake.cli import build_client, build_store, configure_logging
from velib_datalake.collector import collect_once
from velib_datalake.config import Settings

configure_logging()
app = func.FunctionApp()


@app.schedule(
    schedule="%TIMER_SCHEDULE%",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def collect_velib_availability(timer: func.TimerRequest) -> None:
    if timer.past_due:
        logging.warning("timer_invocation_is_past_due")
    settings = Settings.from_env()
    metadata = collect_once(settings, build_store(settings), build_client(settings))
    logging.info("timer_collection_complete collection_id=%s", metadata.collection_id)

