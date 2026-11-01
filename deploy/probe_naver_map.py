"""Shows the Naver departure time picker. Signs in to nothing."""

from urllib.parse import quote

from playwright.sync_api import sync_playwright

START = f"126.8347679,37.6126875,{quote('행신역')},,ADDRESS_POI"
GOAL = f"127.0483487,37.5057908,{quote('선릉역')},,ADDRESS_POI"


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 1280, "height": 900}).new_page()
        page.goto(f"https://map.naver.com/p/directions/{START}/{GOAL}/-/transit", wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(8000)
        trigger = page.get_by_text("출발", exact=False).filter(has_text="오늘").last
        print("TRIGGER", trigger.evaluate("e => e.tagName + '|' + e.className + '|' + e.innerText")[:120])
        trigger.click()
        page.wait_for_timeout(1500)
        print("AFTER", page.evaluate("""() => [...document.querySelectorAll('button, select, a[role=button], [role=option], li button')]
            .filter(e => e.offsetParent && /[0-9]|오전|오후|내일|월|일|확인|출발/.test(e.innerText || ''))
            .map(e => e.tagName + '|' + e.className.slice(0, 30) + '|' + (e.innerText || '').replace(/\\s+/g, ' ').slice(0, 24)).slice(0, 70)"""))
        page.screenshot(path="/tmp/naver-route.png")
        browser.close()


if __name__ == "__main__":
    main()
