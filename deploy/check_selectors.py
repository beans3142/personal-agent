"""Opens each login page and reports whether its form fields exist. Enters and submits nothing."""

from playwright.sync_api import sync_playwright

from agent.authgate import SITES


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for site in SITES.values():
            page = browser.new_context(locale="ko-KR", timezone_id="Asia/Seoul").new_page()
            page.goto(site.login_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            counts = {
                name: page.locator(selector).count()
                for name, selector in (
                    ("user", site.user_selector),
                    ("password", site.password_selector),
                    ("submit", site.submit_selector),
                    ("captcha", site.captcha_selector),
                )
            }
            print(site.name, page.url[:70], page.title()[:40], counts)
        browser.close()


if __name__ == "__main__":
    main()
