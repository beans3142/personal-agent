"""Creates the Kakao developer app with the saved Kakao session. Prints no key values."""

import sys

from playwright.sync_api import sync_playwright

APP_NAME = "personal-agent"
COMPANY = "개인"
DOMAIN = "https://github.com/beans3142/personal-agent"
CONSOLE = "https://developers.kakao.com/console/app"
CATEGORY_WORDS = ("유틸", "생산성", "도구", "기타")


def choose_category(page) -> str:
    page.get_by_text("앱 카테고리 정보", exact=True).first.click()
    page.wait_for_timeout(1000)
    options = page.locator("[role=option], [role=listbox] li, li[role=menuitem]")
    labels = [text.strip() for text in options.all_inner_texts() if text.strip()]
    print("CATEGORIES", labels[:40])
    for word in CATEGORY_WORDS:
        for label in labels:
            if word in label:
                options.filter(has_text=label).first.click()
                return label
    raise RuntimeError("no category matched")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(storage_state="data/sessions/kakao.json", locale="ko-KR", viewport={"width": 1280, "height": 1100})
        page = context.new_page()
        page.goto(CONSOLE, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        if page.get_by_text(APP_NAME, exact=True).count():
            print("app already exists")
        else:
            page.get_by_role("button", name="앱 생성").first.click()
            page.wait_for_timeout(2000)
            page.locator("input[name='name']").fill(APP_NAME)
            page.locator("input[name='company']").fill(COMPANY)
            page.locator("input[name='representative_domain']").fill(DOMAIN)
            print("chosen", choose_category(page))
            page.wait_for_timeout(500)
            page.locator("label:has-text('운영정책을 위반하지')").first.click()
            page.get_by_role("button", name="저장").first.click()
            page.wait_for_timeout(4000)
            print("after save", page.url[:90])
        page.screenshot(path="/tmp/kakao-form.png", full_page=True)
        print(page.locator("body").inner_text()[:300].replace("\n", " / "))
        context.storage_state(path="data/sessions/kakao.json")
        browser.close()


if __name__ == "__main__":
    main()
