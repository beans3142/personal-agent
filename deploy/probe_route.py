"""Prints what public route pages show for a sample trip. Signs in to nothing."""

from urllib.parse import quote

from playwright.sync_api import sync_playwright

ORIGIN, DEST = "행신역", "선릉역"
GOOGLE = f"https://www.google.com/maps/dir/?api=1&origin={quote(ORIGIN)}&destination={quote(DEST)}&travelmode=transit&hl=ko"


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul").new_page()
        page.goto(GOOGLE, wait_until="domcontentloaded", timeout=40000)
        page.wait_for_timeout(7000)
        print("URL", page.url[:120])
        print("TITLE", page.title()[:80])
        print(page.locator("body").inner_text()[:1500])
        login = browser.new_context(locale="ko-KR").new_page()
        login.goto("https://nid.naver.com/nidlogin.login", wait_until="domcontentloaded", timeout=30000)
        login.wait_for_timeout(2000)
        print("NAVER BUTTONS", login.eval_on_selector_all(
            "button, input[type=submit]", "els => els.map(e => [e.tagName, e.id, e.type, e.className].join(':'))"))
        browser.close()


if __name__ == "__main__":
    main()
