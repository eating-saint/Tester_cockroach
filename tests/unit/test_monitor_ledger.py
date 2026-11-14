from hands.monitor import LONG_POLL_S, Monitor


def _age(m: Monitor, key, s: float):
    m._late[key] -= s


def test_ignored_types_and_memory_urls():
    m = Monitor()
    m.req_started("ws", "wss://x/sock", "WebSocket")
    m.req_started("es", "https://x/es", "EventSource")
    m.req_started("pg", "https://x/beacon", "Ping")
    m.req_started("d", "data:text/plain,1", "Image")
    m.req_started("b", "blob:https://x/1", "Fetch")
    assert m.pending_count() == 0


def test_late_request_released_after_long_poll():
    m = Monitor()
    m.set_lifecycle("load")
    m.req_started("s", "https://x/poll", "XHR")
    assert m.blocking_count() == 1
    _age(m, "s", LONG_POLL_S + 0.1)
    assert m.blocking_count() == 0
    assert m.pending_urls() == ["https://x/poll"]


def test_pre_load_script_released_after_long_poll():
    """结束事件收不到的脚本（Google reCAPTCHA webworker.js）：不论生命周期是否到 load，挂满 LONG_POLL_S 就不再阻塞。"""
    m = Monitor()
    m.req_started("doc", "https://x/", "Document")
    m.req_started("w", "https://x/webworker.js", "Script")
    m.req_finished("doc")
    assert m.blocking_count() == 1
    _age(m, "w", LONG_POLL_S + 0.1)
    assert m.blocking_count() == 0      # 生命周期仍是 init（同文档改网址后不再到 load）


def test_document_pending_at_load_not_released():
    m = Monitor()
    m.req_started("doc", "https://x/", "Document")
    m.set_lifecycle("load")
    assert "doc" not in m._late


def test_pre_load_fetch_keeps_blocking():
    """页面自己在 load 前发出的数据请求（F4 慢 fetch）：load 后仍阻塞，等待应超时。"""
    m = Monitor()
    m.req_started("f", "https://x/api/delay?ms=8000", "Fetch")
    m.set_lifecycle("load")
    assert "f" not in m._late
    assert m.blocking_count() == 1


def test_document_never_released():
    m = Monitor()
    m.set_lifecycle("load")
    m.req_started("doc", "https://x/", "Document")
    m.req_response("doc", 200, "https://x/")
    assert "doc" not in m._late
    assert m.blocking_count() == 1
    m.req_finished("doc")
    assert m.blocking_count() == 0


def test_response_starts_timer_for_non_document():
    m = Monitor()
    m.req_started("css", "https://x/a.css", "Stylesheet")
    assert "css" not in m._late
    m.req_response("css", 200, "https://x/a.css")
    assert "css" in m._late


def test_4xx_recorded_once():
    m = Monitor()
    m.req_started("r", "https://x/missing", "Fetch")
    m.req_response("r", 404, "https://x/missing")
    m.req_failed("r", "net::ERR_ABORTED")
    s = m.snapshot()
    assert len(s.failed) == 1 and s.failed[0].status == 404
    assert m.pending_count() == 0


def test_failed_records_error_text():
    m = Monitor()
    m.req_started("r", "https://x/down", "Fetch")
    m.req_failed("r", "net::ERR_CONNECTION_REFUSED")
    assert m.snapshot().failed[0].error_text == "net::ERR_CONNECTION_REFUSED"


def test_redirect_keeps_single_entry():
    m = Monitor()
    m.req_started("r", "https://x/a", "Document")
    m.req_started("r", "https://x/b", "Document")
    assert m.pending_count() == 1 and m.pending_urls() == ["https://x/b"]


def test_lifecycle_only_moves_forward():
    m = Monitor()
    m.set_lifecycle("load")
    m.set_lifecycle("DOMContentLoaded")
    assert m.lifecycle == "load"
    m.set_lifecycle("init")
    assert m.lifecycle == "init"
