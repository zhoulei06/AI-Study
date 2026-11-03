from ag_ui.core import RunErrorEvent


def error_event(error):
    return RunErrorEvent(
        code=error.code.value, message=error.message, metadata={"error": error.public()}
    )
