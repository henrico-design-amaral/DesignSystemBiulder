from playwright.sync_api import sync_playwright

def capture_reality(url):
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page()
        page.goto(url, wait_until='networkidle')

        dom=page.content()
        screenshot=len(page.screenshot(full_page=True))

        browser.close()

        return {
            "dom":dom,
            "screenshot_size":screenshot
        }
