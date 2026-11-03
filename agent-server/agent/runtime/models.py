"""Configure the SDK clients themselves, not only copied Pydantic fields."""


def configure_model(model, *, timeout, **settings):
    if not hasattr(model, "model_copy"):
        return model
    update = {"request_timeout": timeout, "max_retries": 0, **settings}
    sync = getattr(model, "root_client", None)
    if sync is not None:
        sync = sync.with_options(timeout=timeout, max_retries=0)
        update.update(root_client=sync, client=sync.chat.completions)
    asynchronous = getattr(model, "root_async_client", None)
    if asynchronous is not None:
        asynchronous = asynchronous.with_options(timeout=timeout, max_retries=0)
        update.update(
            root_async_client=asynchronous, async_client=asynchronous.chat.completions
        )
    return model.model_copy(update=update)
