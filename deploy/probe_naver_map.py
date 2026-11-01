"""Opens Naver Map transit directions and saves a screenshot. Signs in to nothing."""

from playwright.sync_api import sync_playwright

START = "126.8343,37.6124,%ED%96%89%EC%8B%A0%EC%97%AD,,ADDRESS_POI"
GOAL = "127.0490,37.5045,%EC%84%A0%EB%A6%89%EC%97%AD,,ADDRESS_POI"
URL = f"https://map.naver.com/p/directions/{START}/{GOAL}/-/transit?c=12.00,0,0,0,dh"


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 1280, "height": 900}).new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(9000)
        print("URL", page.url[:150])
        print("TITLE", page.title()[:60])
        frames = [frame.url[:80] for frame in page.frames]
        print("FRAMES", frames[:6])
        for frame in page.frames:
            text = frame.locator("body").inner_text()[:900]
            if "분" in text:
                print("TEXT", frame.url[:60], text)
                break
        page.screenshot(path="/tmp/naver-route.png")
        browser.close()


if __name__ == "__main__":
    main()
