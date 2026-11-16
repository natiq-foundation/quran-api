# middleware.py
import time
import logging
import json
import uuid

request_logger = logging.getLogger("django.request_log")
response_logger = logging.getLogger("django.response_log")


class RequestLoggingMiddleware:
    """Logs all requests and responses to Django's logging system"""

    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request):
        request_id = uuid.uuid7()
        start_time = time.time()

        log_data = {
            "id": request_id,
            "method": request.method,
            "path": request.path,
            "query_params": dict(request.GET),
            "user": {
                "id": (
                    str(request.user.id)
                    if request.user.is_authenticated
                    else "anonymous"
                ),
                "name": (
                    str(request.user) if request.user.is_authenticated else "anonymous"
                ),
            },
            "remote_addr": self.get_client_ip(request),
            "user_agent": request.META.get("HTTP_USER_AGENT", "")[:200],  # Truncate
        }
        # Log as formatted string
        log_message = f"{log_data['method']} {log_data['path']} "
        request_logger.info(log_message, extra=log_data)

        response = self.get_response(request)
        duration = time.time() - start_time

        response_logger.info(
            f"REQUEST END: {request.method} {request.path}",
            extra={
                "request_id": request_id,
                "status_code": response.status_code,
                "content": self.get_response_body(response),
                "duration": round(duration, 4),
                "content_length": (
                    len(response.content) if hasattr(response, "content") else 0
                ),
            },
        )

        return response

    def get_client_ip(self, request):
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    def get_response_body(self, response):
        """
        Handle various response types safely
        """
        if hasattr(response, "streaming_content"):
            return "[Streaming response - body not captured]"

        if hasattr(response, "file_to_stream"):
            return f'[File response: {response.filename if hasattr(response, "filename") else "unknown"}]'

        if hasattr(response, "content"):
            content_type = response.get("Content-Type", "")
            body = response.content

            if "json" in content_type:
                try:
                    return json.loads(body.decode("utf-8"))
                except:
                    return body.decode("utf-8", errors="ignore")[:5000]

            elif "text" in content_type or "html" in content_type:
                return body.decode("utf-8", errors="ignore")[:5000]

            elif "image" in content_type or "video" in content_type:
                return f"[Binary content: {content_type}, Size: {len(body)} bytes]"

            else:
                return f"[Content: {content_type}, Size: {len(body)} bytes]"

        return "[Empty response]"
