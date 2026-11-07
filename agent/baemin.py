"""Collect Baemin stores and menus in code, so the model only summarises.

Opening ten stores one tap at a time costs the model ~60 calls. This tool
does the taps itself (zero model calls) and returns compact facts: list
data, menus with composition and every price option, the store's review
event notice, and hall / shared-kitchen signals from the store info page.
Read-only: it never adds to the cart or orders.
"""
import json
import re
import time

from .android_control import parse_nodes
from .loop import Tool

PACKAGE = "com.sampleapp"
RATING = re.compile(r"^[0-5]\.\d$")
REVIEWS = re.compile(r"^\(([\d,]+)\)$")
# A price label must contain a non-digit (e.g. "16p"), so "17,000원" is never split.
PRICE = re.compile(r"^,?(?:([^,]*[^\d,][^,]*),)?(\d{1,3}(?:,\d{3})+|\d+)원$")
TAGS = re.compile(r"^(인기 \d+위|사장님 추천|대표|신메뉴|추천|품절|리뷰 [\d,]+|BEST|NEW)$")
HALL = re.compile(r"좌석|좌식|테이블|단체석|홀|매장|주차|룸")
SHARED = re.compile(r"공유\s*주방|키친|kitchen|딜리버리|배달\s*전문", re.I)
INFO_LABELS = ("상호명", "주소", "운영시간", "휴무일", "편의시설")


def _value(node):
    return node.get("text") or node.get("description") or ""


def _center(node):
    x1, y1, x2, y2 = node["bounds"]
    return (x1 + x2) // 2, (y1 + y2) // 2


def parse_results(nodes):
    """Store cards on the search result list, in screen order."""
    values = [(v, n) for n in nodes if (v := _value(n))]
    starts = [k for k in range(2, len(values) - 1)
              if RATING.match(values[k][0]) and REVIEWS.match(values[k + 1][0])]
    stores = []
    for index, k in enumerate(starts):
        name_value, name_node = values[k - 2]
        end = starts[index + 1] - 2 if index + 1 < len(starts) else len(values)
        store = {"name": re.sub(r",\s*배달팁.*$", "", name_value).strip(), "summary": values[k - 1][0],
                 "rating": values[k][0], "reviews": REVIEWS.match(values[k + 1][0])[1],
                 "ad": False, "pickup": False, "tap": _center(name_node)}
        seen_eta = False
        for value, _ in values[k + 2:end]:
            if value.startswith("최소주문 "):
                store["min_order"] = value.removeprefix("최소주문 ").strip()
            elif re.match(r"^약 \d+분$", value):
                store["eta"], seen_eta = value, True
            elif seen_eta and "tip" not in store and (m := re.match(r"^(?:배달팁\s*)?([\d,]+원)$", value)):
                store["tip"] = m[1]
            elif re.match(r"^[\d.]+(km|m)$", value):
                store["distance"] = value
            elif value == "픽업가능":
                store["pickup"] = True
            elif value == "광고":
                store["ad"] = True
        stores.append(store)
    return stores


EVENT = re.compile(r"리뷰\s*이벤트|리뷰를\s*남겨|리뷰\s*약속|리뷰\s*작성")


def _review_event(values):
    for value in values:
        if value.startswith("별점") or not EVENT.search(value):
            continue  # Customer reviews quote the event too; only store notices count.
        lines = [line.strip(" *★") for line in value.splitlines() if line.strip()]
        useful = [line for line in lines if re.search(r"리뷰|요청사항|증정|서비스", line)
                  and not re.match(r"^(\d+\.\s*)?리뷰\s*이벤트$", line)]
        return " ".join(useful)[:200] or None
    return None


def parse_store(nodes):
    values = [v for n in nodes if (v := _value(n))]
    menus, chunk = [], []
    for value in values:
        if value.startswith("장바구니 담기"):
            texts = [v for v in chunk if not PRICE.match(v) and not TAGS.match(v) and not v.endswith("요.")]
            prices = [((m[1] or "").strip(), int(m[2].replace(",", ""))) for v in chunk if (m := PRICE.match(v))]
            if texts and prices and texts[0] not in {m["name"] for m in menus}:
                menus.append({"name": texts[0], "items": texts[1] if len(texts) > 1 else "", "prices": prices})
            chunk = []
        elif len(value) <= 20 and (value.endswith("메뉴") or value.endswith("조합")):
            chunk = []  # Section header: combos above it are not menus.
        else:
            chunk.append(value)
    return {"menus": menus, "review_event": _review_event(values)}


def parse_info(nodes):
    values = [v for n in nodes if (v := _value(n))]
    info = {}
    for index, value in enumerate(values[:-1]):
        if value in INFO_LABELS and value not in info and values[index + 1] not in INFO_LABELS:
            info[value] = values[index + 1]
    return info


def classify(info, name):
    facilities, address = info.get("편의시설"), info.get("주소")
    hall = True if facilities and HALL.search(facilities) else None
    text = " ".join(filter(None, [address, info.get("상호명"), name]))
    shared = True if SHARED.search(text) else (False if address else None)
    return {"hall": hall, "shared_kitchen": shared}


class BaeminSearch:
    def __init__(self, device, sleep=time.sleep, clock=time.monotonic, budget=200):
        self.device, self.sleep, self.clock, self.budget = device, sleep, clock, budget

    def _nodes(self):
        return parse_nodes(self.device.dump_hierarchy())

    def _wait(self, predicate, timeout):
        deadline = self.clock() + timeout
        while True:
            nodes = self._nodes()
            if predicate(nodes):
                return nodes
            if self.clock() >= deadline:
                return None
            self.sleep(0.5)

    @staticmethod
    def _has(nodes, test):
        return any(test(_value(n), n) for n in nodes)

    def _open_results(self, query):
        if hasattr(self.device, "stop"):
            self.device.stop(PACKAGE)  # Start from home, not whatever screen was left open.
        self.device.open("baemin")
        nodes = self._wait(lambda ns: self._has(ns, lambda v, n: v.startswith("검색 버튼")), 12)
        if nodes is None:
            raise RuntimeError("Baemin home screen not found")
        self.device.click(*_center(next(n for n in nodes if _value(n).startswith("검색 버튼"))))
        nodes = self._wait(lambda ns: self._has(ns, lambda v, n: n["class"].endswith("EditText")), 6)
        if nodes is None:
            raise RuntimeError("Baemin search field not found")
        self.device.set_text(next(n for n in nodes if n["class"].endswith("EditText")), query)
        self.device.key("enter")
        tab = lambda v, n: re.match(r"^배달 \d", v)
        nodes = self._wait(lambda ns: self._has(ns, tab), 8)
        if nodes is None:
            raise RuntimeError("Baemin delivery results not found")
        self.device.click(*_center(next(n for n in nodes if tab(_value(n), n))))
        if self._wait(lambda ns: bool(parse_results(ns)), 8) is None:
            raise RuntimeError("no Baemin stores for this search")

    def _store_details(self, store):
        self.device.click(*store["tap"])
        self.sleep(1.0)
        nodes = self._wait(lambda ns: self._has(ns, lambda v, n: v == "최소주문"), 8)
        if nodes is None:
            return None
        details = {"info": {}}
        toggle = next((n for n in nodes if n.get("text") == "배달" and n["class"].endswith("TextView") and n["bounds"][0] > 150), None)
        if toggle is not None:
            # "가게정보·원산지" sits above the delivery/pickup toggle and has no accessibility node.
            self.device.click(831, toggle["bounds"][1] - 65)
            info_nodes = self._wait(lambda ns: self._has(ns, lambda v, n: v == "상호명"), 5)
            if info_nodes is not None:
                details["info"] = parse_info(info_nodes)
                self.device.key("back")
            if self._wait(lambda ns: self._has(ns, lambda v, n: v == "최소주문"), 5) is None:
                self.device.key("back")
                return details
        collected = list(nodes)
        for _ in range(2):
            self.device.swipe("up")
            self.sleep(0.6)
            collected += self._nodes()
        details.update(parse_store(collected))
        return details

    def _back_to_results(self):
        self.device.key("back")
        self.sleep(1.0)
        if self._wait(lambda ns: bool(parse_results(ns)), 5) is None:
            self.device.key("back")
            self._wait(lambda ns: bool(parse_results(ns)), 5)

    def search(self, query, max_price=None, count=10):
        started = self.clock()
        self._open_results(query)
        results, visited, idle, partial = [], set(), 0, False
        while len(results) < count:
            nodes = self._nodes()
            fresh = [s for s in parse_results(nodes) if s["name"] not in visited and 190 < s["tap"][1] < 1300]
            if not fresh:
                idle += 1
                if idle > 3:
                    break  # End of the list.
                self.device.swipe("up")
                self.sleep(0.8)
                continue
            idle = 0
            if self.clock() - started > self.budget - 20:
                partial = True
                break
            store = fresh[0]
            visited.add(store["name"])
            details = self._store_details(store) or {}
            self._back_to_results()
            info = details.get("info", {})
            menus = []
            for menu in details.get("menus", []):
                prices = [p for p in menu["prices"] if max_price is None or p[1] <= max_price]
                if prices:
                    menus.append({"name": menu["name"], "items": menu["items"][:80],
                                  "prices": [f"{label} {price:,}원".strip() for label, price in prices]})
            entry = {k: v for k, v in store.items() if k != "tap"}
            entry.update(menus=menus[:8], review_event=details.get("review_event"),
                         facilities=info.get("편의시설"), **classify(info, store["name"]))
            results.append(entry)
        if len(results) < count and self.clock() - started > self.budget - 20:
            partial = True
        return {"query": query, "max_price": max_price, "checked": len(results), "partial": partial, "stores": results}


def build_baemin_tools(device):
    search = BaeminSearch(device)

    def run(args):
        result = search.search(args["query"], args.get("max_price"), args.get("count", 10))
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    schema = {"type": "object", "required": ["query"], "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 30},
        "max_price": {"type": "integer", "minimum": 1000, "maximum": 500000},
        "count": {"type": "integer", "minimum": 1, "maximum": 15}}}
    return [Tool("baemin_search",
                 "배민 등록 주소 기준으로 검색어의 배달 가게를 차례로 열어 메뉴 구성·가격, 리뷰 이벤트 안내, 홀 영업(편의시설)·공유주방 신호를 "
                 "한 번에 모은다. 모델 호출 없이 1~3분 걸린다. 음식·가게 찾기는 이 도구를 먼저 쓰고 android 조작으로 같은 가게를 다시 열지 않는다. "
                 "주문·장바구니는 하지 않는다.", schema, run)]
