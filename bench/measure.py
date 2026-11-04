"""Writes bench/results.md from offline measurements. Calls no model."""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

import tiktoken

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.gateway import instructions_for  # noqa: E402
from agent.loop import Tool  # noqa: E402
from agent.memory import Memory  # noqa: E402
from bench.offline import AnswerCache, route, schema_tokens, select_tools  # noqa: E402
from bench.stubs import production_tools  # noqa: E402

ROOT = Path(__file__).resolve().parent


def first_sentence(tool: Tool) -> Tool:
    short = re.split(r"(?<=[.다])\s", tool.description, maxsplit=1)[0]
    return Tool(tool.name, short, tool.parameters, tool.run, tool.needs_approval)


def main() -> None:
    encode = tiktoken.get_encoding("o200k_base").encode
    requests = [json.loads(line) for line in (ROOT / "requests.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    tools = production_tools()
    short_tools = [first_sentence(tool) for tool in tools]
    instructions = instructions_for(Memory(Path(tempfile.mkdtemp()) / "m.db"))
    base_fixed = len(encode(instructions))
    all_schema = schema_tokens(tools, None, encode)

    rows, totals = [], {"A": 0, "B": 0, "C": 0, "D": 0}
    cache = AnswerCache()
    routed_count = cached_count = 0
    for request in requests:
        text_tokens = len(encode(request["text"]))
        names = select_tools(request["text"])
        a = base_fixed + all_schema + text_tokens
        b = base_fixed + schema_tokens(tools, names, encode) + text_tokens
        c = base_fixed + schema_tokens(short_tools, names, encode) + text_tokens
        routed = route(request["text"])
        hit = request["type"] == "simple" and cache.get(request["text"]) is not None
        if request["type"] == "simple" and not hit:
            cache.put(request["text"], "(answer)")
        d = 0 if routed or hit else c
        routed_count += bool(routed)
        cached_count += hit
        how = "라우터" if routed else "캐시" if hit else "모델"
        rows.append(f"| {request['id']} | {request['type']} | {a:,} | {b:,} | {c:,} | {d:,} | {how} |")
        for key, value in zip("ABCD", (a, b, c, d)):
            totals[key] += value

    def pct(value):
        return f"{(1 - value / totals['A']) * 100:.1f}%"

    report = [
        "# 오프라인 측정 결과",
        "",
        "모델을 부르지 않고 계산했다. 토큰은 `o200k_base` 토크나이저 추정치이고, **모델 호출 1회당 입력**이다. 실제 요청은 여러 번 호출하므로 실제 절감량은 호출 수만큼 커진다. 운영 지시문에는 저장된 기억이 더 붙지만 여기서는 비어 있는 상태로 계산했다.",
        "",
        f"- 지시문: {base_fixed:,} 토큰",
        f"- 도구 설명 24개 전체: {all_schema:,} 토큰",
        f"- 요청 문장: 평균 {sum(len(encode(r['text'])) for r in requests) / len(requests):.0f} 토큰",
        "",
        "| 방식 | 내용 |",
        "| --- | --- |",
        "| A | 기준. 도구 24개 설명을 매번 전부 보낸다 |",
        "| B | 요청 단어로 필요한 도구 묶음만 코드가 골라 보낸다 |",
        "| C | B + 도구 설명을 첫 문장만 남긴다 |",
        "| D | C + 고정 문형은 코드 라우터, 같은 질문은 답변 캐시로 처리해 모델을 부르지 않는다 |",
        "",
        "| 요청 | 유형 | A | B | C | D | 처리 |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        *rows,
        f"| **합계** | | **{totals['A']:,}** | **{totals['B']:,}** | **{totals['C']:,}** | **{totals['D']:,}** | |",
        f"| **A 대비 절감** | | | **{pct(totals['B'])}** | **{pct(totals['C'])}** | **{pct(totals['D'])}** | |",
        "",
        f"- 코드 라우터가 모델 없이 처리한 요청: {routed_count}/{len(requests)}",
        f"- 답변 캐시가 처리한 요청: {cached_count}/{len(requests)}",
    ]
    (ROOT / "results.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report[3:7] + report[-6:]))


if __name__ == "__main__":
    main()
