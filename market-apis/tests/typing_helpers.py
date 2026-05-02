from pydantic import HttpUrl, TypeAdapter


_http_url_adapter = TypeAdapter(HttpUrl)


def http_url(value: str) -> HttpUrl:
    return _http_url_adapter.validate_python(value)
