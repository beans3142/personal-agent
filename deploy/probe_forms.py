"""Prints the Naver time picker options. Signs in to nothing."""

from urllib.parse import quote

from playwright.sync_api import sync_playwright

START = f"126.8347679,37.6126875,{quote('행신역')},,ADDRESS_POI"
GOAL = f"127.0483487,37.5057908,{quote('선릉역')},,ADDRESS_POI"
PANEL_JS = """() => {
  const root = document.querySelector('button.timeset_btn_data').closest('div').parentElement.parentElement;
  return [...root.querySelectorAll('*')].filter(e => e.children.length === 0 && (e.innerText || '').trim())
    .map(e => e.tagName + '|' + String(e.className).slice(0, 26) + '|' + e.innerText.trim().slice(0, 14)).slice(0, 45);
}"""


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 1280, "height": 900}).new_page()
        page.goto(f"https://map.naver.com/p/directions/{START}/{GOAL}/-/transit", wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(8000)
        page.locator("button.btn_time_option").first.click()
        page.wait_for_timeout(800)
        page.locator("button.timeset_btn_data").nth(0).click()
        page.wait_for_timeout(800)
        print("DATE", page.evaluate(PANEL_JS))
        page.keyboard.press("Escape")
        page.locator("button.timeset_btn_data").nth(1).click()
        page.wait_for_timeout(800)
        print("HOUR", page.evaluate(PANEL_JS)[:25])
        page.screenshot(path="/tmp/naver-route.png")
        browser.close()


if __name__ == "__main__":
    main()
