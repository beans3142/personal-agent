from agent.baemin import BaeminSearch, classify, parse_info, parse_results, parse_store, build_baemin_tools


def node(value, y=300, x=160, clickable=False, cls="android.widget.TextView", desc=False):
    return {"text": "" if desc else value, "description": value if desc else "", "bounds": [x, y, x + 200, y + 30],
            "clickable": clickable, "class": cls, "resource": "", "package": "com.sampleapp"}


RESULTS = [node(v, 200 + i * 20, desc=(v in {"잇빠이스시", "싱싱회직거래수산, 배달팁 할인"})) for i, v in enumerate([
    "배달", "기본순",
    "육회란연어다 덕양화정점", "연어초밥, 스테이크 육회초밥", "5.0", "(567)", "최소주문 12,900원", "알뜰배달", "약 39분", "4,100원", "2.7km",
    "배민클럽 가입 시 배달팁 0원", "픽업가능", "예약가능", "광고", "연어초밥, 8,900원",
    "잇빠이스시", "스시 (미) 16p, 스시 (진) 22p", "4.9", "(302)", "최소주문 17,000원", "알뜰배달", "약 30분", "2,700원", "1.8km",
    "픽업가능", "스시 (미) 16p, 17,000원",
    "싱싱회직거래수산, 배달팁 할인", "스페셜초밥, 모듬초밥", "5.0", "(1,180)", "최소주문 20,000원", "알뜰배달", "약 35분", "900원", "866m",
])]


def test_results_are_split_into_stores_with_list_facts():
    stores = parse_results(RESULTS)
    assert [s["name"] for s in stores] == ["육회란연어다 덕양화정점", "잇빠이스시", "싱싱회직거래수산"]
    first, second, third = stores
    assert first["ad"] and first["rating"] == "5.0" and first["reviews"] == "567"
    assert first["min_order"] == "12,900원" and first["eta"] == "약 39분" and first["tip"] == "4,100원"
    assert first["distance"] == "2.7km" and first["pickup"]
    assert not second["ad"] and second["tip"] == "2,700원"
    assert third["distance"] == "866m" and not third["pickup"]
    assert "tap" in second and second["tap"][1] > 0


STORE = [node(v) for v in [
    "최소주문", "17,000원",
    "주문 안내\n\n1. 리뷰 이벤트\n주문 시 요청사항에 <리뷰 이벤트 참여>를 작성해 주시면 초밥 1P를 함께 보내드립니다.\n맛있게 드셨다면 가게찜과 별점, 리뷰 부탁드립니다.",
    "최근 주문 많은 조합", "추천 조합", "스시 (미) 16p, 날치알군함", "18,800원", "최소주문금액 달성",
    "초밥으로 검색한 메뉴", "인기 1위", "사장님 추천", "스시 (미) 16p", "광어2,연어2, 연어타다끼2, 문어1", ",17,000원", "리뷰 112",
    "장바구니 담기, 세부 옵션 선택을 위해 메뉴 상세로 이동합니다.",
    "인기 2위", "스시 (진) 22p", "광어2, 광어백김치1, 연어2", ",24,000원", "리뷰 58",
    "장바구니 담기, 세부 옵션 선택을 위해 메뉴 상세로 이동합니다.",
    "생연어 초밥", "7p,11,000원", "14p,21,000원", "리뷰 15",
    "장바구니 담기, 세부 옵션 선택을 위해 메뉴 상세로 이동합니다.",
    "사장님 추천", "패밀리 36p", "모듬 36개", ",36,500원",
    "장바구니 담기, 세부 옵션 선택을 위해 메뉴 상세로 이동합니다.",
]]


def test_store_menus_keep_composition_and_every_price_option():
    store = parse_store(STORE)
    menus = {m["name"]: m for m in store["menus"]}
    assert set(menus) == {"스시 (미) 16p", "스시 (진) 22p", "생연어 초밥", "패밀리 36p"}
    assert menus["스시 (미) 16p"]["items"] == "광어2,연어2, 연어타다끼2, 문어1"
    assert menus["스시 (미) 16p"]["prices"] == [("", 17000)]
    assert menus["생연어 초밥"]["prices"] == [("7p", 11000), ("14p", 21000)]
    assert menus["생연어 초밥"]["items"] == ""


def test_review_event_text_is_extracted_compactly():
    event = parse_store(STORE)["review_event"]
    assert "리뷰 이벤트 참여" in event and "초밥 1P" in event
    assert parse_store([node("최소주문")])["review_event"] is None


INFO = [node(v) for v in ["잇빠이스시", "상호명", "잇빠이스시", "주소", "경기도 고양시 덕양구 화정동 948 1층 113호",
                           "운영시간", "평일 - 오전 10:50 ~ 오후 10:50", "편의시설", "좌식, 무선 인터넷", "가게 소개"]]


def test_info_labels_map_to_their_values():
    info = parse_info(INFO)
    assert info["주소"].endswith("1층 113호") and info["편의시설"] == "좌식, 무선 인터넷"


def test_hall_and_shared_kitchen_classification():
    assert classify({"편의시설": "좌식, 무선 인터넷", "주소": "화정동 1층"}, "잇빠이스시") == {"hall": True, "shared_kitchen": False}
    assert classify({"주소": "OO동 공유주방 3호"}, "스시")["shared_kitchen"] is True
    assert classify({"주소": "OO동 2층"}, "배달전문 초밥")["shared_kitchen"] is True
    assert classify({}, "스시") == {"hall": None, "shared_kitchen": None}


class FakeDevice:
    """Tiny screen state machine: results list, one store page, its info page."""
    def __init__(self):
        self.state = "home"
        self.calls = []

    def xml(self, values):
        rows = []
        for v, y, desc, cls in values:
            attr = f'text="" content-desc="{v}"' if desc else f'text="{v}" content-desc=""'
            rows.append(f'<node {attr} class="{cls}" package="com.sampleapp" clickable="false" bounds="[160,{y}][700,{y + 30}]"/>')
        return "<hierarchy>" + "".join(rows) + "</hierarchy>"

    def dump_hierarchy(self):
        tv = "android.widget.TextView"
        if self.state == "home":
            return self.xml([("검색 버튼, 샌드위치? 샐러드?", 100, True, "android.view.View")])
        if self.state == "search":
            return self.xml([("", 40, False, "android.widget.EditText")])
        if self.state == "typed":
            return self.xml([("초밥", 40, False, "android.widget.EditText"), ("배달 44", 150, False, tv)])
        if self.state == "results":
            return self.xml([(n["text"] or n["description"], n["bounds"][1], bool(n["description"]), tv) for n in RESULTS])
        if self.state == "store":
            return self.xml([("배달", 787, False, tv)] + [(n["text"].replace("\n", "&#10;").replace("<", "&lt;").replace(">", "&gt;"), 800 + i * 20, False, tv) for i, n in enumerate(STORE)])
        if self.state == "info":
            return self.xml([(n["text"], 100 + i * 20, False, tv) for i, n in enumerate(INFO)])
        return "<hierarchy/>"

    def click(self, x, y):
        self.calls.append(("click", x, y))
        if self.state == "home":
            self.state = "search"
        elif self.state == "typed" and y < 190:
            self.state = "results"
        elif self.state == "results":
            self.state = "store"
        elif self.state == "store" and x == 831:
            self.state = "info"

    def set_text(self, target, value):
        self.state = "typed"

    def key(self, key):
        self.calls.append(("key", key))
        if key == "back":
            self.state = {"info": "store", "store": "results"}.get(self.state, self.state)

    def swipe(self, direction, scale=0.7):
        self.calls.append(("swipe", direction))

    def open(self, app, store=False):
        self.state = "home"
        return {"opened": "app"}

    def stop(self, package):
        self.calls.append(("stop", package))


def test_search_visits_each_store_once_and_filters_by_price():
    device = FakeDevice()
    result = BaeminSearch(device, sleep=lambda s: None).search("초밥", max_price=25000, count=10)
    names = [s["name"] for s in result["stores"]]
    assert names == ["육회란연어다 덕양화정점", "잇빠이스시", "싱싱회직거래수산"]
    store = result["stores"][1]
    assert {m["name"] for m in store["menus"]} == {"스시 (미) 16p", "스시 (진) 22p", "생연어 초밥"}
    assert store["hall"] is True and store["shared_kitchen"] is False
    assert "리뷰 이벤트 참여" in store["review_event"]
    assert result["checked"] == 3 and not result["partial"]


def test_search_stops_at_deadline_and_says_partial():
    device = FakeDevice()
    now = [0.0]
    def sleep(seconds):
        now[0] += 40
    result = BaeminSearch(device, sleep=sleep, clock=lambda: now[0], budget=60).search("초밥", count=10)
    assert result["partial"] is True
    assert result["checked"] < 3


def test_tool_is_registered_with_bounded_arguments():
    tool = build_baemin_tools(FakeDevice())[0]
    assert tool.name == "baemin_search"
    props = tool.parameters["properties"]
    assert props["count"]["maximum"] == 15 and "query" in tool.parameters["required"]


def test_duplicate_menus_from_overlapping_scrolls_are_merged():
    store = parse_store(STORE + STORE)
    assert len(store["menus"]) == 4


def test_customer_reviews_and_greetings_are_not_review_events():
    values = [node("별점 5점, 너무 맛있었어요! 리뷰 이벤트 최고"), node("감사드리며, 행복한 하루 보내시길 바랍니다!")]
    assert parse_store(values)["review_event"] is None
    event = parse_store([node("안녕하세요 초밥24입니다!\n★ 초밥 2ps 리뷰 이벤트 상시 진행\n요청사항에 리뷰 이벤트 적어주세요")])["review_event"]
    assert "리뷰 이벤트" in event and "안녕하세요" not in event


def test_explanatory_sentences_are_not_menu_names():
    nodes = [node(v) for v in ["한 달간 주문수가 많고 만족도가 높은 메뉴예요.", "모듬초밥 12p", ",18,000원",
                               "장바구니 담기, 세부 옵션 선택을 위해 메뉴 상세로 이동합니다."]]
    assert parse_store(nodes)["menus"][0]["name"] == "모듬초밥 12p"


def test_free_delivery_tip_is_read():
    nodes = [node(v, 200 + i * 20) for i, v in enumerate(["가게", "초밥", "4.9", "(10)", "최소주문 15,000원", "약 20분", "배달팁 0원", "1.2km"])]
    assert parse_results(nodes)[0]["tip"] == "0원"
