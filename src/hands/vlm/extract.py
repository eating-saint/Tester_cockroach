"""内容抽取的依据回查（spec ver1 F11）：值必须能在指定元素（或页面正文）里找到，否则标"无依据"。"""
from __future__ import annotations

import re
import unicodedata


def normalize(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "")        # 全角转半角
    return re.sub(r"\s+", "", s).lower()


def verify_extract(items: list[dict], state) -> list[dict]:
    rows = []
    body = normalize(getattr(state, "body_text", ""))
    for it in items:
        field, value, eid = it.get("field"), str(it.get("value", "")), it.get("element")
        v = normalize(value)
        row = {"field": field, "value": value, "element": eid, "grounded": False, "where": None}
        if not v:
            row["note"] = "值为空"
        elif eid:
            e = state.element(eid)
            if e is None:
                row["note"] = "元素不存在"
            else:
                for where in ("name", "text", "value", "context"):
                    if v in normalize(getattr(e, where, None)):
                        row.update(grounded=True, where=where)
                        break
                else:
                    row["note"] = "该元素里找不到这个值"
        else:
            if v in body:
                row.update(grounded=True, where="body")
            else:
                row["note"] = "正文里找不到这个值"
        rows.append(row)
    return rows
