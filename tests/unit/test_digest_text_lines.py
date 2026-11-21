from hands.vlm.digest import text_lines


def test_dedupe_strip_and_blank():
    text = "  店名：小馆 \n\n店名：小馆\n　　\n价位：$$\n ＡＢＣ \nabc"
    assert text_lines(text) == ["店名：小馆", "价位：$$", "ＡＢＣ"]


def test_width_and_limit():
    long = "字" * 200
    assert text_lines(long) == ["字" * 80]
    many = "\n".join(f"第{i}行" for i in range(300))
    out = text_lines(many)
    assert len(out) == 120 and out[0] == "第0行" and out[-1] == "第119行"
    assert text_lines(many, limit=5, width=2) == ["第0", "第1", "第2", "第3", "第4"]


def test_empty():
    assert text_lines(None) == [] and text_lines("") == []
