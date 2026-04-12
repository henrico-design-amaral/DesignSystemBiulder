from playwright.sync_api import sync_playwright

def capture_site(url):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle")

        title = page.title()
        screenshot = page.screenshot(full_page=True)

        layout = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('*')).map(el => {
                const r = el.getBoundingClientRect()
                return {
                    tag: el.tagName,
                    text: (el.innerText || '').slice(0, 30),
                    x: r.x,
                    y: r.y,
                    w: r.width,
                    h: r.height
                }
            })
        }""")

        browser.close()

        return {
            "title": title,
            "layout": layout,
            "screenshot_size": len(screenshot)
        }
