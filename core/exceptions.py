from rest_framework.views import exception_handler as drf_exception_handler


def exception_handler(exc, context):
    """Wrap DRF's default error response in a consistent envelope.

    Response shape: {"detail": <message>, "errors": <original data>, "code": <exc class name>}
    """
    response = drf_exception_handler(exc, context)
    if response is None:
        return response

    data = response.data
    if isinstance(data, dict) and "detail" in data and len(data) == 1:
        detail = data["detail"]
    else:
        detail = "Validation error"

    response.data = {
        "detail": detail,
        "errors": data,
        "code": exc.__class__.__name__,
    }
    return response
