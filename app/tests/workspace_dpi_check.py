"""Check settled workspace geometry and animation cleanup at fractional DPI."""

from playwright.sync_api import expect, sync_playwright


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            headless=True,
        )
        for scale in (1, 1.25, 1.5, 2):
            page = browser.new_page(
                viewport={"width": 961, "height": 720}, device_scale_factor=scale
            )
            page.add_init_script(path="tests/fixtures/qq_browser_bridge.js")
            page.goto("http://127.0.0.1:5184/vue.html")
            page.wait_for_load_state("networkidle")
            for target in ("qq", "local", "qq"):
                page.locator(f"#workspace-{target}").click()
            page.wait_for_function("!document.querySelector('.workspace-track').getAnimations().length")
            expect(page.locator(".workspace-track")).to_have_css("transform", "none")
            expect(page.locator(".workspace-track")).to_have_css("will-change", "auto")
            assert abs(page.locator(".workspace-qq").bounding_box()["x"]) < 0.01
            page.set_viewport_size({"width": 769, "height": 720})
            page.wait_for_function("!document.querySelector('.workspace-track').getAnimations().length")
            assert abs(page.locator(".workspace-qq").bounding_box()["x"]) < 0.01
            page.keyboard.press("Escape")
            page.emulate_media(reduced_motion="reduce")
            page.locator("#workspace-local").click()
            expect(page.locator(".workspace-track")).to_have_css("transform", "none")
            assert abs(page.locator("#content").bounding_box()["x"]) < 0.01
            page.screenshot(path=f"../artifacts/ui/dpi-{scale}.png")
            page.close()
        browser.close()
    print("Workspace DPI checks passed: 100/125/150/200%, rapid reverse, resize, no settled transform, reduced motion")


if __name__ == "__main__":
    main()
