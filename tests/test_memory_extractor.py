import json

import pytest

from engine.llm.client import JSONStrictError
from engine.memory.extractor import extract_candidates, should_extract


class FakeClient:
    def __init__(self, result=None, error=None):
        self.result = result if result is not None else {"candidates": [{"summary": "上周去了长城"}]}
        self.error = error
        self.calls = []

    def chat_json_strict(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


@pytest.mark.parametrize("text, expected", [
    ("我上周去了长城", True), ("这个怎么说？", False),
    ("我把这本书不能看完", False), ("我来自法国", True),
    ("我打算周末去北京", True), ("我住在上海", True), ("他昨天去了长城", False),
])
def test_should_extract(text, expected):
    assert should_extract([text]) is expected


def test_input_window_and_prompt():
    client = FakeClient()
    texts = ["已过期"] + [f"我昨天去了地点{i}" + "字" * 300 for i in range(20)]
    assert extract_candidates(texts, client=client) == [{"summary": "上周去了长城"}]
    request = client.calls[0]
    sent = json.loads(request["user"])["user_texts"]
    assert sent == [t[:200] for t in texts[-20:]]
    assert "已过期" not in request["user"]
    for requirement in ('{"candidates":', "40", "不推断", "健康", "宗教", "政治", "性取向",
                        "民族", "财务", "精确住址", "电话", "证件号", "第三方全名"):
        assert requirement in request["system"]


@pytest.mark.parametrize("result", [{}, {"candidates": {}}, {"candidates": "bad"}, "非法JSON", None])
def test_invalid_shape(result):
    client = FakeClient()
    client.result = result
    assert extract_candidates(["我上周去了长城"], client=client) == []


@pytest.mark.parametrize("error", [JSONStrictError("bad json"), RuntimeError("offline")])
def test_errors_return_empty(error):
    assert extract_candidates(["我去了北京"], client=FakeClient(error=error)) == []


def test_filter_and_three_limit():
    result = {"candidates": ["bad", {"summary": " "}, {"summary": 4},
                             {"summary": "越界", "source": "secret"},
                             {"summary": "字" * 80, "place": "北京", "tags": ["旅行"]},
                             {"summary": "第二"}, {"summary": "第三"}, {"summary": "第四"}]}
    out = extract_candidates(["我去了北京"], client=FakeClient(result))
    assert out == [{"summary": "字" * 40, "place": "北京", "tags": ["旅行"]},
                   {"summary": "第二"}, {"summary": "第三"}]


def test_fake_list_compatibility():
    assert extract_candidates([], client=FakeClient([{"summary": "旅行"}])) == [{"summary": "旅行"}]
