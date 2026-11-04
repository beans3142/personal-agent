"""Read public Naver transit results, with three useful alternatives and source images."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
CARDS = ".list_pubtrans_directions_summary > li"


def format_routes(data: dict) -> str:
    parts = []
    for number, route in enumerate(data.get('routes', [])[:3], 1):
        summary, detail = route.get('summary', ''), route.get('detail', '')
        def found(pattern, text):
            match = re.search(pattern, text)
            return re.sub(r'\s+', ' ', match[0]).strip() if match else ''
        facts = [found(r'\d+시간(?:\s*\d+분)?|\d+분', summary),
                 found(r'(?:(?:오전|오후)\s*)?\d+:\d+\s*도착', summary),
                 found(r'환승\s*\d+회', detail),
                 found(r'도보\s*\d+분', detail),
                 found(r'\d[\d,]*원', summary)]
        boarding = [found(r'\d+:\d+\s*출발', detail)]
        boarding += re.findall(r'빠른(?:환승|하차):?\s*\d+-\d+(?:,\s*\d+-\d+)*', detail)[:2]
        label = '·'.join(route.get('reasons', [])) or '대안'
        parts.append(f"{number}. **{label}**\n" + ' · '.join(x for x in facts if x)
                     + ('\n' + ' · '.join(x for x in boarding if x) if any(boarding) else ''))
    return '\n\n'.join(parts) + '\n예정 시간표 기준 · 네이버 지도' if parts else '경로 조회를 완료하지 못했습니다.'


def candidate_key(text: str) -> str:
    return re.sub(r"최적|최소\s*시간|최소\s*환승|최소\s*도보|[\s·]", "", text)


def choose_candidates(fast: list[str], transfers: str, walking: str) -> list[dict]:
    selected: dict[str, dict] = {}
    for reason, text in [("빠른 도착", fast[0]), ("최소 환승", transfers), ("최소 도보", walking)]:
        key = candidate_key(text)
        selected.setdefault(key, {"summary": text, "reasons": []})["reasons"].append(reason)
    for text in fast:
        if len(selected) >= 3:
            break
        selected.setdefault(candidate_key(text), {"summary": text, "reasons": ["다른 대안"]})
    return list(selected.values())


def compact_detail(text: str) -> str:
    lines, skip = [], False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"\d+개 역 이동", line):
            skip = True
        elif line in ("시간표", "실시간"):
            skip = False
        if not skip and line not in ("파노라마 보기", "상세정보", "정보수정 제안", "새로고침"):
            lines.append(line)
    return "\n".join(lines)[:1600]


def upcoming_timetable(rows: list[list[str]], depart: datetime) -> list[dict]:
    if not rows:
        return []
    result = [{"direction": re.sub(r"\s+", " ", header).strip(), "departures": []} for header in rows[0]]
    threshold = depart.hour * 60 + depart.minute
    candidates = [[] for _ in result]
    for row in rows[1:]:
        for index, cell in enumerate(row[:len(result)]):
            found = re.search(r"(\d{2}):(\d{2})", cell)
            if not found:
                continue
            hour, minute = map(int, found.groups())
            clock = hour * 60 + minute
            if hour < 4 and depart.hour >= 4:
                clock += 24 * 60
            if clock >= threshold:
                candidates[index].append((clock, re.sub(r"\s+", " ", cell).strip()))
    for index, values in enumerate(candidates):
        result[index]["departures"] = [text for _, text in sorted(values)[:3]]
    return result


def _place(page, field: str, query: str) -> str:
    page.get_by_role("combobox", name=field, exact=True).fill(query)
    options = page.locator('[id^="instant-search-item-poi-"]')
    options.first.wait_for(state="visible", timeout=15000)
    texts = options.all_inner_texts()
    # A station query must not silently select a KTX terminal or an exit.
    station = [i for i, text in enumerate(texts) if "지하철,전철" in text and query in text]
    matches = station if query.endswith("역") and station else [i for i, text in enumerate(texts) if query in text]
    if len(matches) > 1 and matches == station:
        # Transfer stations have one suggestion per line. Only collapse those
        # whose station name and full street address agree exactly.
        identities = []
        for i in matches:
            lines = [line.strip() for line in texts[i].splitlines() if line.strip()]
            identities.append((next((line.split()[0] for line in lines if line.startswith(query + ' ')), ''), lines[-1]))
        if identities and len(set(identities)) == 1 and identities[0][0] == query:
            matches = matches[:1]
    if len(matches) != 1:
        raise RuntimeError(f"장소를 확정할 수 없습니다: {query}. 후보: " + " / ".join(texts[:5]))
    selected = matches[0]
    options.nth(selected).click()
    return page.get_by_role("combobox", name=field, exact=True).input_value()


def _departure(page, depart: datetime) -> None:
    now = datetime.now(KST)
    if abs((now - depart).total_seconds()) < 120:
        return  # The default is a fresh 'depart now' query.
    if (depart.year, depart.month) != (now.year, now.month):
        raise RuntimeError("현재 월 이외의 출발일 지정은 아직 지원하지 않습니다")
    page.locator("button.btn_time_option").click()
    for index, label in enumerate((str(depart.day), f"{depart.hour:02d}", f"{depart.minute:02d}")):
        page.locator("button.timeset_btn_data").nth(index).click()
        page.locator("button:not(.is_dimmed)").filter(has_text=re.compile(f"^{label}$")).click()
    page.locator("button.btn_time_option").click()
    page.wait_for_load_state("networkidle", timeout=20000)


def _sort(page, label: str) -> list[str]:
    page.locator("button.btn_order_option").click()
    page.get_by_text(label, exact=True).click()
    page.locator("button.btn_order_option").filter(has_text=label).wait_for(state="visible")
    page.wait_for_load_state("networkidle", timeout=20000)
    page.locator(CARDS).first.wait_for(state="visible", timeout=20000)
    return page.locator(CARDS).all_inner_texts()


def search(origin: str, destination: str, depart_at: str | None, outbox: Path) -> str:
    from .browser_worker import shared_browser

    depart = datetime.fromisoformat(depart_at) if depart_at else datetime.now(KST)
    depart = depart.astimezone(KST) if depart.tzinfo else depart.replace(tzinfo=KST)
    return shared_browser().run(lambda browser: _search_in(browser, origin, destination, depart, outbox))


def _search_in(browser, origin: str, destination: str, depart: datetime, outbox: Path) -> str:
    from .browser_worker import job_cancelled

    def check_active():
        if job_cancelled():
            raise TimeoutError("browser request expired")

    check_active()
    # The browser is shared and kept running; only this request's page is closed.
    if True:
        generated = []
        page = browser.new_page(locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 1600, "height": 1100})
        try:
            page.goto("https://map.naver.com/p/directions/-/-/-/transit", wait_until="domcontentloaded", timeout=45000)
            resolved = [_place(page, "출발지 입력", origin), _place(page, "도착지 입력", destination)]
            page.get_by_role("button", name="길찾기", exact=True).click()
            page.locator(CARDS).first.wait_for(state="visible", timeout=40000)
            _departure(page, depart)
            selected_time = page.locator("button.btn_time_option").inner_text().replace("\n", " ")
            fast = _sort(page, "빠른 도착순")
            transfers = _sort(page, "최소 환승순")[0]
            walking = _sort(page, "최소 도보순")[0]
            selected = choose_candidates(fast, transfers, walking)
            current = _sort(page, "빠른 도착순")
            outbox.mkdir(parents=True, exist_ok=True)
            request_id = uuid.uuid4().hex[:10]
            for number, route in enumerate(selected, 1):
                key = candidate_key(route["summary"])
                index = next(i for i, text in enumerate(current) if candidate_key(text) == key)
                page.locator(CARDS).nth(index).get_by_role("button", name="상세보기", exact=True).click()
                panel = page.locator("#sub_panel")
                panel.wait_for(state="visible")
                route["detail"] = compact_detail(panel.inner_text())
                route["url"] = page.url
                # First route's timetable is source-labelled and explicitly scheduled, not real-time.
                if number == 1 and panel.get_by_role("button", name="시간표", exact=True).count():
                    try:
                        panel.get_by_role("button", name="시간표", exact=True).first.click()
                        frame = page.frame_locator("#subwayScheduleIframe")
                        frame.get_by_role("table").wait_for(timeout=10000)
                        rows = frame.get_by_role("table").get_by_role("row").evaluate_all("rows => rows.map(r => Array.from(r.querySelectorAll('th,td')).map(c => c.innerText))")
                        route["scheduled_timetable"] = upcoming_timetable(rows, depart)
                        direction = re.search(r"([가-힣0-9]+)역\s*방면", route["detail"])
                        if direction:
                            route["scheduled_timetable"] = [entry for entry in route["scheduled_timetable"] if direction[1] + " 방면" in entry["direction"]]
                        route["timetable_source"] = page.locator("#subwayScheduleIframe").get_attribute("src")
                    except Exception:
                        route["timetable_status"] = "시간표 추가 조회 실패; 경로 상세의 예정 승차시각만 확인됨"
                page.go_back(wait_until="domcontentloaded")
                page.locator(CARDS).first.wait_for(state="visible")
                current = page.locator(CARDS).all_inner_texts()
                if number == 1:
                    # Capture the map after the detail panel is removed, rather than mid-slide.
                    page.locator("#sub_panel").wait_for(state="hidden")
                    check_active()
                    route["image"] = f"route-{request_id}-1.png"
                    image_path = outbox / route["image"]
                    generated.append(image_path)
                    page.screenshot(path=str(image_path), animations="disabled")
                    check_active()
            check_active()
            return json.dumps({"source": "네이버 지도", "queried_at": datetime.now(KST).isoformat(timespec="minutes"),
                               "departure_setting": selected_time, "places": resolved, "routes": selected,
                               "note": "실제 조회한 예정 경로. 도착 예정은 보장되지 않음. 시간표는 예정 운행이며 실시간 도착정보와 구분. 빠른 승하차는 detail에 제공된 항목만 사용."}, ensure_ascii=False)
        except Exception:
            for path in generated:
                path.unlink(missing_ok=True)
            raise
        finally:
            page.close()
